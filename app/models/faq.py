import uuid
from typing import List
from sqlalchemy import String, Text, Integer, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class FAQItem(Base, TimestampMixin):
    """FAQ 专属结构化问答条目"""
    __tablename__ = "faq_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属文档ID"
    )
    kb_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="归属知识库ID"
    )
    standard_question: Mapped[str] = mapped_column(Text, nullable=False, comment="标准问法")
    similar_questions: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False, comment="相似问法列表")
    answer: Mapped[str] = mapped_column(Text, nullable=False, comment="标准答案（支持Markdown富文本）")
    priority: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="问答优先级")

    document: Mapped["Document"] = relationship("Document", back_populates="faq_items")
