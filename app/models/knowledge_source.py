import uuid
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import String, Text, Integer, Boolean, JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class KnowledgeSource(Base, TimestampMixin):
    """外部知识源配置实体 (支持飞书、语雀等)"""
    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="绑定的目标知识库ID"
    )
    tenant_id: Mapped[str] = mapped_column(String(64), default="default", index=True, nullable=False, comment="租户ID")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="数据源名称，如'售后服务飞书文档库'")
    source_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False, comment="数据源类型: FEISHU, YUQUE")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", nullable=False, comment="状态: ACTIVE, INACTIVE, ERROR")

    # 认证与连接凭据 (JSON 存储)
    credentials: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
        comment="连接凭据: app_id/secret 或 token 等"
    )

    # 同步模式与定时调度
    sync_mode: Mapped[str] = mapped_column(
        String(32),
        default="POLL_INCREMENTAL",
        nullable=False,
        comment="同步模式: POLL_INCREMENTAL, MANUAL, WEBHOOK"
    )
    cron_expression: Mapped[str] = mapped_column(
        String(64),
        default="0 */2 * * *",
        nullable=False,
        comment="定时轮询表达式"
    )

    # 规则过滤配置 (JSON 存储)
    sync_rules: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "folder_tokens": [],
            "exclude_draft": True,
            "auto_publish": True,
            "conflict_strategy": "SOURCE_OVERWRITE"
        },
        nullable=False,
        comment="同步与清洗规则"
    )

    last_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="最后同步时间")
    last_sync_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, comment="最后同步状态: SUCCESS, FAILED, PARTIAL_SUCCESS")

    created_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False, comment="创建人")
    updated_by: Mapped[str] = mapped_column(String(64), default="system", nullable=False, comment="更新人")

    # 关联
    knowledge_base: Mapped["KnowledgeBase"] = relationship("KnowledgeBase", back_populates="sources")
    mappings: Mapped[List["DocumentSourceMapping"]] = relationship(
        "DocumentSourceMapping",
        back_populates="knowledge_source",
        cascade="all, delete-orphan",
        passive_deletes=True
    )
    jobs: Mapped[List["SyncJob"]] = relationship(
        "SyncJob",
        back_populates="knowledge_source",
        cascade="all, delete-orphan",
        passive_deletes=True
    )


class DocumentSourceMapping(Base, TimestampMixin):
    """源文档与本地知识文档的一对一映射表（变更对比与防重主键）"""
    __tablename__ = "document_source_mappings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联数据源ID"
    )
    doc_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联本地文档ID"
    )

    source_doc_id: Mapped[str] = mapped_column(String(255), index=True, nullable=False, comment="外部系统的唯一文档ID/token/slug")
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="外部源网页或文档链接")
    source_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="外部源文档原始标题")
    source_folder_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, comment="外部源文档所在目录层级")

    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="正文清洗后的SHA256哈希")
    source_updated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="外部源最后更新时间")
    source_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="外部源版本标识/ETag")

    sync_status: Mapped[str] = mapped_column(String(32), default="SYNCED", nullable=False, comment="状态: SYNCED, CONFLICT, FAILED, ORPHANED")
    is_locked_manually: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, comment="本地是否手工加锁保护")

    # 关联
    knowledge_source: Mapped["KnowledgeSource"] = relationship("KnowledgeSource", back_populates="mappings")
    document: Mapped["Document"] = relationship("Document", backref="source_mapping")


class SyncJob(Base):
    """同步批次任务记录"""
    __tablename__ = "sync_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联数据源ID"
    )
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="触发类型: MANUAL, SCHEDULED, WEBHOOK")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, comment="任务状态: PENDING, RUNNING, COMPLETED, FAILED, PARTIAL_SUCCESS")

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False, comment="开始时间")
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="完成时间")

    total_scanned: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="扫描文档总数")
    added_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本次新增数")
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本次更新数")
    deleted_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本次软删除数")
    unchanged_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="内容未变跳过数")
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="失败数")

    error_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="异常摘要")

    knowledge_source: Mapped["KnowledgeSource"] = relationship("KnowledgeSource", back_populates="jobs")
    items: Mapped[List["SyncJobItem"]] = relationship(
        "SyncJobItem",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin"
    )


class SyncJobItem(Base):
    """单篇文档同步明细日志"""
    __tablename__ = "sync_job_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    job_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sync_jobs.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
        comment="关联批次任务ID"
    )
    source_doc_id: Mapped[str] = mapped_column(String(255), nullable=False, comment="源端文档唯一ID")
    doc_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="文档标题")
    action: Mapped[str] = mapped_column(String(32), nullable=False, comment="动作: CREATE, UPDATE, SOFT_DELETE, SKIP, ERROR")
    status: Mapped[str] = mapped_column(String(32), default="SUCCESS", nullable=False, comment="状态: SUCCESS, FAILED")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="错误说明")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False, comment="记录时间")

    job: Mapped["SyncJob"] = relationship("SyncJob", back_populates="items")
