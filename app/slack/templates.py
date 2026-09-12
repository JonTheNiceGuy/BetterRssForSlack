import html2text

from app.models.enums import Template
from app.worker.feed import FeedEntry

_converter = html2text.HTML2Text()
_converter.ignore_links = True
_converter.body_width = 0


def _to_text(body_html: str) -> str:
    return _converter.handle(body_html).strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _title(entry: FeedEntry, backfill: bool) -> str:
    return f"[Backfill] {entry.title}" if backfill else entry.title


def render(
    template: Template, entry: FeedEntry, *, truncate_chars: int, backfill: bool = False
) -> tuple[str, str | None]:
    title = _title(entry, backfill)
    headline = f"{title}\n{entry.link}"

    if template == Template.HEADLINE_LINK:
        return headline, None

    if template == Template.HEADLINE_LINK_TRUNCATED:
        body = _truncate(_to_text(entry.body_html), truncate_chars)
        return f"{headline}\n{body}", None

    if template == Template.HEADLINE_LINK_THREAD:
        body = _truncate(_to_text(entry.body_html), truncate_chars)
        return headline, body

    if template == Template.HEADLINE_LINK_FULL:
        body = _to_text(entry.body_html)
        return f"{headline}\n{body}", None

    raise ValueError(f"Unknown template: {template}")
