def test_all_tables_registered_on_metadata():
    import app.models  # noqa: F401 - side effect: registers tables
    from app.db import Base

    table_names = set(Base.metadata.tables.keys())
    assert table_names == {
        "user_cache",
        "slack_channel_cache",
        "rss_watch",
        "api_token",
    }
