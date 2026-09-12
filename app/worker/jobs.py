import datetime as dt
from typing import Callable

from app.models.rss_watch import RssWatch
from app.models.enums import Template
from app.worker.feed import FeedEntry, find_new_entries
from app.slack.templates import render


def check_watch(
    session,
    watch_id: int,
    *,
    fetch_entries: Callable[[str], list[FeedEntry]],
    slack_client,
    truncate_chars: int,
) -> None:
    watch = session.get(RssWatch, watch_id)
    if watch is None:
        return

    now = dt.datetime.now(dt.timezone.utc)

    try:
        entries = fetch_entries(watch.feed_url)
    except Exception as exc:  # feed fetch/parse failure
        watch.last_error = str(exc)
        watch.last_checked_at = now
        watch.consecutive_failure_count += 1
        if (
            watch.auto_pause_after_failures > 0
            and watch.consecutive_failure_count >= watch.auto_pause_after_failures
        ):
            slack_client.post_message(
                watch.slack_channel_id,
                f"⚠️ Auto-paused after {watch.consecutive_failure_count} "
                f"consecutive failures. Last error: {watch.last_error}",
            )
            watch.is_active = False
        session.flush()
        return

    watch.last_error = None
    watch.consecutive_failure_count = 0

    new_entries, is_backfill = find_new_entries(entries, watch.last_entry_id)

    for entry in new_entries:
        root_text, thread_text = render(
            watch.template, entry, truncate_chars=truncate_chars, backfill=is_backfill
        )
        response = slack_client.post_message(watch.slack_channel_id, root_text)
        if watch.template == Template.HEADLINE_LINK_THREAD and thread_text is not None:
            slack_client.post_message(
                watch.slack_channel_id, thread_text, thread_ts=response["ts"]
            )

    if new_entries:
        watch.last_entry_id = new_entries[-1].guid
        watch.last_posted_at = now
    watch.last_checked_at = now
    session.flush()
