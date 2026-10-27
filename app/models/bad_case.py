import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.models.base import Base, TimestampMixin

class BadCase(Base):
    """BadCase 明细实体（记录用户差评、隐性未命中、坐席人工标注及前置多轮会话）"""
    __tablename__ = "bad_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), default="tenant_default", index=True, nullable=False, comment="归属企业租户ID")
    product_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="SAAS_CLOUD", comment="归属产品线")
    kb_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="归属知识库ID")
    session_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="关联会话ID")
    message_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="产生问题的AI消息ID")
    
    # 来源分类: USER_FEEDBACK (用户显式差评), LOW_CONFIDENCE (系统隐性未命中), AGENT_LABEL (坐席人工标注)
    case_type: Mapped[str] = mapped_column(String(32), index=True, nullable=False, comment="BadCase类型")
    
    user_query: Mapped[str] = mapped_column(Text, nullable=False, comment="用户原始提问")
    user_feedback_comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="用户填写的差评原因描述")
    
    # 坐席标注分类: WRONG_ANSWER (回答错误), IRRELEVANT (答非所问), OUTDATED (答案过时), MISSING_KNOWLEDGE (缺少知识)
    agent_label_category: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, comment="坐席标注类型")
    agent_label_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="坐席标注补充说明")
    
    ai_raw_response: Mapped[str] = mapped_column(Text, nullable=False, comment="机器人当时的原始回答")
    recall_sources: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False, comment="当时检索命中的切片来源与得分")
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, comment="检索相似度置信度得分")
    
    # 会话上下文 (前置 N 轮多轮对话完整快照)
    chat_context: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False, comment="前置会话上下文快照")
    
    # 聚类归属与处理状态
    cluster_id: Mapped[Optional[str]] = mapped_column(String(36), index=True, nullable=True, comment="所属聚类簇ID")
    status: Mapped[str] = mapped_column(
        String(32), 
        default="PENDING", 
        nullable=False, 
        comment="状态: PENDING(待处理), CLUSTERED(已入簇), ADOPTED(已采纳入库), REJECTED(驳回无效)"
    )
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="驳回理由")
    resolved_doc_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="关联修复或生成的文档ID")
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="解决时间")
    
    # 根因定位分析（严格按照 7 大根因字典与全链路判定）
    root_cause_code: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True, comment="根因编码: RC_...")
    root_cause_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="根因名称: 知识缺失/召回不足...")
    root_cause_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="单条根因判定依据与详细解释")

    # 系统预判方案: ADD_SIMILAR_QUERY (追加相似问), CREATE_FAQ (新建FAQ), UPDATE_DOC (修订知识文档)
    recommended_action: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, comment="系统推荐动作")
    target_knowledge_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="关联或推荐的目标知识ID")
    
    handled_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="处理运营工号")
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="运营处理时间")
    handling_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="处理结论说明")
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=func.now(), 
        nullable=False, 
        comment="记录创建时间"
    )

class BadCaseCluster(Base, TimestampMixin):
    """BadCase 语义聚合簇（解决高频统计痛点，聚合相似问法与预判建议）"""
    __tablename__ = "bad_case_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), default="tenant_default", index=True, nullable=False, comment="归属企业租户ID")
    product_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="SAAS_CLOUD", comment="归属产品线")
    kb_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="归属知识库ID")
    
    cluster_name: Mapped[str] = mapped_column(String(255), nullable=False, comment="簇代表问题/聚合标题")
    case_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False, comment="该簇累计频次热度")
    sample_queries: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False, comment="簇内聚合的所有用户真实问法列表")
    
    # 簇整体根因定位（由簇内Case统计与优先级裁定）
    root_cause_code: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True, comment="簇整体根因编码")
    root_cause_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="簇整体根因名称")
    root_cause_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="簇整体根因官方描述信息")
    root_cause_distribution: Mapped[Dict[str, int]] = mapped_column(JSON, default=dict, nullable=False, comment="簇内各根因计数分布")

    # 系统推荐建议与分析
    recommended_action: Mapped[str] = mapped_column(String(32), default="CREATE_FAQ", nullable=False, comment="推荐动作: ADD_SIMILAR_QUERY, CREATE_FAQ, UPDATE_DOC, TUNE_KNOWLEDGE, OPTIMIZE_CONTEXT, CLARIFY_PROMPT, REJECT_CLUSTER")
    recommended_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="系统预判断推荐分析说明")
    target_knowledge_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="推荐关联的目标知识文档/FAQ ID")
    target_knowledge_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="推荐关联的目标知识标题")
    
    # 运营处理状态
    status: Mapped[str] = mapped_column(String(32), default="PENDING", nullable=False, comment="状态: PENDING(待处理), ADOPTED(已采纳), REJECTED(已驳回)")
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="驳回理由")
    resolved_doc_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="采纳后生成的知识文档/FAQ ID")
    
    handled_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="处理人")
    handled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="处理时间")

