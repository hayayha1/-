from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class FAQItemCreate(BaseModel):
    standard_question: str = Field(..., min_length=2, max_length=500, description="标准问法")
    similar_questions: List[str] = Field(default_factory=list, description="相似问法列表")
    answer: str = Field(..., min_length=1, description="标准答案")
    priority: int = Field(default=1, ge=1, le=5, description="优先级 1-5")

class FAQBatchImportRequest(BaseModel):
    kb_id: str = Field(..., description="归属知识库ID")
    document_name: str = Field(default="批量导入FAQ问答集", description="聚合文档名称")
    items: List[FAQItemCreate] = Field(..., min_length=1, description="FAQ条目列表")
    created_by: Optional[str] = Field("admin")

class DocumentCreate(BaseModel):
    kb_id: str = Field(..., description="归属知识库ID")
    name: str = Field(..., min_length=1, max_length=255, description="文档名称")
    doc_type: str = Field(..., pattern="^(FAQ|MD|TXT)$", description="文档类型")
    content: str = Field(..., min_length=1, description="文档原始文本内容")
    priority: int = Field(default=1, ge=1, le=5, description="优先级 1-5")
    created_by: Optional[str] = Field("admin")

class DocumentUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    priority: Optional[int] = Field(None, ge=1, le=5)
    updated_by: Optional[str] = Field("admin")

class DocumentVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    doc_id: str
    version: int
    content: str
    metadata_snapshot: Optional[Dict[str, Any]] = None
    created_by: str
    created_at: datetime

class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    kb_id: str
    name: str
    doc_type: str
    content: Optional[str] = None
    file_size: int
    priority: int
    version: int
    status: str
    source_origin: Optional[str] = "MANUAL_UPLOAD"
    source_id: Optional[str] = None
    is_external_controlled: Optional[bool] = False
    created_by: str
    updated_by: str
    created_at: datetime
    updated_at: datetime

class DocumentQuery(BaseModel):
    kb_id: Optional[str] = None
    name: Optional[str] = None
    doc_type: Optional[str] = None
    priority: Optional[int] = None
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
