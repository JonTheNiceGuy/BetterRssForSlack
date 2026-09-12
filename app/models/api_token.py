from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text

from app.db import Base


class ApiToken(Base):
    __tablename__ = "api_token"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String, nullable=False, unique=True)
    owner_sub = Column(String, ForeignKey("user_cache.id"), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
