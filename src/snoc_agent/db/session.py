"""Engine/session construction without global database clients."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from snoc_agent.db.base import Base

SessionFactory = sessionmaker[Session]


def create_engine_and_session(database_url: str) -> tuple[Engine, SessionFactory]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def create_schema(engine: Engine) -> None:
    # Import registers every model with the metadata.
    from snoc_agent.db import models  # noqa: F401

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: SessionFactory) -> Iterator[Session]:
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
