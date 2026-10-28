import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class ChatSession(Base, TimestampMixin):
    """客户客服会话实体（支持多客户独立会话与人工座席接管）"""
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), default="tenant_default", index=True, nullable=False, comment="归属企业租户ID")
    customer_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False, comment="客户唯一标识")
    customer_name: Mapped[str] = mapped_column(String(128), nullable=False, default="访客客户", comment="客户姓名/昵称")
    product_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="SAAS_CLOUD", comment="归属产品线")
    status: Mapped[str] = mapped_column(
        String(32), 
        default="AI_SERVICE", 
        nullable=False, 
        comment="会话状态: AI_SERVICE(AI应答中), WAITING_HUMAN(等待人工), HUMAN_CONNECTED(人工客服接管), CLOSED(已结单)"
    )
    assigned_agent: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="当前接入的人工客服工号")
    last_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="最后一条会话摘要")
    last_message_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=func.now(), 
        nullable=False,
        comment="最后活跃时间"
    )

    # 质检与服务质量审计字段
    qa_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="质检评分: 1-5分")
    qa_feedback: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="质检评语")
    qa_tags: Mapped[Optional[List[str]]] = mapped_column(JSON, default=list, nullable=True, comment="质检问题标签")
    qa_inspector: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="质检审核人工号")

    messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.created_at.asc()"
    )

class ChatMessage(Base):
    """会话消息明细（记录问答流、Query改写、三层决策及业务卡片）"""
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    session_id: Mapped[str] = mapped_column(
        String(36), 
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), 
        index=True, 
        nullable=False, 
        comment="关联会话ID"
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="角色: user, assistant, system, human_agent")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息主体内容")
    reply_type: Mapped[Optional[str]] = mapped_column(
        String(32), 
        nullable=True, 
        comment="决策类型: DIRECT_ANSWER, RECOMMEND_QUESTIONS, FALLBACK, BLOCKED, TRANSACTION, HUMAN_AGENT"
    )
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, comment="置信度得分")
    raw_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="用户原始问句")
    rewritten_query: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="LLM改写后的标准问句")
    
    # 溯源来源列表与推荐问题
    sources: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False, comment="来源出处引用")
    suggested_questions: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False, comment="推荐相似问题")
    
    # 业务接口卡片（订单卡片、物流轨迹等）
    business_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True, comment="业务交互卡片数据")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=func.now(), 
        nullable=False, 
        comment="消息发送时间"
    )

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
