import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime
from sqlalchemy import String, Text, Integer, JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base
from app.core.config import settings

# 动态适配 Vector 类型：PostgreSQL 上使用 pgvector，其他环境降级为 JSON
try:
    if settings.PG_VECTOR_ENABLED:
        from pgvector.sqlalchemy import Vector
        VectorFieldType = Vector(settings.EMBEDDING_DIM)
    else:
        VectorFieldType = JSON
except Exception:
    VectorFieldType = JSON

class DocumentChunk(Base):
    """知识切片实体（供 BM25 与密集向量召回）"""
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属知识库ID"
    )
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属文档ID"
    )
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="切片序号")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="分块文本内容")
    embedding: Mapped[Optional[Any]] = mapped_column(VectorFieldType, nullable=True, comment="密集向量")
    heading_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, comment="面包屑层级路径")
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="继承自文档的优先级")
    metadata_info: Mapped[Dict[str, Any]] = mapped_column(
        "metadata",
        JSON,
        default=dict,
        nullable=False,
        comment="Chunk元数据注水"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=func.now(), 
        nullable=False,
        comment="生成时间"
    )

    knowledge_base: Mapped["KnowledgeBase"] = relationship("KnowledgeBase", back_populates="chunks")
    document: Mapped["Document"] = relationship("Document", back_populates="chunks")
