import pytest
from testcontainers.community.postgres import PostgresContainer

from app.db import Base, make_engine, make_session_factory
import app.models  # noqa: F401 - registers tables on Base.metadata


@pytest.fixture(scope="session")
def postgres_container():
    with PostgresContainer("postgres:18", driver="psycopg") as pg:
        yield pg


@pytest.fixture()
def db_engine(postgres_container):
    engine = make_engine(postgres_container.get_connection_url())
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    factory = make_session_factory(db_engine)
    session = factory()
    yield session
    session.close()
