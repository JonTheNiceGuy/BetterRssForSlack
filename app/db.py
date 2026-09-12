from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

Base = declarative_base()


def make_engine(database_url: str):
    return create_engine(database_url, future=True)


def make_session_factory(engine):
    return sessionmaker(bind=engine, future=True, expire_on_commit=False)
