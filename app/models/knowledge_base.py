import uuid
from typing import List, Dict, Any, Optional
from sqlalchemy import String, Text, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class KnowledgeBase(Base, TimestampMixin):
    """知识库实体"""
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), default="tenant_default", index=True, nullable=False, comment="归属企业租户ID")
    product_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="归属产品编码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="知识库名称")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="知识库描述")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, comment="状态：ACTIVE/INACTIVE")
    usage_scope: Mapped[str] = mapped_column(
        String(32), 
        default="BOT_AND_AGENT", 
        nullable=False, 
        comment="使用场景: BOT_AND_AGENT(前台机器人直接调用) / AGENT_ONLY(仅供内部人工客服坐席参考)"
    )
    doc_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="知识文档数量")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="切片总数")
    
    # 检索配置快照
    config: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "bm25_top_k": 15,
            "vector_top_k": 15,
            "rerank_top_k": 3,
            "threshold_direct": 0.80,
            "threshold_clarify": 0.50
        },
        nullable=False,
        comment="独立检索与重排阈值配置"
    )

    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False, comment="创建人")
    updated_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False, comment="更新人")

    # 级联关系：知识库删除时，所有文档、切片和FAQ全部级联删除
    documents: Mapped[List["Document"]] = relationship(
        "Document",
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    chunks: Mapped[List["DocumentChunk"]] = relationship(
        "DocumentChunk",
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    sources: Mapped[List["KnowledgeSource"]] = relationship(
        "KnowledgeSource",
        back_populates="knowledge_base",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
