from unittest.mock import MagicMock, patch

import pytest
from opensearchpy.exceptions import AuthorizationException, RequestError

from tests.conftest import load_handler


@pytest.fixture
def handler_module(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_ENDPOINT", "https://example.us-east-1.aoss.amazonaws.com")
    monkeypatch.setenv("INDEX_NAME", "asklore-kb-index")
    module = load_handler("kb-index-setup")
    monkeypatch.setattr(module.time, "sleep", MagicMock())
    return module


def _cfn_event(request_type: str) -> dict:
    return {
        "RequestType": request_type,
        "ResponseURL": "https://example.com/cfn-response",
        "StackId": "stack-1",
        "RequestId": "req-1",
        "LogicalResourceId": "AossKbIndex",
    }


def test_ensure_index_creates_when_missing(handler_module):
    client = MagicMock()
    client.indices.exists.return_value = False

    handler_module.ensure_index(client)

    client.indices.create.assert_called_once()
    _, kwargs = client.indices.create.call_args
    body = kwargs["body"]
    assert body["mappings"]["properties"]["vector"]["dimension"] == 1024
    assert set(body["mappings"]["properties"]) == {"vector", "text", "metadata"}
    handler_module.time.sleep.assert_called_once_with(handler_module.INDEX_SETTLE_SECONDS)


def test_ensure_index_skips_when_present(handler_module):
    client = MagicMock()
    client.indices.exists.return_value = True

    handler_module.ensure_index(client)

    client.indices.create.assert_not_called()
    handler_module.time.sleep.assert_not_called()


def test_ensure_index_swallows_already_exists_race(handler_module):
    client = MagicMock()
    client.indices.exists.return_value = False
    client.indices.create.side_effect = RequestError(
        400, "resource_already_exists_exception", {}
    )

    handler_module.ensure_index(client)  # must not raise


def _authz_error() -> AuthorizationException:
    return AuthorizationException(403, "security_exception", {})


def test_ensure_index_retries_through_authorization_propagation_delay(handler_module):
    client = MagicMock()
    client.indices.exists.side_effect = [_authz_error(), _authz_error(), False]

    handler_module.ensure_index(client)

    assert client.indices.exists.call_count == 3
    client.indices.create.assert_called_once()


def test_ensure_index_raises_after_exhausting_authorization_retries(handler_module):
    client = MagicMock()
    client.indices.exists.side_effect = _authz_error()

    with pytest.raises(AuthorizationException):
        handler_module.ensure_index(client)

    assert client.indices.exists.call_count == handler_module.AUTHORIZATION_PROPAGATION_MAX_ATTEMPTS


@patch("urllib.request.urlopen")
def test_handler_sends_success_on_create(mock_urlopen, handler_module):
    with patch.object(handler_module, "get_os_client", return_value=MagicMock()), \
         patch.object(handler_module, "ensure_index") as mock_ensure_index:
        handler_module.handler(_cfn_event("Create"), MagicMock(log_stream_name="stream-1"))

    mock_ensure_index.assert_called_once()
    mock_urlopen.assert_called_once()


@patch("urllib.request.urlopen")
def test_handler_skips_ensure_index_on_delete(mock_urlopen, handler_module):
    with patch.object(handler_module, "ensure_index") as mock_ensure_index:
        handler_module.handler(_cfn_event("Delete"), MagicMock(log_stream_name="stream-1"))

    mock_ensure_index.assert_not_called()
    mock_urlopen.assert_called_once()


@patch("urllib.request.urlopen")
def test_handler_sends_failed_on_exception(mock_urlopen, handler_module):
    with patch.object(handler_module, "get_os_client", side_effect=RuntimeError("boom")):
        handler_module.handler(_cfn_event("Create"), MagicMock(log_stream_name="stream-1"))

    sent_body = mock_urlopen.call_args[0][0].data
    assert b'"FAILED"' in sent_body
