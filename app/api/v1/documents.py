from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, or_

from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.chunk import DocumentChunk
from app.models.faq import FAQItem
from app.schemas.document import (
    DocumentCreate,
    DocumentUpdate,
    DocumentResponse,
    DocumentVersionResponse,
    FAQBatchImportRequest
)
from app.services.chunking.splitter import DocumentChunker
from app.services.model_service import model_service
from app.core.auth import (
    get_current_user, 
    CurrentUser, 
    require_roles, 
    ROLE_ADMIN, 
    ROLE_TECH_AGENT, 
    ROLE_OPERATOR,
    ROLE_PLATFORM_ADMIN,
    ROLE_TENANT_ADMIN,
    ROLE_KNOWLEDGE_MASTER,
    ROLE_QA_SUPERVISOR,
    ROLE_SERVICE_AGENT
)

# 知识文档与FAQ管理模块对 管理员、知识主管 与 坐席 开放
router = APIRouter(
    prefix="/documents", 
    tags=["知识文档与FAQ管理"],
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TENANT_ADMIN, ROLE_KNOWLEDGE_MASTER, ROLE_SERVICE_AGENT, ROLE_TECH_AGENT, ROLE_OPERATOR]))]
)

async def index_document_chunks(doc: Document, db: AsyncSession) -> int:
    """
    为生效文档构建切片并向量化入库（审核通过或超管直接发布时触发）
    """
    # 彻底清除可能存在的旧切片
    await db.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == doc.id))
    
    doc_info = {"id": doc.id, "name": doc.name, "priority": doc.priority}
    if doc.doc_type == "MD":
        raw_chunks = DocumentChunker.chunk_markdown(doc.content or "", doc_info)
    elif doc.doc_type == "TXT":
        raw_chunks = DocumentChunker.chunk_txt(doc.content or "", doc_info)
    elif doc.doc_type == "FAQ":
        # 如果是 FAQ，从关联的 FAQItem 中读取
        faq_stmt = select(FAQItem).where(FAQItem.doc_id == doc.id)
        faqs = (await db.execute(faq_stmt)).scalars().all()
        faq_dicts = [
            {
                "standard_question": f.standard_question,
                "similar_questions": f.similar_questions or [],
                "answer": f.answer,
                "priority": f.priority
            }
            for f in faqs
        ]
        raw_chunks = DocumentChunker.chunk_faq(faq_dicts, doc_info)
    else:
        raw_chunks = []

    for rc in raw_chunks:
        emb = await model_service.get_embedding(rc.content)
        chunk_model = DocumentChunk(
            kb_id=doc.kb_id,
            doc_id=doc.id,
            chunk_index=rc.chunk_index,
            content=rc.content,
            embedding=emb,
            heading_path=rc.heading_path,
            priority=doc.priority,
            metadata_info=rc.metadata
        )
        db.add(chunk_model)

    await db.flush()

    # 维护知识库统计计数
    kb = await db.get(KnowledgeBase, doc.kb_id)
    if kb:
        c_count_stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.kb_id == kb.id)
        kb.chunk_count = (await db.execute(c_count_stmt)).scalar() or 0
        
        doc_count_stmt = select(func.count(Document.id)).where(Document.kb_id == kb.id, Document.status == "ACTIVE")
        kb.doc_count = (await db.execute(doc_count_stmt)).scalar() or 0

    return len(raw_chunks)

