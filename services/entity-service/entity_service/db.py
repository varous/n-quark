from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from entity_service.config import settings

connect_args: dict[str, object] = {}
if settings.postgres_url.startswith("sqlite"):
    connect_args["check_same_thread"] = False

engine = create_engine(
    settings.postgres_url,
    connect_args=connect_args,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
