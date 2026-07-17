from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()


def database_engine_options(database_url: str) -> dict:
    is_sqlite = database_url.startswith("sqlite")
    options: dict = {
        "pool_pre_ping": True,
        "connect_args": {"check_same_thread": False} if is_sqlite else {},
    }
    if database_url in {"sqlite://", "sqlite:///:memory:"}:
        options["poolclass"] = StaticPool
    elif not is_sqlite:
        # Authentication reads happen before business row locks. READ COMMITTED
        # lets a lock waiter observe the winner's commit in later reads.
        options["isolation_level"] = "READ COMMITTED"
    return options


engine_options = database_engine_options(settings.database_url)
engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
