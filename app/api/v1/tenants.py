import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, update

from app.db.session import get_db
from app.models.tenant import Tenant
from app.models.knowledge_base import KnowledgeBase
from app.models.chat_session import ChatSession
from app.core.auth import get_current_user, CurrentUser, require_roles, ROLE_ADMIN

router = APIRouter(prefix="/tenants", tags=["多租户管理与配额中心"])

# ===== Schemas =====
class TenantBrandConfig(BaseModel):
    brand_name: str = Field(default="智能客服平台", description="企业自定义品牌名称")
    bot_name: str = Field(default="AI助手", description="机器人自定义昵称")
    bot_avatar: str = Field(default="🤖", description="机器人头像标识")
    welcome_message: str = Field(default="您好！我是您的企业专属智能客服助手，请问有什么可以协助您？", description="开场欢迎语")
    primary_color: str = Field(default="#4F46E5", description="主题品牌主色")
    theme: str = Field(default="indigo", description="主题色系")

class TenantCreateRequest(BaseModel):
    tenant_code: str = Field(..., description="租户唯一标识编码，如 tenant_retail_01")
    name: str = Field(..., description="企业全称")
    tier: str = Field(default="PROFESSIONAL", description="套餐版本: STARTER, PROFESSIONAL, ENTERPRISE")
    contact_name: str = Field(default="企业管理员", description="联系人")
    contact_phone: str = Field(default="13800000000", description="联系电话")
    contact_email: Optional[str] = Field(None, description="联系邮箱")
    max_seats: int = Field(default=10, description="最大座席配额")
    max_kbs: int = Field(default=5, description="最大知识库数量")
    monthly_token_quota: int = Field(default=1000000, description="月度Token额度")
    monthly_query_quota: int = Field(default=50000, description="月度问答额度")
    brand_config: Optional[TenantBrandConfig] = None

class TenantUpdateRequest(BaseModel):
    name: Optional[str] = None
    status: Optional[str] = None  # TRIAL, ACTIVE, SUSPENDED, TERMINATED
    tier: Optional[str] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    max_seats: Optional[int] = None
    max_kbs: Optional[int] = None
    monthly_token_quota: Optional[int] = None
    monthly_query_quota: Optional[int] = None

class TenantResponse(BaseModel):
    id: str
    tenant_code: str
    name: str
    status: str
    tier: str
    contact_name: str
    contact_phone: str
    contact_email: Optional[str] = None
    max_seats: int
    max_kbs: int
    monthly_token_quota: int
    used_tokens: int
    monthly_query_quota: int
    used_queries: int
    brand_config: Dict[str, Any]
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)

# ===== APIs =====

