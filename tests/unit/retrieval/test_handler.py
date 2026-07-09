import json
from unittest.mock import MagicMock

import pytest

from tests.conftest import load_handler


@pytest.fixture
def handler_module(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-123")
    monkeypatch.setenv("GEMINI_MODEL_ID", "gemini-2.5-flash")
    monkeypatch.setenv(
        "GEMINI_API_KEY_SECRET_ARN",
        "arn:aws:secretsmanager:us-east-1:123456789012:secret:asklore-dev/gemini-api-key-abc123",
    )
    module = load_handler("retrieval")
    module.bedrock_agent_runtime = MagicMock()
    module.secretsmanager = MagicMock()
    module._gemini_client = MagicMock()
    return module


def _retrieval_result(text: str, uri: str) -> dict:
    return {"content": {"text": text}, "location": {"s3Location": {"uri": uri}}}


def test_retrieve_returns_retrieval_results(handler_module):
    result = _retrieval_result(
        "Rotate the cert by...", "s3://asklore-raw-1-us-east-1/infra-runbooks/ssl-cert-rotation.md"
    )
    handler_module.bedrock_agent_runtime.retrieve.return_value = {"retrievalResults": [result]}

    results = handler_module.retrieve("How do I rotate an SSL cert?")

    assert results == [result]
    handler_module.bedrock_agent_runtime.retrieve.assert_called_once_with(
        knowledgeBaseId="kb-123",
        retrievalQuery={"text": "How do I rotate an SSL cert?"},
        retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 5}},
    )


def test_build_sources_dedups_repeated_uris(handler_module):
    uri = "s3://asklore-raw-1-us-east-1/infra-runbooks/on-call.md"
    results = [_retrieval_result("a", uri), _retrieval_result("b", uri)]

    sources = handler_module.build_sources(results)

    assert sources == [{"doc_title": "on-call.md", "source_key": uri}]


def test_build_sources_maps_doc_title_from_uri(handler_module):
    uri = "s3://asklore-raw-1-us-east-1/infra-runbooks/database-failover.md"

    sources = handler_module.build_sources([_retrieval_result("failover steps", uri)])

    assert sources == [{"doc_title": "database-failover.md", "source_key": uri}]


def test_generate_answer_returns_gemini_text(handler_module):
    handler_module._gemini_client.models.generate_content.return_value = MagicMock(
        text="Rotate the cert by..."
    )

    answer = handler_module.generate_answer(
        "How do I rotate an SSL cert?",
        [_retrieval_result("cert rotation steps...", "s3://bucket/ssl-cert-rotation.md")],
    )

    assert answer == "Rotate the cert by..."
    call_kwargs = handler_module._gemini_client.models.generate_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-2.5-flash"
    assert "cert rotation steps..." in call_kwargs["contents"]
    assert "How do I rotate an SSL cert?" in call_kwargs["contents"]


def test_generate_answer_handles_none_text(handler_module):
    handler_module._gemini_client.models.generate_content.return_value = MagicMock(text=None)

    answer = handler_module.generate_answer("anything", [])

    assert answer == ""


def test_handler_returns_400_for_missing_query(handler_module):
    result = handler_module.handler({"body": json.dumps({})}, None)

    assert result["statusCode"] == 400


def test_handler_returns_answer_and_sources(handler_module):
    uri = "s3://asklore-raw-1-us-east-1/infra-runbooks/database-failover.md"
    handler_module.bedrock_agent_runtime.retrieve.return_value = {
        "retrievalResults": [_retrieval_result("failover steps", uri)]
    }
    handler_module._gemini_client.models.generate_content.return_value = MagicMock(
        text="Restart via the runbook."
    )

    result = handler_module.handler(
        {"body": json.dumps({"query": "How do I fail over the database?"})}, None
    )

    assert result["statusCode"] == 200
    payload = json.loads(result["body"])
    assert payload["answer"] == "Restart via the runbook."
    assert payload["sources"][0]["doc_title"] == "database-failover.md"


def test_handler_returns_500_on_unhandled_error(handler_module):
    handler_module.bedrock_agent_runtime.retrieve.side_effect = RuntimeError("boom")

    result = handler_module.handler({"body": json.dumps({"query": "anything"})}, None)

    assert result["statusCode"] == 500
