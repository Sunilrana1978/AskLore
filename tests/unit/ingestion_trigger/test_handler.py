from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.conftest import load_handler


def _conflict_error() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConflictException", "Message": "already running"}},
        "StartIngestionJob",
    )


@pytest.fixture
def handler_module(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-123")
    monkeypatch.setenv("DATA_SOURCE_ID", "ds-456")
    module = load_handler("ingestion-trigger")
    module.bedrock_agent = MagicMock()
    return module


def _s3_event(key: str) -> dict:
    return {"Records": [{"s3": {"object": {"key": key}}}]}


def test_starts_ingestion_job(handler_module):
    handler_module.handler(_s3_event("infra-runbooks/ssl-cert-rotation.md"), None)

    handler_module.bedrock_agent.start_ingestion_job.assert_called_once_with(
        knowledgeBaseId="kb-123",
        dataSourceId="ds-456",
    )


def test_conflict_exception_is_not_raised(handler_module):
    handler_module.bedrock_agent.start_ingestion_job.side_effect = _conflict_error()

    result = handler_module.handler(_s3_event("infra-runbooks/on-call.md"), None)

    assert result == {"statusCode": 200}


def test_other_client_errors_do_not_raise(handler_module):
    handler_module.bedrock_agent.start_ingestion_job.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "too fast"}},
        "StartIngestionJob",
    )

    result = handler_module.handler(_s3_event("infra-runbooks/on-call.md"), None)

    assert result == {"statusCode": 200}
