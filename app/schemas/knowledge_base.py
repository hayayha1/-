from typing import Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class KBConfigSchema(BaseModel):
    bm25_top_k: int = Field(default=15, ge=1, le=50, description="BM25 召回条数")
    vector_top_k: int = Field(default=15, ge=1, le=50, description="向量检索召回条数")
    rerank_top_k: int = Field(default=3, ge=1, le=10, description="重排后送入大模型条数")
    threshold_direct: float = Field(default=0.80, ge=0.50, le=1.0, description="直接回答置信阈值")
    threshold_clarify: float = Field(default=0.50, ge=0.10, le=0.80, description="相似澄清置信阈值")

class KBCreate(BaseModel):
    product_code: str = Field(..., min_length=1, max_length=64, description="产品线编码")
    name: str = Field(..., min_length=1, max_length=128, description="知识库名称")
    description: Optional[str] = Field(None, description="知识库描述")
    usage_scope: Optional[str] = Field("BOT_AND_AGENT", description="使用场景: BOT_AND_AGENT(机器人与客服共享) / AGENT_ONLY(仅坐席参考)")
    config: Optional[KBConfigSchema] = Field(default_factory=KBConfigSchema, description="个性化检索配置")
    created_by: Optional[str] = Field("admin", description="创建人")

class KBUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=128)
    description: Optional[str] = None
    status: Optional[str] = Field(None, pattern="^(ACTIVE|INACTIVE)$")
    usage_scope: Optional[str] = Field(None, description="使用场景: BOT_AND_AGENT / AGENT_ONLY")
    config: Optional[KBConfigSchema] = None
    updated_by: Optional[str] = Field("admin")

class KBResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_code: str
    name: str
    description: Optional[str] = None
    status: str
    usage_scope: Optional[str] = "BOT_AND_AGENT"
    doc_count: int
    chunk_count: int
    config: Dict[str, Any]
    tenant_id: Optional[str] = "tenant_default"
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime
