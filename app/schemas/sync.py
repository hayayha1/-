from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class KnowledgeSourceCreate(BaseModel):
    kb_id: str = Field(..., description="归属知识库ID")
    name: str = Field(..., min_length=2, max_length=128, description="数据源名称，如'售后知识飞书同步'")
    source_type: str = Field(..., pattern="^(FEISHU|YUQUE)$", description="来源类型: FEISHU 或 YUQUE")
    credentials: Dict[str, Any] = Field(default_factory=dict, description="认证凭据")
    sync_mode: str = Field(default="POLL_INCREMENTAL", description="同步模式: POLL_INCREMENTAL, MANUAL, WEBHOOK")
    cron_expression: str = Field(default="0 */2 * * *", description="Cron表达式")
    sync_rules: Optional[Dict[str, Any]] = Field(
        default_factory=lambda: {
            "folder_tokens": [],
            "exclude_draft": True,
            "auto_publish": True,
            "conflict_strategy": "SOURCE_OVERWRITE"
        },
        description="过滤与同步规则"
    )

class KnowledgeSourceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=128)
    credentials: Optional[Dict[str, Any]] = None
    sync_mode: Optional[str] = None
    cron_expression: Optional[str] = None
    sync_rules: Optional[Dict[str, Any]] = None
    status: Optional[str] = Field(None, pattern="^(ACTIVE|INACTIVE)$")

class KnowledgeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kb_id: str
    tenant_id: str
    name: str
    source_type: str
    status: str
    credentials: Dict[str, Any]
    sync_mode: str
    cron_expression: str
    sync_rules: Dict[str, Any]
    last_sync_at: Optional[datetime] = None
    last_sync_status: Optional[str] = None
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime

class SyncJobItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    source_doc_id: str
    doc_title: Optional[str] = None
    action: str
    status: str
    error_message: Optional[str] = None
    created_at: datetime

class SyncJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_id: str
    trigger_type: str
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    total_scanned: int
    added_count: int
    updated_count: int
    deleted_count: int
    unchanged_count: int
    failed_count: int
    error_summary: Optional[str] = None
    items: Optional[List[SyncJobItemResponse]] = None

class SyncJobDetailResponse(SyncJobResponse):
    items: List[SyncJobItemResponse] = []

class ConnectivityTestResponse(BaseModel):
    success: bool
    message: str
    details: Optional[Dict[str, Any]] = None
