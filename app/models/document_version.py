import uuid
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy import String, Text, Integer, JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base

class DocumentVersion(Base):
    """文档历史版本快照（用于版本追溯与一键回滚）"""
    __tablename__ = "document_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联文档ID"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, comment="版本号")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="该版本完整内容快照")
    metadata_snapshot: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="元数据快照")
    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False, comment="操作人")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=func.now(), 
        nullable=False,
        comment="版本归档时间"
    )

    document: Mapped["Document"] = relationship("Document", back_populates="versions")
