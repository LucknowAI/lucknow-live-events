from __future__ import annotations

import hashlib
import os
from pathlib import Path

import structlog

from api.core.config import settings


log = structlog.get_logger(__name__)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalStorage:
    """Stores raw snapshots on the local filesystem."""

    def __init__(self, base: str) -> None:
        self._base = Path(base)
        self._base.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        path = self._base / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get(self, key: str) -> bytes | None:
        path = self._base / key
        if path.exists():
            return path.read_bytes()
        return None

    def exists(self, key: str) -> bool:
        return (self._base / key).exists()


class R2Storage:
    """Cloudflare R2 stub — swap in boto3/s3 client when STORAGE_TYPE=r2."""

    def __init__(self) -> None:
        import boto3  # type: ignore

        self._client = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        )
        self._bucket = settings.R2_BUCKET_NAME

    def put(self, key: str, data: bytes) -> str:
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes | None:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except Exception:
            return None

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
            return True
        except Exception:
            return False


def get_storage() -> LocalStorage | R2Storage:
    if settings.STORAGE_TYPE == "r2":
        return R2Storage()
    return LocalStorage(settings.LOCAL_STORAGE_PATH)


def snapshot_key(source_id: str, url_hash: str) -> str:
    return f"snapshots/{source_id}/{url_hash}.raw"


def content_hash(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return _sha256(data)
