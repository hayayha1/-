import uuid
from typing import List, Optional
from sqlalchemy import String, Integer, BigInteger, ForeignKey, Text, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class Document(Base, TimestampMixin):
    """知识文档实体"""
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属知识库ID"
    )
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False, comment="文档名称")
    doc_type: Mapped[str] = mapped_column(String(16), index=True, nullable=False, comment="文档类型: FAQ, MD, TXT")
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="当前文档完整正文")
    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False, comment="文件字节大小")
    priority: Mapped[int] = mapped_column(Integer, default=1, index=True, nullable=False, comment="知识优先级 1-5")
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="当前版本号")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, comment="状态: INDEXING, ACTIVE, FAILED")
    source_origin: Mapped[str] = mapped_column(String(32), default="MANUAL_UPLOAD", nullable=False, comment="来源: MANUAL_UPLOAD, SYNC_ENGINE")
    source_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="关联外部数据源ID")
    is_external_controlled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="是否受外部源完全托管")

    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False, comment="创建人")
    updated_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False, comment="更新人")

    # 关联
    knowledge_base: Mapped["KnowledgeBase"] = relationship("KnowledgeBase", back_populates="documents")
    
    versions: Mapped[List["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    chunks: Mapped[List["DocumentChunk"]] = relationship(
        "DocumentChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    faq_items: Mapped[List["FAQItem"]] = relationship(
        "FAQItem",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
