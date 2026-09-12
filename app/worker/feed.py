from dataclasses import dataclass

import feedparser


@dataclass(frozen=True)
class FeedEntry:
    guid: str
    title: str
    link: str
    body_html: str


def parse_feed(feed_url: str) -> list[FeedEntry]:
    parsed = feedparser.parse(feed_url)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Failed to parse feed {feed_url}: {parsed.bozo_exception}")
    entries = []
    for e in parsed.entries:
        guid = e.get("id") or e.get("link") or e.get("title", "")
        body_html = ""
        if "content" in e and e["content"]:
            body_html = e["content"][0].get("value", "")
        elif "summary" in e:
            body_html = e.get("summary", "")
        entries.append(
            FeedEntry(guid=guid, title=e.get("title", ""), link=e.get("link", ""), body_html=body_html)
        )
    return entries


def find_new_entries(
    entries: list[FeedEntry], last_entry_id: str | None
) -> tuple[list[FeedEntry], bool]:
    if not entries:
        return [], last_entry_id is None

    if last_entry_id is None:
        return [entries[0]], True

    index = next((i for i, e in enumerate(entries) if e.guid == last_entry_id), None)
    if index is None:
        return [entries[0]], True

    newer = entries[:index]
    return list(reversed(newer)), False
