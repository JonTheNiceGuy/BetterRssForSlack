from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import inspect


def test_alembic_upgrade_head_creates_all_tables(postgres_container):
    alembic_cfg = AlembicConfig("alembic.ini")
    alembic_cfg.set_main_option(
        "sqlalchemy.url", postgres_container.get_connection_url()
    )
    command.upgrade(alembic_cfg, "head")

    from app.db import make_engine

    engine = make_engine(postgres_container.get_connection_url())
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) >= {
        "user_cache",
        "slack_channel_cache",
        "rss_watch",
        "api_token",
    }
