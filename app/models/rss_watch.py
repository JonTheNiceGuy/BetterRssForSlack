from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, Text,
    Enum as SAEnum,
)

from app.db import Base
from app.models.enums import Template, Visibility


class RssWatch(Base):
    __tablename__ = "rss_watch"

    id = Column(Integer, primary_key=True, autoincrement=True)
    feed_url = Column(String, nullable=False)
    slack_channel_id = Column(
        String, ForeignKey("slack_channel_cache.id"), nullable=False
    )
    template = Column(SAEnum(Template), nullable=False)
    visibility = Column(
        SAEnum(Visibility), nullable=False, default=Visibility.OWNER_ONLY
    )
    owning_group = Column(String, nullable=True)
    created_by_sub = Column(String, ForeignKey("user_cache.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False)
    check_interval_seconds = Column(Integer, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    consecutive_failure_count = Column(Integer, nullable=False, default=0)
    auto_pause_after_failures = Column(Integer, nullable=False, default=0)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    last_posted_at = Column(DateTime(timezone=True), nullable=True)
    last_entry_id = Column(String, nullable=True)
    last_error = Column(Text, nullable=True)
