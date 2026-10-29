import uuid
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, JSON, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin

class Tenant(Base, TimestampMixin):
    """企业租户实体（多租户数据隔离、生命周期治理与商业化配额中心）"""
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, comment="租户唯一标识码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="企业/租户全称")
    
    # 租户全生命周期状态:
    # ACTIVE: 正常运营，使用所有功能
    # FROZEN: 业务冻结，锁定前台客服与文档处理，仅保留后台只读
    # DISABLED: 租户启停/已停用
    # TERMINATED: 客户解约/已注销
    status: Mapped[str] = mapped_column(
        String(32), 
        default="ACTIVE", 
        nullable=False, 
        comment="租户状态: ACTIVE(正常运行), FROZEN(业务冻结), DISABLED(暂停服务), TERMINATED(已注销), TRIAL(试用中)"
    )
    
    # 订阅套餐版本:
    # STARTER: 体验版 / 团队版 (1产品线, 2知识库, 3客服坐席, 1知识主管, 单库1000文档)
    # PROFESSIONAL: 专业版 (3产品线, 8知识库, 10客服坐席, 2知识主管, 单库10000文档)
    # ENTERPRISE: 旗舰版 (10产品线, 30知识库, 30客服坐席, 5知识主管, 单库50000文档)
    # CUSTOM: 企业尊享版（定制）(不限产品线, 弹性知识库与坐席, 单库独立配额)
    tier: Mapped[str] = mapped_column(
        String(32), 
        default="PROFESSIONAL", 
        nullable=False, 
        comment="订阅版本套餐: STARTER(团队版/体验版), PROFESSIONAL(专业版), ENTERPRISE(旗舰版), CUSTOM(企业尊享版)"
    )

    # 企业基础与联系人信息
    contact_name: Mapped[str] = mapped_column(String(64), default="企业管理员", nullable=False, comment="主要联系人")
    contact_phone: Mapped[str] = mapped_column(String(32), default="13800000000", nullable=False, comment="联系电话")
    contact_email: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="企业联系邮箱")
    credit_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="统一社会信用代码")
    official_website: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, comment="官方网站")
    industry: Mapped[Optional[str]] = mapped_column(String(64), default="SaaS", nullable=True, comment="所属行业")
    business_desc: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="主营业务线说明")

    # SaaS 阶梯配额划分（支持超管弹性非标追加）
    max_product_lines: Mapped[int] = mapped_column(Integer, default=3, nullable=False, comment="产品线配额上限")
    max_kbs: Mapped[int] = mapped_column(Integer, default=8, nullable=False, comment="知识库配额数量上限")
    max_seats: Mapped[int] = mapped_column(Integer, default=10, nullable=False, comment="人工客服标准坐席配额")
    max_km_seats: Mapped[int] = mapped_column(Integer, default=2, nullable=False, comment="知识主管运营坐席配额")
    max_docs_per_kb: Mapped[int] = mapped_column(Integer, default=10000, nullable=False, comment="单库文档上限")

    # Token与调用量消耗
    monthly_token_quota: Mapped[int] = mapped_column(Integer, default=5000000, nullable=False, comment="月度LLM Token配额")
    used_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本月已消耗Token数")
    monthly_query_quota: Mapped[int] = mapped_column(Integer, default=100000, nullable=False, comment="月度问答调用量上限")
    used_queries: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本月已使用问答次数")

    # 商业账务与履约监控
    subscribed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="订阅开始时间")
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="服务到期时间")
    total_paid_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False, comment="累计付费金额(元)")
    last_active_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="最后登录活跃时间")

    # 模型配置模块（企业自主配置双模型: 文本生成 LLM 与 向量化 Embedding）
    llm_config: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "provider": "DeepSeek",
            "model_name": "deepseek-chat",
            "api_base": "https://api.deepseek.com",
            "api_key": "",
            "temperature": 0.2,
            "max_tokens": 2048
        },
        nullable=False,
        comment="企业自主配置文本生成模型"
    )
    embedding_config: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "provider": "SiliconFlow",
            "model_name": "BAAI/bge-large-zh-v1.5",
            "api_base": "https://api.siliconflow.cn/v1",
            "api_key": "",
            "dimensions": 1024,
            "score_threshold": 0.50
        },
        nullable=False,
        comment="企业自主配置向量化模型"
    )

    # 产品线与业务分组配置 (1:N 层级体系)
    product_lines: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON,
        default=lambda: [
            {
                "code": "SAAS_CLOUD",
                "name": "智能云平台服务线",
                "description": "面向企业级SaaS订阅与云服务",
                "group_name": "云创云服务客服组",
                "created_at": "2026-10-01 10:00:00"
            }
        ],
        nullable=False,
        comment="企业产品线与服务场景分组"
    )

    # 人员与分组管理（客服与知识运营坐席）
    staff_config: Mapped[List[Dict[str, Any]]] = mapped_column(
        JSON,
        default=lambda: [
            {
                "user_id": "agent_1028",
                "username": "service_agent",
                "role": "service_agent",
                "display_name": "一线技术客服坐席 (工号#1028)",
                "product_code": "SAAS_CLOUD",
                "group_name": "云创云服务客服组",
                "max_concurrent_sessions": 10,
                "status": "ONLINE"
            },
            {
                "user_id": "km_001",
                "username": "knowledge_master",
                "role": "knowledge_master",
                "display_name": "知识库架构主管 (工号#km01)",
                "product_code": "SAAS_CLOUD",
                "group_name": "云创云服务客服组",
                "status": "ONLINE"
            }
        ],
        nullable=False,
        comment="客服与知识主管账号及分组分配"
    )

    # 品牌个性化配置 (Whitelabel)
    brand_config: Mapped[Dict[str, Any]] = mapped_column(
        JSON,
        default=lambda: {
            "brand_name": "智能客服平台",
            "bot_name": "AI智能助手",
            "bot_avatar": "🤖",
            "welcome_message": "您好！我是您的企业专属智能客服助手，请问有什么可以协助您？",
            "primary_color": "#4F46E5",
            "theme": "indigo"
        },
        nullable=False,
        comment="租户品牌定制化配置"
    )