@router.post("/upload", response_model=DocumentResponse, summary="上传本地文件 (技术客服提交需超管审核，超管上传直接生效)")
async def upload_document(
    kb_id: str = Form(...),
    priority: int = Form(1),
    file: UploadFile = File(...),
    created_by: str = Form("system"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="目标知识库不存在")

    # 权限隔离校验：技术客服只能向自己所属产品的知识库提交文档；运营专员只能向负责知识库提交
    if current_user.role == ROLE_TECH_AGENT:
        allowed_product = current_user.product_code or "SAAS_CLOUD"
        if kb.product_code != allowed_product:
            raise HTTPException(status_code=403, detail="权限不足：您不能向非本产品归属的知识库上传文档")
    elif current_user.role == ROLE_OPERATOR:
        allowed_kb = current_user.assigned_kb_id or "b34fb60a-392c-4bb2-9e10-57539b37fed2"
        if kb.id != allowed_kb:
            raise HTTPException(status_code=403, detail="权限不足：运营人员只能向自身负责的知识库上传文档")

    filename = file.filename or "uploaded_file.txt"
    ext = filename.rsplit(".", 1)[-1].upper() if "." in filename else "TXT"
    doc_type = "MD" if ext in ["MD", "MARKDOWN"] else "TXT"

    content_bytes = await file.read()
    try:
        content_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content_text = content_bytes.decode("gbk", errors="ignore")

    # 采审分离逻辑：一线技术坐席(service_agent)提交设为 PENDING_REVIEW 待审核草稿；知识主管/企业超管创建直接 ACTIVE
    is_authorizer = current_user.role in [ROLE_PLATFORM_ADMIN, ROLE_TENANT_ADMIN, ROLE_ADMIN, ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR]
    doc_status = "ACTIVE" if is_authorizer else "PENDING_REVIEW"

    doc = Document(
        kb_id=kb_id,
        name=filename,
        doc_type=doc_type,
        content=content_text,
        file_size=len(content_bytes),
        priority=priority,
        version=1,
        status=doc_status,
        created_by=current_user.username or created_by,
        updated_by=current_user.username or created_by
    )
    db.add(doc)
    await db.flush()

    initial_version = DocumentVersion(
        doc_id=doc.id,
        version=1,
        content=content_text,
        metadata_snapshot={"name": doc.name, "doc_type": doc.doc_type, "status": doc_status},
        created_by=doc.created_by
    )
    db.add(initial_version)

    # 仅在 ACTIVE 状态下才执行向量切片入库
    if doc_status == "ACTIVE":
        await index_document_chunks(doc, db)

    await db.commit()
    await db.refresh(doc)
    return doc

@router.post("", response_model=DocumentResponse, summary="新增知识文档 (技术坐席提交需主管审核，主管/超管创建直接生效)")
async def create_document(
    payload: DocumentCreate, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    kb = await db.get(KnowledgeBase, payload.kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="目标知识库不存在")

    if current_user.is_service_agent:
        allowed_product = current_user.product_code or "SAAS_CLOUD"
        if kb.product_code != allowed_product:
            raise HTTPException(status_code=403, detail="权限不足：您不能向非本产品归属的知识库创建文档")
    elif current_user.is_knowledge_master:
        allowed_kb = current_user.assigned_kb_id or "b34fb60a-392c-4bb2-9e10-57539b37fed2"
        if kb.id != allowed_kb and current_user.role == ROLE_OPERATOR:
            raise HTTPException(status_code=403, detail="权限不足：运营人员只能向自身负责的知识库创建文档")

    is_authorizer = current_user.role in [ROLE_PLATFORM_ADMIN, ROLE_TENANT_ADMIN, ROLE_ADMIN, ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR]
    doc_status = "ACTIVE" if is_authorizer else "PENDING_REVIEW"

    doc = Document(
        kb_id=payload.kb_id,
        name=payload.name,
        doc_type=payload.doc_type,
        content=payload.content,
        file_size=len(payload.content.encode("utf-8")),
        priority=payload.priority,
        version=1,
        status=doc_status,
        created_by=current_user.username or payload.created_by or "system",
        updated_by=current_user.username or payload.created_by or "system"
    )
    db.add(doc)
    await db.flush()

    initial_version = DocumentVersion(
        doc_id=doc.id,
        version=1,
        content=payload.content,
        metadata_snapshot={"name": doc.name, "doc_type": doc.doc_type, "status": doc_status},
        created_by=doc.created_by
    )
    db.add(initial_version)

    if doc_status == "ACTIVE":
        await index_document_chunks(doc, db)

    await db.commit()
    await db.refresh(doc)
    return doc

@router.post("/faq/batch", summary="FAQ 结构化批量导入 (技术坐席提交进入审核流)")
async def batch_import_faq(
    payload: FAQBatchImportRequest, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    kb = await db.get(KnowledgeBase, payload.kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="目标知识库不存在")

    if current_user.is_service_agent:
        allowed_product = current_user.product_code or "SAAS_CLOUD"
        if kb.product_code != allowed_product:
            raise HTTPException(status_code=403, detail="权限不足：您不能向非本产品归属的知识库录入FAQ")
    elif current_user.is_knowledge_master:
        allowed_kb = current_user.assigned_kb_id or "b34fb60a-392c-4bb2-9e10-57539b37fed2"
        if kb.id != allowed_kb and current_user.role == ROLE_OPERATOR:
            raise HTTPException(status_code=403, detail="权限不足：运营人员只能向自身负责的知识库录入FAQ")

    # 若是新角色 service_agent，强制审核流；若是兼容角色 tech_agent 或管理员/主管则 ACTIVE
    is_draft_agent = (current_user.role == ROLE_SERVICE_AGENT)
    doc_status = "PENDING_REVIEW" if is_draft_agent else "ACTIVE"

    doc = Document(
        kb_id=payload.kb_id,
        name=payload.document_name,
        doc_type="FAQ",
        file_size=0,
        priority=1,
        version=1,
        status=doc_status,
        created_by=current_user.username or payload.created_by or "system",
        updated_by=current_user.username or payload.created_by or "system"
    )
    db.add(doc)
    await db.flush()

    total_size = 0
    for item in payload.items:
        faq_row = FAQItem(
            doc_id=doc.id,
            kb_id=doc.kb_id,
            standard_question=item.standard_question,
            similar_questions=item.similar_questions,
            answer=item.answer,
            priority=item.priority
        )
        db.add(faq_row)
        total_size += len(item.answer.encode("utf-8"))

    doc.file_size = total_size
    faq_text = "\n\n".join([
        f"【标准问题】: {item.standard_question}\n" +
        (f"【相似问法】: {'；'.join(item.similar_questions)}\n" if item.similar_questions else "") +
        f"【权威解答】: {item.answer}"
        for item in payload.items
    ])
    doc.content = faq_text

    initial_version = DocumentVersion(
        doc_id=doc.id,
        version=1,
        content=faq_text,
        metadata_snapshot={"name": doc.name, "doc_type": "FAQ", "count": len(payload.items), "status": doc_status},
        created_by=doc.created_by
    )
    db.add(initial_version)

    chunk_count = await index_document_chunks(doc, db)

    await db.commit()
    return {
        "message": f"成功录入 {len(payload.items)} 条 FAQ 知识" + ("（已生效并切片）" if doc_status == "ACTIVE" else "（已提交待超管审核）"),
        "doc_id": doc.id,
        "status": doc_status,
        "chunk_count": chunk_count
    }

@router.get("", response_model=List[DocumentResponse], summary="条件分页查询知识文档 (带产品权限隔离)")
async def list_documents(
    kb_id: Optional[str] = Query(None, description="知识库ID"),
    name: Optional[str] = Query(None, description="文档名称模糊匹配"),
    doc_type: Optional[str] = Query(None, description="类型: FAQ, MD, TXT"),
    priority: Optional[int] = Query(None, ge=1, le=5, description="优先级"),
    status: Optional[str] = Query(None, description="状态: ACTIVE, PENDING_REVIEW, REJECTED"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Document)

    # 技术客服数据隔离：只能看自己产品线下的知识库文档
    if current_user.role == ROLE_TECH_AGENT:
        allowed_product = current_user.product_code or "SAAS_CLOUD"
        kb_subq = select(KnowledgeBase.id).where(KnowledgeBase.product_code == allowed_product)
        stmt = stmt.where(Document.kb_id.in_(kb_subq))
    elif current_user.role == ROLE_OPERATOR:
        # 运营人员数据隔离：每个知识库有不同组的运营，只能看负责知识库文档
        assigned_kb = current_user.assigned_kb_id or "b34fb60a-392c-4bb2-9e10-57539b37fed2"
        stmt = stmt.where(Document.kb_id == assigned_kb)

    if kb_id:
        stmt = stmt.where(Document.kb_id == kb_id)
    if name:
        stmt = stmt.where(Document.name.ilike(f"%{name}%"))
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if priority:
        stmt = stmt.where(Document.priority == priority)
    if status:
        stmt = stmt.where(Document.status == status)

    stmt = stmt.order_by(Document.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/{doc_id}", summary="获取文档详情及切片信息")
async def get_document_detail(doc_id: str, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    content = doc.content
    faq_data = None
    if doc.doc_type == "FAQ":
        f_stmt = select(FAQItem).where(FAQItem.doc_id == doc.id).limit(1)
        f = (await db.execute(f_stmt)).scalars().first()
        if f:
            faq_data = {
                "standard_question": f.standard_question,
                "similar_questions": f.similar_questions or [],
                "answer": f.answer
            }
            if not content:
                content = f"【标准问题】: {f.standard_question}\n【相似问法】: {'；'.join(f.similar_questions or [])}\n【标准解答】: {f.answer}"

    if not content:
        v_stmt = select(DocumentVersion).where(DocumentVersion.doc_id == doc.id).order_by(DocumentVersion.version.desc()).limit(1)
        v = (await db.execute(v_stmt)).scalars().first()
        if v and v.content:
            content = v.content

    stmt_chunks = select(DocumentChunk).where(DocumentChunk.doc_id == doc_id).order_by(DocumentChunk.chunk_index.asc())
    c_res = await db.execute(stmt_chunks)
    chunks = c_res.scalars().all()

    doc_resp = DocumentResponse.model_validate(doc)
    doc_resp.content = content or ""

    return {
        "doc": doc_resp,
        "content": content or "",
        "faq": faq_data,
        "chunk_count": len(chunks),
        "chunks": [
            {
                "id": c.id,
                "chunk_index": c.chunk_index,
                "heading_path": c.heading_path,
                "content_preview": c.content[:150],
                "char_count": len(c.content),
                "metadata": c.metadata_info
            }
            for c in chunks
        ]
    }

@router.put("/{doc_id}", response_model=DocumentResponse, summary="编辑文档 (客服编辑后进入待审核状态)")
async def update_document(
    doc_id: str, 
    payload: DocumentUpdate, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    is_tech = (current_user.role == ROLE_TECH_AGENT)

    # 若更新了内容，执行版本自增与快照归档
    if payload.content is not None and payload.content != doc.content:
        max_ver_stmt = select(func.max(DocumentVersion.version)).where(DocumentVersion.doc_id == doc.id)
        max_v = (await db.execute(max_ver_stmt)).scalar()
        if max_v is None or max_v < 1:
            max_v = doc.version or 1
            init_v = DocumentVersion(
                doc_id=doc.id,
                version=max_v,
                content=doc.content or "",
                metadata_snapshot={"name": doc.name, "doc_type": doc.doc_type, "priority": doc.priority},
                created_by=doc.created_by or "system"
            )
            db.add(init_v)

        next_version = max(max_v, doc.version) + 1
        doc.version = next_version
        doc.content = payload.content
        doc.file_size = len(payload.content.encode("utf-8"))
        doc.updated_by = payload.updated_by or current_user.username or "system"

        # 若为技术客服编辑，置为待审核状态
        if is_tech:
            doc.status = "PENDING_REVIEW"

        new_version_snapshot = DocumentVersion(
            doc_id=doc.id,
            version=next_version,
            content=payload.content,
            metadata_snapshot={
                "name": payload.name or doc.name, 
                "doc_type": doc.doc_type, 
                "priority": payload.priority or doc.priority,
                "status": doc.status
            },
            created_by=doc.updated_by
        )
        db.add(new_version_snapshot)

        # 超级管理员编辑则立即更新切片；技术客服编辑则暂不切片，等待超管审核
        if not is_tech and doc.status == "ACTIVE":
            await index_document_chunks(doc, db)

    if payload.name is not None:
        doc.name = payload.name
    if payload.priority is not None:
        doc.priority = payload.priority
    if payload.updated_by is not None:
        doc.updated_by = payload.updated_by or current_user.username

    await db.commit()
    await db.refresh(doc)
    return doc

# ================= 知识文档审核流 (知识主管/企业超管/平台超管) =================

@router.post(
    "/{doc_id}/approve", 
    summary="知识架构主管/企业超管审核通过知识文档 (触发自动切片与向量入库生效)",
    dependencies=[Depends(require_roles([ROLE_KNOWLEDGE_MASTER, ROLE_TENANT_ADMIN, ROLE_PLATFORM_ADMIN, ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def approve_document(
    doc_id: str, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc.status = "ACTIVE"
    doc.updated_by = current_user.username or "admin"

    # 执行切片与向量构建
    chunk_count = await index_document_chunks(doc, db)

    await db.commit()
    await db.refresh(doc)
    return {
        "message": f"文档【{doc.name}】已审核通过并成功生效！已自动构建 {chunk_count} 个切片向量",
        "doc_id": doc.id,
        "status": "ACTIVE",
        "chunk_count": chunk_count
    }

@router.post(
    "/{doc_id}/reject", 
    summary="知识架构主管/企业超管驳回知识文档",
    dependencies=[Depends(require_roles([ROLE_KNOWLEDGE_MASTER, ROLE_TENANT_ADMIN, ROLE_PLATFORM_ADMIN, ROLE_ADMIN, ROLE_OPERATOR]))]
)
async def reject_document(
    doc_id: str, 
    reason: Optional[str] = Query("内容未达标", description="驳回理由"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    doc.status = "REJECTED"
    doc.updated_by = f"{current_user.username or '主管'}(已驳回: {reason})"

    # 清除切片（如果有）
    await db.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == doc.id))
    
    # 维护知识库统计
    kb = await db.get(KnowledgeBase, doc.kb_id)
    if kb:
        c_count_stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.kb_id == kb.id)
        kb.chunk_count = (await db.execute(c_count_stmt)).scalar() or 0
        doc_count_stmt = select(func.count(Document.id)).where(Document.kb_id == kb.id, Document.status == "ACTIVE")
        kb.doc_count = (await db.execute(doc_count_stmt)).scalar() or 0

    await db.commit()
    await db.refresh(doc)
    return {
        "message": f"文档【{doc.name}】已被驳回",
        "doc_id": doc.id,
        "status": "REJECTED",
        "reason": reason
    }

@router.get("/{doc_id}/versions", response_model=List[DocumentVersionResponse], summary="查询文档所有历史版本")
async def list_document_versions(doc_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(DocumentVersion).where(DocumentVersion.doc_id == doc_id).order_by(DocumentVersion.version.desc(), DocumentVersion.created_at.desc())
    res = await db.execute(stmt)
    versions = res.scalars().all()
    seen = set()
    unique_versions = []
    for v in versions:
        if v.version not in seen:
            seen.add(v.version)
            unique_versions.append(v)
    return unique_versions

@router.post("/{doc_id}/rollback", summary="版本回滚（恢复指定历史版本）")
async def rollback_document_version(
    doc_id: str, 
    target_version: int = Query(..., description="目标恢复版本号"),
    updated_by: Optional[str] = Query("admin", description="操作人"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    stmt = select(DocumentVersion).where(
        DocumentVersion.doc_id == doc_id,
        DocumentVersion.version == target_version
    ).order_by(DocumentVersion.created_at.desc())
    res = await db.execute(stmt)
    ver_snapshot = res.scalars().first()
    if not ver_snapshot:
        raise HTTPException(status_code=404, detail="指定的历史版本快照未找到")

    return await update_document(
        doc_id=doc_id,
        payload=DocumentUpdate(content=ver_snapshot.content, updated_by=updated_by),
        current_user=current_user,
        db=db
    )

@router.delete("/{doc_id}", summary="删除文档（级联清除所有切片与版本）", dependencies=[Depends(require_roles([ROLE_KNOWLEDGE_MASTER, ROLE_TENANT_ADMIN, ROLE_PLATFORM_ADMIN, ROLE_ADMIN, ROLE_OPERATOR]))])
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    kb = await db.get(KnowledgeBase, doc.kb_id)
    await db.delete(doc)

    if kb:
        await db.flush()
        c_count_stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.kb_id == kb.id)
        kb.chunk_count = (await db.execute(c_count_stmt)).scalar() or 0
        doc_count_stmt = select(func.count(Document.id)).where(Document.kb_id == kb.id, Document.status == "ACTIVE")
        kb.doc_count = (await db.execute(doc_count_stmt)).scalar() or 0

    await db.commit()
    return {"message": "文档及切片数据已成功级联删除", "doc_id": doc_id}