# 1. 租户列表检索（平台超级管理员专享）
@router.get("", response_model=List[TenantResponse], summary="查询企业租户列表（平台超管）")
async def list_tenants(
    search: Optional[str] = Query(None, description="搜索关键词（名称或编码）"),
    status: Optional[str] = Query(None, description="状态过滤: TRIAL, ACTIVE, SUSPENDED"),
    tier: Optional[str] = Query(None, description="套餐过滤"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="仅平台超级管理员有权查看全平台租户列表")

    stmt = select(Tenant)
    if search:
        s = f"%{search.strip()}%"
        stmt = stmt.where((Tenant.name.ilike(s)) | (Tenant.tenant_code.ilike(s)))
    if status:
        stmt = stmt.where(Tenant.status == status)
    if tier:
        stmt = stmt.where(Tenant.tier == tier)

    stmt = stmt.order_by(desc(Tenant.created_at))
    res = await db.execute(stmt)
    return res.scalars().all()

# 2. 获取当前用户所属/活动租户详情（含品牌个性化配置与配额）
@router.get("/current", response_model=TenantResponse, summary="获取当前活动租户配置与品牌参数")
async def get_current_tenant_profile(
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    target_code = current_user.tenant_id or "tenant_default"
    stmt = select(Tenant).where(
        (Tenant.tenant_code == target_code) | (Tenant.id == target_code)
    )
    res = await db.execute(stmt)
    tenant = res.scalars().first()
    
    if not tenant:
        # 兜底自动补建默认租户
        tenant = Tenant(
            tenant_code="tenant_default",
            name="云创智能科技有限公司",
            status="ACTIVE",
            tier="ENTERPRISE",
            contact_name="云创超管",
            contact_phone="400-888-9999",
            max_seats=20,
            max_kbs=10,
            monthly_token_quota=5000000,
            used_tokens=18200,
            monthly_query_quota=100000,
            used_queries=452,
            brand_config={
                "brand_name": "云创企业智能客服平台",
                "bot_name": "小创助理",
                "bot_avatar": "🤖",
                "welcome_message": "您好！我是云创专属智能客服助手，已接入当前企业产品知识库，很高兴为您服务！",
                "primary_color": "#4F46E5",
                "theme": "indigo"
            }
        )
        db.add(tenant)
        await db.commit()
        await db.refresh(tenant)

    return tenant

# 3. 创建新租户（平台超级管理员）
@router.post("", response_model=TenantResponse, summary="入驻/开通新企业租户（平台超管）")
async def create_tenant(
    payload: TenantCreateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="仅平台超级管理员可开通新租户")

    # 查重 tenant_code
    check_stmt = select(Tenant).where(Tenant.tenant_code == payload.tenant_code.strip())
    existing = (await db.execute(check_stmt)).scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail=f"租户标识码 [{payload.tenant_code}] 已存在，请更换！")

    brand_data = payload.brand_config.model_dump() if payload.brand_config else {
        "brand_name": payload.name,
        "bot_name": "专属AI客服",
        "bot_avatar": "🤖",
        "welcome_message": f"您好！欢迎接入【{payload.name}】智能在线客服系统，随时为您解答疑问！",
        "primary_color": "#4F46E5",
        "theme": "indigo"
    }

    new_tenant = Tenant(
        tenant_code=payload.tenant_code.strip(),
        name=payload.name.strip(),
        tier=payload.tier,
        status="ACTIVE",
        contact_name=payload.contact_name,
        contact_phone=payload.contact_phone,
        contact_email=payload.contact_email,
        max_seats=payload.max_seats,
        max_kbs=payload.max_kbs,
        monthly_token_quota=payload.monthly_token_quota,
        monthly_query_quota=payload.monthly_query_quota,
        brand_config=brand_data
    )
    db.add(new_tenant)
    await db.commit()
    await db.refresh(new_tenant)
    return new_tenant

# 4. 获取单个租户详情
@router.get("/{tenant_id_or_code}", response_model=TenantResponse, summary="获取指定租户详情")
async def get_tenant_detail(
    tenant_id_or_code: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Tenant).where(
        (Tenant.tenant_code == tenant_id_or_code) | (Tenant.id == tenant_id_or_code)
    )
    tenant = (await db.execute(stmt)).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    if not current_user.is_platform_admin and current_user.tenant_id != tenant.tenant_code:
        raise HTTPException(status_code=403, detail="跨租户越权访问被拦截")

    return tenant

# 5. 更新租户基本资料、配额与状态（平台超管）
@router.put("/{tenant_id_or_code}", response_model=TenantResponse, summary="更新租户信息与配额配置")
async def update_tenant(
    tenant_id_or_code: str,
    payload: TenantUpdateRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not current_user.is_platform_admin:
        raise HTTPException(status_code=403, detail="仅平台超管可调整租户核心配额与状态")

    stmt = select(Tenant).where(
        (Tenant.tenant_code == tenant_id_or_code) | (Tenant.id == tenant_id_or_code)
    )
    tenant = (await db.execute(stmt)).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    update_data = payload.model_dump(exclude_unset=True)
    for field, val in update_data.items():
        if val is not None:
            setattr(tenant, field, val)

    await db.commit()
    await db.refresh(tenant)
    return tenant

# 6. 自定义品牌 Whitelabel 配置更新（租户管理员或超管）
@router.put("/{tenant_id_or_code}/brand", response_model=TenantResponse, summary="更新企业品牌与人设配置")
async def update_tenant_brand(
    tenant_id_or_code: str,
    brand_payload: TenantBrandConfig,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Tenant).where(
        (Tenant.tenant_code == tenant_id_or_code) | (Tenant.id == tenant_id_or_code)
    )
    tenant = (await db.execute(stmt)).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    if not current_user.is_platform_admin and current_user.tenant_id != tenant.tenant_code:
        raise HTTPException(status_code=403, detail="跨租户越权修改品牌配置")

    tenant.brand_config = brand_payload.model_dump()
    await db.commit()
    await db.refresh(tenant)
    return tenant

# 7. 租户资源消耗与实时用量监控
@router.get("/{tenant_id_or_code}/usage", summary="租户资源消耗看板与配额健康度")
async def get_tenant_usage(
    tenant_id_or_code: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Tenant).where(
        (Tenant.tenant_code == tenant_id_or_code) | (Tenant.id == tenant_id_or_code)
    )
    tenant = (await db.execute(stmt)).scalars().first()
    if not tenant:
        raise HTTPException(status_code=404, detail="租户不存在")

    # 统计本租户当前实际知识库数量
    kb_count = (await db.execute(
        select(func.count(KnowledgeBase.id)).where(KnowledgeBase.tenant_id == tenant.tenant_code)
    )).scalar_one()

    # 统计本租户当前活跃会话数量
    active_sessions = (await db.execute(
        select(func.count(ChatSession.id)).where(
            ChatSession.tenant_id == tenant.tenant_code,
            ChatSession.status != "CLOSED"
        )
    )).scalar_one()

    token_percent = round((tenant.used_tokens / max(tenant.monthly_token_quota, 1)) * 100, 1)
    query_percent = round((tenant.used_queries / max(tenant.monthly_query_quota, 1)) * 100, 1)
    kb_percent = round((kb_count / max(tenant.max_kbs, 1)) * 100, 1)

    return {
        "tenant_code": tenant.tenant_code,
        "name": tenant.name,
        "status": tenant.status,
        "tier": tenant.tier,
        "metrics": {
            "token": {
                "used": tenant.used_tokens,
                "quota": tenant.monthly_token_quota,
                "percent": min(token_percent, 100.0)
            },
            "queries": {
                "used": tenant.used_queries,
                "quota": tenant.monthly_query_quota,
                "percent": min(query_percent, 100.0)
            },
            "kbs": {
                "used": kb_count,
                "quota": tenant.max_kbs,
                "percent": min(kb_percent, 100.0)
            },
            "active_sessions": active_sessions,
            "max_seats": tenant.max_seats
        }
    }
