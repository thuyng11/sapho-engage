from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
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
    notes: Mapped[str] = mapped_column(Text)

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
