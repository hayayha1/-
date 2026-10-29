from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.models.tenant import Tenant
from app.schemas.knowledge_base import KBCreate, KBUpdate, KBResponse
from app.core.auth import (
    get_current_user, 
    CurrentUser, 
    require_roles, 
    ROLE_ADMIN, 
    ROLE_TENANT_ADMIN,
    ROLE_KNOWLEDGE_MASTER,
    ROLE_TECH_AGENT, 
    ROLE_SERVICE_AGENT,
    ROLE_OPERATOR,
    ROLE_PLATFORM_ADMIN
)

router = APIRouter(
    prefix="/kbs", 
    tags=["知识库管理"]
)

@router.post(
    "", 
    response_model=KBResponse, 
    summary="创建不同产品对应的知识库（支持租户配额校验）",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TENANT_ADMIN, ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR]))]
)
async def create_knowledge_base(
    payload: KBCreate, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    target_tenant = current_user.tenant_id or "tenant_default"

    # 1. 租户配额与状态检查
    tenant_stmt = select(Tenant).where(Tenant.tenant_code == target_tenant)
    tenant = (await db.execute(tenant_stmt)).scalars().first()
    if tenant:
        if tenant.status in ["FROZEN", "SUSPENDED", "DISABLED", "TERMINATED"]:
            raise HTTPException(status_code=403, detail="当前企业租户服务处于冻结或停用状态，无法创建新知识库")
        
        current_kb_count = (await db.execute(
            select(func.count(KnowledgeBase.id)).where(KnowledgeBase.tenant_id == target_tenant)
        )).scalar_one()

        if current_kb_count >= tenant.max_kbs:
            raise HTTPException(
                status_code=400, 
                detail=f"当前企业租户知识库数量已达配额上限 ({tenant.max_kbs}个)，请升级租户套餐或联系平台管理员！"
            )

    # 2. 检查同租户、同产品下知识库名称是否重名
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.tenant_id == target_tenant,
        KnowledgeBase.product_code == payload.product_code,
        KnowledgeBase.name == payload.name
    )
    res = await db.execute(stmt)
    if res.scalars().first():
        raise HTTPException(status_code=400, detail="当前企业该产品线下已存在同名知识库")

    kb = KnowledgeBase(
        tenant_id=target_tenant,
        product_code=payload.product_code,
        name=payload.name,
        description=payload.description,
        usage_scope=payload.usage_scope or "BOT_AND_AGENT",
        config=payload.config.model_dump() if payload.config else {},
        created_by=payload.created_by or current_user.user_id,
        updated_by=payload.created_by or current_user.user_id
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return kb

@router.get(
    "", 
    response_model=List[KBResponse], 
    summary="查询知识库列表 (按租户与运营隔离)",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TENANT_ADMIN, ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR]))]
)
async def list_knowledge_bases(
    product_code: Optional[str] = Query(None, description="按产品编码过滤"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == ROLE_PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="平台管理员无权访问租户私有知识库")

    stmt = select(KnowledgeBase)
    target_tenant = current_user.tenant_id or "tenant_default"

    # 多租户数据隔离
    stmt = stmt.where(KnowledgeBase.tenant_id == target_tenant)
    
    # 权限与数据隔离：技术客服只能看归属产品，运营专员只能看负责知识库
    if current_user.role in [ROLE_TECH_AGENT, ROLE_SERVICE_AGENT]:
        allowed_product = current_user.product_code or "SAAS_CLOUD"
        stmt = stmt.where(KnowledgeBase.product_code == allowed_product)
    elif current_user.role in [ROLE_OPERATOR, ROLE_KNOWLEDGE_MASTER]:
        if current_user.assigned_kb_id:
            stmt = stmt.where(KnowledgeBase.id == current_user.assigned_kb_id)
        elif product_code:
            stmt = stmt.where(KnowledgeBase.product_code == product_code)
    elif product_code:
        stmt = stmt.where(KnowledgeBase.product_code == product_code)

    stmt = stmt.order_by(KnowledgeBase.created_at.asc())
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/{kb_id}", response_model=KBResponse, summary="获取知识库详情")
async def get_knowledge_base(
    kb_id: str, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == ROLE_PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="平台管理员无权访问租户私有知识库")

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    res = await db.execute(stmt)
    kb = res.scalars().first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    
    # 租户隔离校验
    if kb.tenant_id != (current_user.tenant_id or "tenant_default"):
        raise HTTPException(status_code=403, detail="跨租户越权访问知识库被拒绝")

    # 技术客服跨产品线越权访问校验
    if current_user.role in [ROLE_TECH_AGENT, ROLE_SERVICE_AGENT]:
        allowed_product = current_user.product_code or "SAAS_CLOUD"
        if kb.product_code != allowed_product:
            raise HTTPException(status_code=403, detail="权限不足：您无权查阅非本产品归属的知识库")

    return kb

@router.put(
    "/{kb_id}", 
    response_model=KBResponse, 
    summary="编辑知识库基本信息与检索参数",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TENANT_ADMIN, ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR]))]
)
async def update_knowledge_base(
    kb_id: str, 
    payload: KBUpdate, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == ROLE_PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="平台管理员无权修改租户私有知识库")

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    res = await db.execute(stmt)
    kb = res.scalars().first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if kb.tenant_id != (current_user.tenant_id or "tenant_default"):
        raise HTTPException(status_code=403, detail="跨租户越权修改知识库被拒绝")

    if payload.name is not None:
        kb.name = payload.name
    if payload.description is not None:
        kb.description = payload.description
    if payload.usage_scope is not None:
        kb.usage_scope = payload.usage_scope
    if payload.status is not None:
        kb.status = payload.status
    if payload.config is not None:
        current_cfg = dict(kb.config or {})
        current_cfg.update(payload.config.model_dump())
        kb.config = current_cfg
    if payload.updated_by is not None:
        kb.updated_by = payload.updated_by

    await db.commit()
    await db.refresh(kb)
    return kb

@router.delete(
    "/{kb_id}", 
    summary="删除知识库（级联删除全部关联文档与切片）",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TENANT_ADMIN]))]
)
async def delete_knowledge_base(
    kb_id: str, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role == ROLE_PLATFORM_ADMIN:
        raise HTTPException(status_code=403, detail="平台管理员无权删除租户私有知识库")

    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    res = await db.execute(stmt)
    kb = res.scalars().first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if kb.tenant_id != (current_user.tenant_id or "tenant_default"):
        raise HTTPException(status_code=403, detail="跨租户越权删除知识库被拒绝")

    await db.delete(kb)
    await db.commit()
    return {"message": "知识库及其全部关联知识切片已成功级联删除", "kb_id": kb_id}
