import os
import struct

# Use an isolated SQLite database for tests (set before importing media_service.config).
os.environ.setdefault("NQUARK_POSTGRES_URL", "sqlite://")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from media_service.models import Base


@pytest.fixture()
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'media.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def png_bytes(width: int, height: int, tag: bytes = b"") -> bytes:
    """A header-valid PNG (enough for sniff + IHDR dimensions), plus optional trailing bytes so
    different `tag`s hash differently."""
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR"
            + struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00" + tag)


@pytest.fixture()
def png() -> bytes:
    return png_bytes(800, 600)
