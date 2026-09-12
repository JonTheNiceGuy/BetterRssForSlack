from app.worker.feed import FeedEntry
from app.models.enums import Template
from app.slack.templates import render

LONG_BODY = "<p>" + ("word " * 200) + "</p>"


def entry(body_html="<p>Hello <b>world</b></p>"):
    return FeedEntry(guid="1", title="My Title", link="https://example.com/1", body_html=body_html)


def test_headline_link_has_no_thread():
    root, thread = render(Template.HEADLINE_LINK, entry(), truncate_chars=500)
    assert "My Title" in root
    assert "https://example.com/1" in root
    assert thread is None


def test_headline_link_truncated_strips_html_and_truncates():
    root, thread = render(Template.HEADLINE_LINK_TRUNCATED, entry(LONG_BODY), truncate_chars=20)
    assert "My Title" in root
    assert "<p>" not in root
    title_line, link_line, *body_lines = root.split("\n")
    body_part = "\n".join(body_lines)
    assert len(body_part) <= 23  # 20 chars + ellipsis
    assert thread is None


def test_headline_link_thread_splits_root_and_reply():
    root, thread = render(Template.HEADLINE_LINK_THREAD, entry(LONG_BODY), truncate_chars=20)
    assert "My Title" in root
    assert "https://example.com/1" in root
    assert "word" in thread
    assert len(thread) <= 23


def test_headline_link_full_includes_entire_body_untruncated():
    root, thread = render(Template.HEADLINE_LINK_FULL, entry(LONG_BODY), truncate_chars=20)
    assert "word" * 1 in root
    assert root.count("word") == 200
    assert thread is None


def test_backfill_prefixes_title():
    root, _ = render(Template.HEADLINE_LINK, entry(), truncate_chars=500, backfill=True)
    assert root.startswith("[Backfill] ")
