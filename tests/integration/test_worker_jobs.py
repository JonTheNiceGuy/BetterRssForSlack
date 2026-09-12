import datetime as dt

from app.models.user_cache import UserCache
from app.models.slack_channel_cache import SlackChannelCache
from app.models.rss_watch import RssWatch
from app.models.enums import Template, Visibility
from app.worker.feed import FeedEntry
from app.worker.jobs import check_watch


class FakeSlack:
    def __init__(self):
        self.posts = []

    def post_message(self, channel, text, thread_ts=None):
        self.posts.append({"channel": channel, "text": text, "thread_ts": thread_ts})
        return {"ok": True, "ts": f"ts-{len(self.posts)}"}


def seed_watch(db_session, **overrides):
    now = dt.datetime.now(dt.timezone.utc)
    db_session.add(UserCache(id="owner", display_name="O", email="o@x.com", last_refreshed_at=now))
    db_session.add(SlackChannelCache(id="C1", name="general", last_refreshed_at=now))
    db_session.commit()  # ensure FK targets exist before inserting rss_watch (no relationship() defined to order this automatically)
    defaults = dict(
        feed_url="https://example.com/feed.xml",
        slack_channel_id="C1",
        template=Template.HEADLINE_LINK,
        visibility=Visibility.OWNER_ONLY,
        created_by_sub="owner",
        created_at=now,
        check_interval_seconds=3600,
        is_active=True,
        consecutive_failure_count=0,
        auto_pause_after_failures=0,
        last_entry_id=None,
    )
    defaults.update(overrides)
    watch = RssWatch(**defaults)
    db_session.add(watch)
    db_session.commit()
    return watch


def entries(*guids):
    return [
        FeedEntry(guid=g, title=f"T{g}", link=f"https://x/{g}", body_html="<p>b</p>")
        for g in guids
    ]


def test_success_posts_new_entries_and_advances_cursor(db_session):
    watch = seed_watch(db_session, last_entry_id="1")
    slack = FakeSlack()
    fetch = lambda url: entries("3", "2", "1")  # newest-first

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.last_entry_id == "3"
    assert refreshed.last_error is None
    assert refreshed.consecutive_failure_count == 0
    assert refreshed.last_posted_at is not None
    assert len(slack.posts) == 2  # entries "2" then "3", oldest-of-new-batch first


def test_thread_template_posts_root_then_threaded_reply(db_session):
    watch = seed_watch(db_session, template=Template.HEADLINE_LINK_THREAD, last_entry_id="1")
    slack = FakeSlack()
    fetch = lambda url: entries("2", "1")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)

    assert len(slack.posts) == 2
    assert slack.posts[0]["thread_ts"] is None
    root_ts = "ts-1"
    assert slack.posts[1]["thread_ts"] == root_ts


def test_fetch_failure_increments_failure_count_and_sets_error(db_session):
    watch = seed_watch(db_session)
    slack = FakeSlack()

    def fetch(url):
        raise ValueError("boom")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.consecutive_failure_count == 1
    assert "boom" in refreshed.last_error
    assert refreshed.is_active is True  # threshold is 0 -> never auto-pause


def test_failure_reaching_threshold_auto_pauses_and_notifies(db_session):
    watch = seed_watch(db_session, auto_pause_after_failures=2, consecutive_failure_count=1)
    slack = FakeSlack()

    def fetch(url):
        raise ValueError("boom")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.consecutive_failure_count == 2
    assert refreshed.is_active is False
    assert len(slack.posts) == 1
    assert "Auto-paused" in slack.posts[0]["text"]


def test_cold_start_backfills_single_newest_entry(db_session):
    watch = seed_watch(db_session, last_entry_id=None)
    slack = FakeSlack()
    fetch = lambda url: entries("3", "2", "1")

    check_watch(db_session, watch.id, fetch_entries=fetch, slack_client=slack, truncate_chars=500)
    db_session.commit()

    refreshed = db_session.get(RssWatch, watch.id)
    assert refreshed.last_entry_id == "3"
    assert len(slack.posts) == 1
    assert slack.posts[0]["text"].startswith("[Backfill]")
