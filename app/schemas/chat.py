from typing import List, Optional, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class SourceCitation(BaseModel):
    doc_id: str
    doc_name: str
    heading: Optional[str] = None
    chunk_id: str
    snippet: Optional[str] = None
    priority: int = 1

class SuggestedQuestion(BaseModel):
    question_id: str
    title: str

class ActionButton(BaseModel):
    action: str = Field(..., description="动作标识: TRANSFER_TO_HUMAN, VIEW_ORDER 等")
    label: str = Field(..., description="按钮文字描述")
    payload: Optional[Dict[str, Any]] = None

class ChatMessageRequest(BaseModel):
    product_code: str = Field(default="SAAS_CLOUD", description="业务产品编码")
    kb_id: Optional[str] = Field(None, description="可选指定知识库ID")
    customer_id: str = Field(default="user_001", description="多客户会话隔离ID")
    session_id: Optional[str] = Field(None, description="可选会话ID")
    query: str = Field(..., min_length=1, max_length=1000, description="客户原始问句")

class ChatResponse(BaseModel):
    session_id: str = Field(..., description="当前会话ID")
    reply_type: str = Field(
        ..., 
        description="决策类型: DIRECT_ANSWER | RECOMMEND_QUESTIONS | FALLBACK | BLOCKED | TRANSACTION | HUMAN_AGENT"
    )
    confidence_score: float = Field(..., description="最终置信度得分")
    content: str = Field(..., description="机器人回复主体内容")
    raw_query: str = Field(..., description="客户原始输入")
    rewritten_query: Optional[str] = Field(None, description="标准化改写后的检索问句")
    sources: List[SourceCitation] = Field(default_factory=list, description="来源溯源列表")
    suggested_questions: List[SuggestedQuestion] = Field(default_factory=list, description="中置信度下的相似推荐问题")
    business_data: Optional[Dict[str, Any]] = Field(None, description="业务卡片数据(订单/物流/退款单)")
    action_buttons: List[ActionButton] = Field(
        default_factory=lambda: [ActionButton(action="TRANSFER_TO_HUMAN", label="转接人工客服")],
        description="交互操作按钮"
    )

class ChatSessionCreate(BaseModel):
    customer_id: str
    customer_name: str
    product_code: str = "SAAS_CLOUD"
    force_new: bool = False

class ChatSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_id: str
    customer_name: str
    product_code: str
    status: str
    assigned_agent: Optional[str] = None
    last_message: Optional[str] = None
    last_message_at: datetime
    created_at: datetime

class ChatMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: str
    content: str
    reply_type: Optional[str] = None
    confidence_score: float
    raw_query: Optional[str] = None
    rewritten_query: Optional[str] = None
    sources: List[Dict[str, Any]] = []
    suggested_questions: List[Dict[str, Any]] = []
    business_data: Optional[Dict[str, Any]] = None
    created_at: datetime

class TransferHumanRequest(BaseModel):
    session_id: str
    customer_id: str
    reason: Optional[str] = "用户主动请求转接人工客服"
