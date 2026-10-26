from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete

from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.document import Document
from app.models.chunk import DocumentChunk
from app.schemas.knowledge_base import KBCreate, KBUpdate, KBResponse
from app.core.auth import require_roles, ROLE_ADMIN, ROLE_TECH_AGENT

# 知识库管理模块：仅对超级管理员开放 (RBAC权限隔离)
router = APIRouter(
    prefix="/kbs", 
    tags=["知识库管理"],
    dependencies=[Depends(require_roles([ROLE_ADMIN]))]
)

@router.post("", response_model=KBResponse, summary="创建不同产品对应的知识库")
async def create_knowledge_base(payload: KBCreate, db: AsyncSession = Depends(get_db)):
    # 检查同产品下知识库名称是否重名
    stmt = select(KnowledgeBase).where(
        KnowledgeBase.product_code == payload.product_code,
        KnowledgeBase.name == payload.name
    )
    res = await db.execute(stmt)
    if res.scalars().first():
        raise HTTPException(status_code=400, detail="该产品下已存在同名知识库")

    kb = KnowledgeBase(
        product_code=payload.product_code,
        name=payload.name,
        description=payload.description,
        config=payload.config.model_dump() if payload.config else {},
        created_by=payload.created_by or "admin",
        updated_by=payload.created_by or "admin"
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    return kb

@router.get("", response_model=List[KBResponse], summary="查询知识库列表")
async def list_knowledge_bases(
    product_code: Optional[str] = Query(None, description="按产品编码过滤"),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(KnowledgeBase)
    if product_code:
        stmt = stmt.where(KnowledgeBase.product_code == product_code)
    stmt = stmt.order_by(KnowledgeBase.created_at.desc())
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/{kb_id}", response_model=KBResponse, summary="获取知识库详情")
async def get_knowledge_base(kb_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    res = await db.execute(stmt)
    kb = res.scalars().first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return kb

@router.put(
    "/{kb_id}", 
    response_model=KBResponse, 
    summary="编辑知识库基本信息与检索参数",
    dependencies=[Depends(require_roles([ROLE_ADMIN]))]
)
async def update_knowledge_base(
    kb_id: str, 
    payload: KBUpdate, 
    db: AsyncSession = Depends(get_db)
):
    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    res = await db.execute(stmt)
    kb = res.scalars().first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    if payload.name is not None:
        kb.name = payload.name
    if payload.description is not None:
        kb.description = payload.description
    if payload.status is not None:
        kb.status = payload.status
    if payload.config is not None:
        # 合并更新检索配置
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
    dependencies=[Depends(require_roles([ROLE_ADMIN]))]
)
async def delete_knowledge_base(kb_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(KnowledgeBase).where(KnowledgeBase.id == kb_id)
    res = await db.execute(stmt)
    kb = res.scalars().first()
    if not kb:
        raise HTTPException(status_code=404, detail="知识库不存在")

    # 执行级联删除
    await db.delete(kb)
    await db.commit()
    return {"message": "知识库及其全部关联知识切片已成功级联删除", "kb_id": kb_id}
