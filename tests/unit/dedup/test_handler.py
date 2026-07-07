import hashlib
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from tests.conftest import load_handler


def _conditional_check_failed() -> ClientError:
    return ClientError(
        {"Error": {"Code": "ConditionalCheckFailedException", "Message": "hash exists"}},
        "PutItem",
    )


@pytest.fixture
def handler_module(monkeypatch):
    monkeypatch.setenv("FILE_HASHES_TABLE", "asklore-file-hashes")
    monkeypatch.setenv("CLEAN_BUCKET", "asklore-raw-123456789-us-east-1")
    module = load_handler("dedup")
    module.s3 = MagicMock()
    module.dynamodb = MagicMock()
    return module


def _get_object_response(content: bytes) -> dict:
    return {"Body": MagicMock(read=MagicMock(return_value=content))}


def _s3_event(bucket: str, key: str) -> dict:
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def test_new_content_is_copied_and_deduped(handler_module):
    content = b"# SSL cert rotation runbook"
    handler_module.s3.get_object.return_value = _get_object_response(content)

    result = handler_module.handler(
        _s3_event("asklore-raw-uploads-123456789-us-east-1", "infra-runbooks/ssl-cert-rotation.md"), None
    )

    assert result == {"statusCode": 200}
    handler_module.s3.copy_object.assert_called_once_with(
        Bucket="asklore-raw-123456789-us-east-1",
        Key="infra-runbooks/ssl-cert-rotation.md",
        CopySource={"Bucket": "asklore-raw-uploads-123456789-us-east-1", "Key": "infra-runbooks/ssl-cert-rotation.md"},
    )
    handler_module.s3.delete_object.assert_called_once_with(
        Bucket="asklore-raw-uploads-123456789-us-east-1", Key="infra-runbooks/ssl-cert-rotation.md"
    )

    put_item_kwargs = handler_module.dynamodb.put_item.call_args.kwargs
    assert put_item_kwargs["TableName"] == "asklore-file-hashes"
    item = put_item_kwargs["Item"]
    assert item["file_hash"]["S"] == hashlib.sha256(content).hexdigest()
    assert item["domain"]["S"] == "infra-runbooks"
    assert item["filename"]["S"] == "ssl-cert-rotation.md"


def test_duplicate_content_is_deleted_not_copied(handler_module):
    handler_module.s3.get_object.return_value = _get_object_response(b"duplicate content")
    handler_module.dynamodb.put_item.side_effect = _conditional_check_failed()

    result = handler_module.handler(
        _s3_event("asklore-raw-uploads-123456789-us-east-1", "infra-runbooks/duplicate.md"), None
    )

    assert result == {"statusCode": 200}
    handler_module.s3.copy_object.assert_not_called()
    handler_module.s3.delete_object.assert_called_once_with(
        Bucket="asklore-raw-uploads-123456789-us-east-1", Key="infra-runbooks/duplicate.md"
    )


def test_metadata_sidecar_passes_through_unhashed(handler_module):
    result = handler_module.handler(
        _s3_event(
            "asklore-raw-uploads-123456789-us-east-1",
            "infra-runbooks/ssl-cert-rotation.md.metadata.json",
        ),
        None,
    )

    assert result == {"statusCode": 200}
    handler_module.s3.get_object.assert_not_called()
    handler_module.dynamodb.put_item.assert_not_called()
    handler_module.s3.copy_object.assert_called_once_with(
        Bucket="asklore-raw-123456789-us-east-1",
        Key="infra-runbooks/ssl-cert-rotation.md.metadata.json",
        CopySource={
            "Bucket": "asklore-raw-uploads-123456789-us-east-1",
            "Key": "infra-runbooks/ssl-cert-rotation.md.metadata.json",
        },
    )
    handler_module.s3.delete_object.assert_called_once()


def test_bad_record_does_not_block_other_records(handler_module):
    handler_module.s3.get_object.side_effect = [
        Exception("boom"),
        _get_object_response(b"good content"),
    ]

    event = {
        "Records": [
            {
                "s3": {
                    "bucket": {"name": "asklore-raw-uploads-123456789-us-east-1"},
                    "object": {"key": "infra-runbooks/broken.md"},
                }
            },
            {
                "s3": {
                    "bucket": {"name": "asklore-raw-uploads-123456789-us-east-1"},
                    "object": {"key": "infra-runbooks/ok.md"},
                }
            },
        ]
    }

    result = handler_module.handler(event, None)

    assert result == {"statusCode": 200}
    handler_module.s3.copy_object.assert_called_once_with(
        Bucket="asklore-raw-123456789-us-east-1",
        Key="infra-runbooks/ok.md",
        CopySource={"Bucket": "asklore-raw-uploads-123456789-us-east-1", "Key": "infra-runbooks/ok.md"},
    )


def test_non_conditional_dynamodb_error_is_not_treated_as_duplicate(handler_module):
    handler_module.s3.get_object.return_value = _get_object_response(b"content")
    handler_module.dynamodb.put_item.side_effect = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "too fast"}},
        "PutItem",
    )

    result = handler_module.handler(
        _s3_event("asklore-raw-uploads-123456789-us-east-1", "infra-runbooks/on-call.md"), None
    )

    assert result == {"statusCode": 200}
    handler_module.s3.copy_object.assert_not_called()
    handler_module.s3.delete_object.assert_not_called()
