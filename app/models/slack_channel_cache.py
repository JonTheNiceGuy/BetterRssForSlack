from sqlalchemy import Column, String, DateTime

from app.db import Base


class SlackChannelCache(Base):
    __tablename__ = "slack_channel_cache"

    id = Column(String, primary_key=True)  # Slack channel ID
    name = Column(String, nullable=False)
    last_refreshed_at = Column(DateTime(timezone=True), nullable=False)
