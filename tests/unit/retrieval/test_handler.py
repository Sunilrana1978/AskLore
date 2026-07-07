import json
from unittest.mock import MagicMock

import pytest

from tests.conftest import load_handler


@pytest.fixture
def handler_module(monkeypatch):
    monkeypatch.setenv("KNOWLEDGE_BASE_ID", "kb-123")
    monkeypatch.setenv(
        "GENERATION_MODEL_ARN",
        "arn:aws:bedrock:us-east-1::foundation-model/cohere.command-r-plus-v1:0",
    )
    module = load_handler("retrieval")
    module.bedrock_agent_runtime = MagicMock()
    return module


def _rag_response(answer: str, uris: list[str]) -> dict:
    return {
        "output": {"text": answer},
        "citations": [
            {
                "retrievedReferences": [
                    {"location": {"s3Location": {"uri": uri}}} for uri in uris
                ]
            }
        ],
    }


def test_retrieve_and_generate_maps_sources(handler_module):
    handler_module.bedrock_agent_runtime.retrieve_and_generate.return_value = _rag_response(
        "Rotate the cert by...",
        ["s3://asklore-raw-1-us-east-1/infra-runbooks/ssl-cert-rotation.md"],
    )

    answer, sources = handler_module.retrieve_and_generate("How do I rotate an SSL cert?")

    assert answer == "Rotate the cert by..."
    assert sources == [{
        "doc_title": "ssl-cert-rotation.md",
        "source_key": "s3://asklore-raw-1-us-east-1/infra-runbooks/ssl-cert-rotation.md",
    }]


def test_retrieve_and_generate_dedups_repeated_uris(handler_module):
    uri = "s3://asklore-raw-1-us-east-1/infra-runbooks/on-call.md"
    handler_module.bedrock_agent_runtime.retrieve_and_generate.return_value = _rag_response(
        "Escalate to...", [uri, uri]
    )

    _, sources = handler_module.retrieve_and_generate("How do I escalate?")

    assert len(sources) == 1


def test_handler_returns_400_for_missing_query(handler_module):
    result = handler_module.handler({"body": json.dumps({})}, None)

    assert result["statusCode"] == 400


def test_handler_returns_answer_and_sources(handler_module):
    handler_module.bedrock_agent_runtime.retrieve_and_generate.return_value = _rag_response(
        "Restart via the runbook.",
        ["s3://asklore-raw-1-us-east-1/infra-runbooks/database-failover.md"],
    )

    result = handler_module.handler(
        {"body": json.dumps({"query": "How do I fail over the database?"})}, None
    )

    assert result["statusCode"] == 200
    payload = json.loads(result["body"])
    assert payload["answer"] == "Restart via the runbook."
    assert payload["sources"][0]["doc_title"] == "database-failover.md"


def test_handler_returns_500_on_unhandled_error(handler_module):
    handler_module.bedrock_agent_runtime.retrieve_and_generate.side_effect = RuntimeError("boom")

    result = handler_module.handler({"body": json.dumps({"query": "anything"})}, None)

    assert result["statusCode"] == 500
