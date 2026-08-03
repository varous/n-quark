"""Test harness: bind crawl-service to a throwaway SQLite DB before importing the app, and provide
a fresh schema per test. Test helpers (StubCapturer) live in tests/_stubs.py.
"""

import os
import tempfile

# Must be set before crawl_service.config / db are imported.
_TMP = tempfile.mkdtemp()
os.environ["NQUARK_POSTGRES_URL"] = f"sqlite:///{_TMP}/crawl_test.db"
os.environ["NQUARK_NETWORK_MODE"] = "local"

import pytest  # noqa: E402

from crawl_service.db import SessionLocal, engine  # noqa: E402
from crawl_service.models import Base  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture()
def session_factory():
    return SessionLocal