class BadCaseValidation(Base):
    """BadCase 回流回归测试记录（闭环验证新知识解决效果）"""
    __tablename__ = "bad_case_validations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    cluster_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="关联聚类簇ID")
    kb_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="归属知识库ID")
    knowledge_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="验证的上线知识ID")
    
    total_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="回归测试样本总数")
    passed_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="成功命中新知识的样本数")
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, comment="回归测试通过率")
    
    details: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False, comment="每条Query的测试明细(Query, 是否命中, 命中切片, 得分)")
    
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        default=func.now(), 
        nullable=False, 
        comment="回流验证测试时间"
    )

class BadCaseThresholdConfig(Base, TimestampMixin):
    """按知识库/产品线维度的 BadCase 判定阈值配置表"""
    __tablename__ = "bad_case_threshold_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="知识库ID")
    product_code: Mapped[str] = mapped_column(String(64), index=True, nullable=False, default="SAAS_CLOUD", comment="归属产品线")

    threshold_low: Mapped[float] = mapped_column(Float, default=0.30, nullable=False, comment="无答案底线阈值(默认0.30)")
    threshold_high: Mapped[float] = mapped_column(Float, default=0.70, nullable=False, comment="强命中高分阈值(默认0.70)")
    short_query_len: Mapped[int] = mapped_column(Integer, default=5, nullable=False, comment="上下文缺失短句字数上限(默认5)")
    candidate_rank_start: Mapped[int] = mapped_column(Integer, default=4, nullable=False, comment="候选切片起始位(默认4)")
    candidate_rank_end: Mapped[int] = mapped_column(Integer, default=10, nullable=False, comment="候选切片结束位(默认10)")
    context_window_rounds: Mapped[int] = mapped_column(Integer, default=3, nullable=False, comment="回溯用户输入轮数(默认3)")

    updated_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="最后配置人")

class ThresholdAuditLog(Base):
    """阈值变更审计日志表"""
    __tablename__ = "threshold_audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, comment="知识库ID")
    operator: Mapped[str] = mapped_column(String(64), nullable=False, comment="操作人工号/用户名")
    old_values: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False, comment="变更前阈值快照")
    new_values: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False, comment="变更后阈值快照")
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=func.now(), nullable=False, comment="变更时间")

# 7 大标准根因字典与官方描述映射
ROOT_CAUSE_DICT = {
    "RC_INVALID_CASE": {
        "name": "无效 case",
        "description": "闲聊、辱骂、乱码、敏感词、业务不支持解答",
        "priority": 1,
        "action": "REJECT_CLUSTER",
        "action_label": "场景 G: 整簇驳回 / 敏感词加黑"
    },
    "RC_MATCH_CONFUSION": {
        "name": "匹配混淆（错召回别的知识）",
        "description": "召回了别的产品 / 别的知识，答非所问",
        "priority": 2,
        "action": "TUNE_KNOWLEDGE",
        "action_label": "场景 D: 知识调优与负向排除"
    },
    "RC_KNOWLEDGE_ERROR": {
        "name": "知识错误 / 过时",
        "description": "命中了知识，但知识本身内容不对、政策过期",
        "priority": 3,
        "action": "UPDATE_DOC",
        "action_label": "场景 C: 发起长文本知识库修订草稿"
    },
    "RC_CONTEXT_MISSING": {
        "name": "上下文缺失",
        "description": "多轮对话，缺少上下文信息导致理解偏差",
        "priority": 4,
        "action": "OPTIMIZE_CONTEXT",
        "action_label": "场景 E: 优化多轮指代策略"
    },
    "RC_KNOWLEDGE_MISSING": {
        "name": "知识缺失",
        "description": "知识库里面完全没有这个问题对应的答案，检索没有召回有效切片",
        "priority": 5,
        "action": "CREATE_FAQ",
        "action_label": "场景 B: 新建独立 FAQ"
    },
    "RC_RECALL_INSUFFICIENT": {
        "name": "召回不足（有知识但没捞出来）",
        "description": "知识存在，但是相似度不够、chunk 切分问题、缺少相似问、排序低没进 top",
        "priority": 6,
        "action": "ADD_SIMILAR_QUERY",
        "action_label": "场景 A: 追加相似问法"
    },
    "RC_QUERY_AMBIGUOUS": {
        "name": "用户问题模糊",
        "description": "用户提问语义不清、信息不全，不是知识库问题",
        "priority": 7,
        "action": "CLARIFY_PROMPT",
        "action_label": "场景 F: 配置反问澄清话术"
    }
}

