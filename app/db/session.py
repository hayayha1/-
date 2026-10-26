import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from app.core.config import settings
from app.models.base import Base

logger = logging.getLogger(__name__)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True
)

async_session_maker = async_sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)

async def init_db():
    """初始化数据库表与必要扩展"""
    async with engine.begin() as conn:
        if settings.PG_VECTOR_ENABLED:
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
                logger.info("PostgreSQL pgvector extension enabled successfully.")
            except Exception as e:
                logger.warning(f"Could not enable pgvector extension: {e}")
        
        await conn.run_sync(Base.metadata.create_all)

        # 兼容性迁移：确保 documents 表包含 content 列
        try:
            res = await conn.execute(text("PRAGMA table_info(documents);"))
            columns = [row[1] for row in res.fetchall()]
            if columns and "content" not in columns:
                await conn.execute(text("ALTER TABLE documents ADD COLUMN content TEXT;"))
                logger.info("Auto migrated: added column 'content' to table 'documents'.")
        except Exception:
            try:
                await conn.execute(text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS content TEXT;"))
            except Exception:
                pass

        logger.info("All database tables created or confirmed.")

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖注入 DB Session"""
    async with async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
