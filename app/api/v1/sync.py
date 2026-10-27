from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload

from app.db.session import get_db
from app.models.knowledge_source import KnowledgeSource, SyncJob, SyncJobItem
from app.models.knowledge_base import KnowledgeBase
from app.schemas.sync import (
    KnowledgeSourceCreate,
    KnowledgeSourceUpdate,
    KnowledgeSourceResponse,
    SyncJobResponse,
    SyncJobDetailResponse,
    SyncJobItemResponse,
    ConnectivityTestResponse
)
from app.services.sync.sync_service import SyncService
from app.services.sync.feishu_connector import FeishuConnector
from app.services.sync.yuque_connector import YuqueConnector
from app.core.auth import (
    get_current_user,
    CurrentUser,
    require_roles,
    ROLE_ADMIN,
    ROLE_OPERATOR
)

router = APIRouter(prefix="/sync", tags=["知识来源同步管理"])

# ================= 1. 数据源管理与配置 =================

@router.get(
    "/sources", 
    response_model=List[KnowledgeSourceResponse], 
    summary="获取知识源配置列表",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def list_knowledge_sources(
    kb_id: Optional[str] = Query(None, description="按归属知识库过滤"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(KnowledgeSource).order_by(desc(KnowledgeSource.created_at))
    if kb_id:
        stmt = stmt.where(KnowledgeSource.kb_id == kb_id)

    # 运营人员只能查看自身负责的知识库
    if current_user.role == ROLE_OPERATOR and current_user.assigned_kb_id:
        stmt = stmt.where(KnowledgeSource.kb_id == current_user.assigned_kb_id)

    res = await db.execute(stmt)
    return res.scalars().all()

@router.post(
    "/sources", 
    response_model=KnowledgeSourceResponse, 
    summary="新建知识数据源配置",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def create_knowledge_source(
    payload: KnowledgeSourceCreate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    kb = await db.get(KnowledgeBase, payload.kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="目标知识库不存在")

    if current_user.role == ROLE_OPERATOR and current_user.assigned_kb_id:
        if kb.id != current_user.assigned_kb_id:
            raise HTTPException(status_code=403, detail="权限不足：运营人员只能在自身负责的知识库下创建数据源")

    source = KnowledgeSource(
        kb_id=payload.kb_id,
        tenant_id="default",
        name=payload.name,
        source_type=payload.source_type,
        credentials=payload.credentials,
        sync_mode=payload.sync_mode,
        cron_expression=payload.cron_expression,
        sync_rules=payload.sync_rules or {},
        created_by=current_user.username or "system",
        updated_by=current_user.username or "system"
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source

@router.get(
    "/sources/{source_id}", 
    response_model=KnowledgeSourceResponse, 
    summary="获取单个知识源详情",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def get_knowledge_source(
    source_id: str,
    db: AsyncSession = Depends(get_db)
):
    source = await db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return source

@router.put(
    "/sources/{source_id}", 
    response_model=KnowledgeSourceResponse, 
    summary="更新知识源配置",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def update_knowledge_source(
    source_id: str,
    payload: KnowledgeSourceUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    source = await db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    if payload.name is not None:
        source.name = payload.name
    if payload.credentials is not None:
        source.credentials = payload.credentials
    if payload.sync_mode is not None:
        source.sync_mode = payload.sync_mode
    if payload.cron_expression is not None:
        source.cron_expression = payload.cron_expression
    if payload.sync_rules is not None:
        source.sync_rules = payload.sync_rules
    if payload.status is not None:
        source.status = payload.status

    source.updated_by = current_user.username or "system"
    await db.commit()
    await db.refresh(source)
    return source

@router.delete(
    "/sources/{source_id}", 
    summary="删除知识源配置",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def delete_knowledge_source(
    source_id: str,
    db: AsyncSession = Depends(get_db)
):
    source = await db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    await db.delete(source)
    await db.commit()
    return {"status": "success", "message": f"数据源 '{source.name}' 已成功删除"}

# ================= 2. 连通性测试 (保存前体检 & 运行中检测) =================

@router.post(
    "/sources/{source_id}/test", 
    response_model=ConnectivityTestResponse, 
    summary="测试已保存数据源的连通性",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def test_source_connectivity(
    source_id: str,
    db: AsyncSession = Depends(get_db)
):
    source = await db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    result = await SyncService.test_connection(source)
    return ConnectivityTestResponse(
        success=result.get("success", False),
        message=result.get("message", "测试完成"),
        details=result.get("details")
    )

@router.post(
    "/sources/test-transient", 
    response_model=ConnectivityTestResponse, 
    summary="临时测试连通性 (表单保存前测试)",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def test_transient_connectivity(
    payload: KnowledgeSourceCreate
):
    stype = (payload.source_type or "").upper()
    if stype == "FEISHU":
        connector = FeishuConnector(payload.credentials, payload.sync_rules or {})
    elif stype == "YUQUE":
        connector = YuqueConnector(payload.credentials, payload.sync_rules or {})
    else:
        raise HTTPException(status_code=400, detail="不支持的数据源类型")

    result = await connector.test_connection()
    return ConnectivityTestResponse(
        success=result.get("success", False),
        message=result.get("message", "测试完成"),
        details=result.get("details")
    )

# ================= 3. 手动同步与任务流转 =================

@router.post(
    "/sources/{source_id}/trigger", 
    response_model=SyncJobResponse, 
    summary="立即触发同步任务 (增量/全量扫描)",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def trigger_sync(
    source_id: str,
    db: AsyncSession = Depends(get_db)
):
    source = await db.get(KnowledgeSource, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    job = await SyncService.execute_sync(source_id=source.id, trigger_type="MANUAL", db=db)
    return job

@router.get(
    "/sources/{source_id}/jobs", 
    response_model=List[SyncJobResponse], 
    summary="获取指定数据源的同步任务历史",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def list_source_jobs(
    source_id: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(SyncJob)
        .where(SyncJob.source_id == source_id)
        .order_by(desc(SyncJob.started_at))
        .limit(limit)
    )
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get(
    "/jobs/{job_id}", 
    response_model=SyncJobDetailResponse, 
    summary="获取指定同步任务详情及文档处理明细",
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def get_job_detail(
    job_id: str,
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(SyncJob)
        .options(selectinload(SyncJob.items))
        .where(SyncJob.id == job_id)
    )
    res = await db.execute(stmt)
    job = res.scalars().first()
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job

# ================= 4. Webhook 实时回调接口 (公开免鉴权，内部校验签名) =================

@router.post(
    "/webhook/{source_id}", 
    summary="外部系统实时变更 Webhook 接收端点"
)
async def receive_webhook(
    source_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    headers = dict(request.headers)
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    res = await SyncService.handle_webhook(source_id=source_id, headers=headers, payload=payload, db=db)
    return res
