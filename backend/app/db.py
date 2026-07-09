"""DB 연결 및 ORM 모델 (SQLite + JSON).

자체포함 배포(MSI)를 위해 서버가 필요 없는 SQLite를 사용한다. JSON 컬럼은
SQLAlchemy 제네릭 JSON 타입으로, 스펙/리뷰/분석 결과를 그대로 저장한다.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .config import settings
from .paths import db_path

# database_url이 비어 있으면 데이터 폴더의 app.db(SQLite) 사용
_DEFAULT_DB = f"sqlite+aiosqlite:///{db_path().as_posix()}"
DATABASE_URL = settings.database_url or _DEFAULT_DB

engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


class CrawlSession(Base):
    """검색어 1회 실행 단위(세션)."""

    __tablename__ = "crawl_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    query: Mapped[str] = mapped_column(String(300), index=True)
    sources: Mapped[dict[str, Any]] = mapped_column(JSON, default=list)  # ["naver","coupang"]
    status: Mapped[str] = mapped_column(String(30), default="running")  # running/done/error
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    products: Mapped[list["Product"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    analyses: Mapped[list["Analysis"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Product(Base):
    """상세페이지에서 수집한 개별 상품."""

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("crawl_sessions.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(20), index=True)  # naver / coupang
    rank: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(Text)
    price: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mall_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    image_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 스펙: 구조화(JSONB) + 자연어 텍스트
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    spec_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    reviews_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=list)
    # NLP 전처리 결과: 긍/부정 분포 + 핵심 키워드 + 대표 샘플
    review_analysis: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    rating: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    review_count: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    raw_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    crawl_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["CrawlSession"] = relationship(back_populates="products")


class Analysis(Base):
    """세션 데이터를 Gemini로 분석한 결과."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("crawl_sessions.id", ondelete="CASCADE"), index=True
    )
    prompt: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(String(60))
    result_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    session: Mapped["CrawlSession"] = relationship(back_populates="analyses")


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
