from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Influencer(Base):
    __tablename__ = "influencers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(200))
    linkedin_url: Mapped[str] = mapped_column(String(500))
    relevance_score: Mapped[float] = mapped_column(Float)
    activity_score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    engagement_score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    credibility_score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    overall_score: Mapped[float] = mapped_column(Float, default=0, server_default="0")
    notes: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(50), default="sample", server_default="sample")
    is_sample: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")

    posts: Mapped[list[Post]] = relationship(back_populates="influencer")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True)
    influencer_id: Mapped[int] = mapped_column(
        ForeignKey("influencers.id"), index=True
    )
    content: Mapped[str] = mapped_column(Text)
    post_url: Mapped[str] = mapped_column(String(500))
    # Store UTC without an offset because SQLite does not preserve time zones.
    posted_at: Mapped[datetime] = mapped_column(DateTime)
    likes: Mapped[int] = mapped_column(Integer)
    comments: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), default="new", server_default="new")

    influencer: Mapped[Influencer] = relationship(back_populates="posts")
    generated_responses: Mapped[list[GeneratedResponse]] = relationship(back_populates="post")


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class GeneratedResponse(Base):
    __tablename__ = "generated_responses"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), index=True)
    goal: Mapped[str] = mapped_column(String(50))
    tone: Mapped[str] = mapped_column(String(50))
    length: Mapped[str] = mapped_column(String(50))
    custom_instruction: Mapped[str | None] = mapped_column(Text)
    generated_text: Mapped[str] = mapped_column(Text)
    edited_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="draft", server_default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now)

    post: Mapped[Post] = relationship(back_populates="generated_responses")
