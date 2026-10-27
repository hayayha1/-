import uuid
from typing import Optional, Dict, Any
from datetime import datetime
from sqlalchemy import String, Text, Integer, JSON, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin

class Tenant(Base, TimestampMixin):
    """企业租户实体（多租户数据隔离、生命周期治理与配额中心）"""
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False, comment="租户唯一标识码")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="企业/租户全称")
    status: Mapped[str] = mapped_column(
        String(32), 
        default="ACTIVE", 
        nullable=False, 
        comment="租户状态: TRIAL(试用中), ACTIVE(正常运营), SUSPENDED(已冻结/欠费), TERMINATED(已注销)"
    )
    tier: Mapped[str] = mapped_column(
        String(32), 
        default="PROFESSIONAL", 
        nullable=False, 
        comment="订阅版本套餐: STARTER(基础版), PROFESSIONAL(专业版), ENTERPRISE(旗舰版)"
    )
    contact_name: Mapped[str] = mapped_column(String(64), default="企业管理员", nullable=False, comment="主要联系人")
    contact_phone: Mapped[str] = mapped_column(String(32), default="13800000000", nullable=False, comment="联系电话")
    contact_email: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="企业联系邮箱")

    # 资源配额与用量
    max_seats: Mapped[int] = mapped_column(Integer, default=10, nullable=False, comment="最大坐席数")
    max_kbs: Mapped[int] = mapped_column(Integer, default=5, nullable=False, comment="最大知识库数量")
    monthly_token_quota: Mapped[int] = mapped_column(Integer, default=1000000, nullable=False, comment="月度LLM Token配额")
    used_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本月已消耗Token数")
    monthly_query_quota: Mapped[int] = mapped_column(Integer, default=50000, nullable=False, comment="月度问答调用量上限")
    used_queries: Mapped[int] = mapped_column(Integer, default=0, nullable=False, comment="本月已使用问答次数")

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

    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="服务到期时间")
