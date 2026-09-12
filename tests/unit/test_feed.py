from app.worker.feed import FeedEntry, find_new_entries


def entry(guid):
    return FeedEntry(guid=guid, title=f"Title {guid}", link=f"https://x/{guid}", body_html="<p>body</p>")


def test_cold_start_with_no_cursor_backfills_newest_only():
    entries = [entry("3"), entry("2"), entry("1")]  # newest-first
    new_entries, is_backfill = find_new_entries(entries, last_entry_id=None)
    assert is_backfill is True
    assert [e.guid for e in new_entries] == ["3"]


def test_cursor_not_found_backfills_newest_only():
    entries = [entry("3"), entry("2"), entry("1")]
    new_entries, is_backfill = find_new_entries(entries, last_entry_id="99")
    assert is_backfill is True
    assert [e.guid for e in new_entries] == ["3"]


def test_cursor_found_returns_newer_entries_oldest_first():
    entries = [entry("5"), entry("4"), entry("3"), entry("2"), entry("1")]
    new_entries, is_backfill = find_new_entries(entries, last_entry_id="3")
    assert is_backfill is False
    assert [e.guid for e in new_entries] == ["4", "5"]


def test_cursor_is_newest_returns_nothing_new():
    entries = [entry("3"), entry("2"), entry("1")]
    new_entries, is_backfill = find_new_entries(entries, last_entry_id="3")
    assert is_backfill is False
    assert new_entries == []


def test_empty_feed_with_no_cursor_backfills_nothing():
    new_entries, is_backfill = find_new_entries([], last_entry_id=None)
    assert is_backfill is True
    assert new_entries == []
