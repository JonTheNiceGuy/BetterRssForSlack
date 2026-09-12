import datetime as dt

from app.slack.channel_cache import refresh_channel, list_bot_channels


class FakeSlackClient:
    def __init__(self):
        self.info_calls = []
        self.channels = {"C1": "general", "C2": "announcements"}

    def conversations_info(self, channel):
        self.info_calls.append(channel)
        return {"channel": {"id": channel, "name": self.channels[channel]}}

    def conversations_list(self, **kwargs):
        return {
            "channels": [
                {"id": cid, "name": name} for cid, name in self.channels.items()
            ]
        }


def test_refresh_channel_creates_row_when_missing(db_session):
    client = FakeSlackClient()
    row = refresh_channel(db_session, client, "C1")
    db_session.commit()
    assert row.id == "C1"
    assert row.name == "general"
    assert client.info_calls == ["C1"]


def test_refresh_channel_skips_call_when_fresh(db_session):
    client = FakeSlackClient()
    refresh_channel(db_session, client, "C1")
    db_session.commit()
    refresh_channel(db_session, client, "C1")
    assert client.info_calls == ["C1"]  # second call skipped, still fresh


def test_refresh_channel_refetches_when_stale(db_session):
    client = FakeSlackClient()
    row = refresh_channel(db_session, client, "C1")
    db_session.commit()
    row.last_refreshed_at = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
    db_session.commit()

    refresh_channel(db_session, client, "C1", max_age_seconds=3600)
    assert client.info_calls == ["C1", "C1"]


def test_list_bot_channels_returns_id_and_name():
    client = FakeSlackClient()
    channels = list_bot_channels(client)
    assert {"id": "C1", "name": "general"} in channels
    assert {"id": "C2", "name": "announcements"} in channels
