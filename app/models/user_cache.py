from sqlalchemy import Column, String, DateTime

from app.db import Base


class UserCache(Base):
    __tablename__ = "user_cache"

    id = Column(String, primary_key=True)  # OIDC sub
    display_name = Column(String, nullable=True)
    email = Column(String, nullable=True)
    last_refreshed_at = Column(DateTime(timezone=True), nullable=False)
