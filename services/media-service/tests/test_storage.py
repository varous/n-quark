"""Content-addressed local storage: dedup, idempotency, disable, size cap."""

from media_service import identity
from media_service.storage import ContentAddressedStore


def test_write_and_dedup(tmp_path):
    store = ContentAddressedStore(str(tmp_path), enabled=True, max_bytes=1000)
    data = b"hello-bytes"
    sha = identity.content_sha256(data)
    key1 = store.write(sha, data)
    assert key1 and store.exists(sha)
    # writing the same content again is idempotent (same key, no duplicate file content churn)
    mtime = (tmp_path / key1).stat().st_mtime_ns
    key2 = store.write(sha, data)
    assert key2 == key1 and (tmp_path / key1).stat().st_mtime_ns == mtime
    assert store.read(sha) == data


def test_disabled_store_returns_none(tmp_path):
    store = ContentAddressedStore(str(tmp_path), enabled=False, max_bytes=1000)
    sha = identity.content_sha256(b"x")
    assert store.write(sha, b"x") is None and not store.exists(sha)


def test_too_large_not_stored(tmp_path):
    store = ContentAddressedStore(str(tmp_path), enabled=True, max_bytes=4)
    sha = identity.content_sha256(b"toolong")
    assert store.write(sha, b"toolong") is None and not store.exists(sha)


def test_key_is_content_addressed(tmp_path):
    store = ContentAddressedStore(str(tmp_path))
    sha = "ab" + "c" * 62
    assert store.key_for(sha) == f"ab/cc/{sha}"
