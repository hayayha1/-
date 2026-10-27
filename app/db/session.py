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

        # 兼容性迁移：确保 documents 表包含 content 及知识源同步字段
        try:
            res = await conn.execute(text("PRAGMA table_info(documents);"))
            columns = [row[1] for row in res.fetchall()]
            if columns and "content" not in columns:
                await conn.execute(text("ALTER TABLE documents ADD COLUMN content TEXT;"))
                logger.info("Auto migrated: added column 'content' to table 'documents'.")
            if columns and "source_origin" not in columns:
                await conn.execute(text("ALTER TABLE documents ADD COLUMN source_origin VARCHAR(32) DEFAULT 'MANUAL_UPLOAD';"))
                logger.info("Auto migrated: added column 'source_origin' to table 'documents'.")
            if columns and "source_id" not in columns:
                await conn.execute(text("ALTER TABLE documents ADD COLUMN source_id VARCHAR(36);"))
                logger.info("Auto migrated: added column 'source_id' to table 'documents'.")
            if columns and "is_external_controlled" not in columns:
                await conn.execute(text("ALTER TABLE documents ADD COLUMN is_external_controlled BOOLEAN DEFAULT 0;"))
                logger.info("Auto migrated: added column 'is_external_controlled' to table 'documents'.")
        except Exception:
            pass

        # 兼容性迁移：确保 bad_cases 包含根因及解决时间字段
        try:
            res = await conn.execute(text("PRAGMA table_info(bad_cases);"))
            bc_cols = [row[1] for row in res.fetchall()]
            if bc_cols:
                for col_name, col_type in [
                    ("root_cause_code", "TEXT"),
                    ("root_cause_name", "TEXT"),
                    ("root_cause_reason", "TEXT"),
                    ("resolved_doc_id", "TEXT"),
                    ("resolved_at", "DATETIME")
                ]:
                    if col_name not in bc_cols:
                        await conn.execute(text(f"ALTER TABLE bad_cases ADD COLUMN {col_name} {col_type};"))
                        logger.info(f"Auto migrated: added column '{col_name}' to table 'bad_cases'.")
        except Exception as e:
            logger.warning(f"bad_cases migration skipped or failed: {e}")

        # 兼容性迁移：确保 bad_case_clusters 包含根因及分布字段
        try:
            res = await conn.execute(text("PRAGMA table_info(bad_case_clusters);"))
            bcc_cols = [row[1] for row in res.fetchall()]
            if bcc_cols:
                for col_name, col_type in [
                    ("root_cause_code", "TEXT"),
                    ("root_cause_name", "TEXT"),
                    ("root_cause_description", "TEXT"),
                    ("root_cause_distribution", "TEXT")
                ]:
                    if col_name not in bcc_cols:
                        await conn.execute(text(f"ALTER TABLE bad_case_clusters ADD COLUMN {col_name} {col_type};"))
                        logger.info(f"Auto migrated: added column '{col_name}' to table 'bad_case_clusters'.")
        except Exception as e:
            logger.warning(f"bad_case_clusters migration skipped or failed: {e}")

        # 兼容性迁移：确保多租户隔离字段 tenant_id 存在
        for tbl in ["knowledge_bases", "chat_sessions", "bad_cases", "bad_case_clusters"]:
            try:
                res = await conn.execute(text(f"PRAGMA table_info({tbl});"))
                tbl_cols = [row[1] for row in res.fetchall()]
                if tbl_cols and "tenant_id" not in tbl_cols:
                    await conn.execute(text(f"ALTER TABLE {tbl} ADD COLUMN tenant_id VARCHAR(64) DEFAULT 'tenant_default';"))
                    logger.info(f"Auto migrated: added column 'tenant_id' to table '{tbl}'.")
            except Exception as e:
                logger.warning(f"tenant_id migration skipped for {tbl}: {e}")

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
