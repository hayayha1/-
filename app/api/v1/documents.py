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
from app.core.auth import require_roles, ROLE_ADMIN, ROLE_TECH_AGENT

# 知识文档与FAQ管理模块仅对 超级管理员 与 技术客服 开放
router = APIRouter(
    prefix="/documents", 
    tags=["知识文档与FAQ管理"],
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TECH_AGENT]))]
)

@router.post("/upload", response_model=DocumentResponse, summary="直接上传本地文件并解析分块 (MD / TXT)")
async def upload_document(
    kb_id: str = Form(...),
    priority: int = Form(1),
    file: UploadFile = File(...),
    created_by: str = Form("admin"),
    db: AsyncSession = Depends(get_db)
):
    kb = await db.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="目标知识库不存在")

    filename = file.filename or "uploaded_file.txt"
    ext = filename.rsplit(".", 1)[-1].upper() if "." in filename else "TXT"
    doc_type = "MD" if ext in ["MD", "MARKDOWN"] else "TXT"

    content_bytes = await file.read()
    try:
        content_text = content_bytes.decode("utf-8")
    except UnicodeDecodeError:
        content_text = content_bytes.decode("gbk", errors="ignore")

    doc = Document(
        kb_id=kb_id,
        name=filename,
        doc_type=doc_type,
        content=content_text,
        file_size=len(content_bytes),
        priority=priority,
        version=1,
        status="ACTIVE",
        created_by=created_by,
        updated_by=created_by
    )
    db.add(doc)
    await db.flush()

    initial_version = DocumentVersion(
        doc_id=doc.id,
        version=1,
        content=content_text,
        metadata_snapshot={"name": doc.name, "doc_type": doc.doc_type},
        created_by=created_by
    )
    db.add(initial_version)

    doc_info = {"id": doc.id, "name": doc.name, "priority": doc.priority}
    if doc_type == "MD":
        raw_chunks = DocumentChunker.chunk_markdown(content_text, doc_info)
    else:
        raw_chunks = DocumentChunker.chunk_txt(content_text, doc_info)

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

    kb.doc_count += 1
    kb.chunk_count += len(raw_chunks)

    await db.commit()
    await db.refresh(doc)
    return doc

@router.post("", response_model=DocumentResponse, summary="新增知识文档 (MD / TXT)")
async def create_document(payload: DocumentCreate, db: AsyncSession = Depends(get_db)):
    # 1. 验证知识库存在
    kb = await db.get(KnowledgeBase, payload.kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="目标知识库不存在")

    # 2. 创建文档主体
    doc = Document(
        kb_id=payload.kb_id,
        name=payload.name,
        doc_type=payload.doc_type,
        content=payload.content,
        file_size=len(payload.content.encode("utf-8")),
        priority=payload.priority,
        version=1,
        status="ACTIVE",
        created_by=payload.created_by or "admin",
        updated_by=payload.created_by or "admin"
    )
    db.add(doc)
    await db.flush()

    # 3. 归档初始版本快照
    initial_version = DocumentVersion(
        doc_id=doc.id,
        version=1,
        content=payload.content,
        metadata_snapshot={"name": doc.name, "doc_type": doc.doc_type},
        created_by=doc.created_by
    )
    db.add(initial_version)

    # 4. 执行自动切片
    doc_info = {"id": doc.id, "name": doc.name, "priority": doc.priority}
    if payload.doc_type == "MD":
        raw_chunks = DocumentChunker.chunk_markdown(payload.content, doc_info)
    else:
        raw_chunks = DocumentChunker.chunk_txt(payload.content, doc_info)

    # 5. 向量化与持久化 Chunks
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

    # 6. 更新知识库文档统计
    kb.doc_count += 1
    kb.chunk_count += len(raw_chunks)

    await db.commit()
    await db.refresh(doc)
    return doc

@router.post("/faq/batch", summary="FAQ 结构化批量导入（标准问 + 相似问法列表 + 标准答案）")
async def batch_import_faq(payload: FAQBatchImportRequest, db: AsyncSession = Depends(get_db)):
    kb = await db.get(KnowledgeBase, payload.kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="目标知识库不存在")

    # 1. 建立聚合 FAQ 文档载体
    doc = Document(
        kb_id=payload.kb_id,
        name=payload.document_name,
        doc_type="FAQ",
        file_size=0,
        priority=1,
        version=1,
        status="ACTIVE",
        created_by=payload.created_by or "admin",
        updated_by=payload.created_by or "admin"
    )
    db.add(doc)
    await db.flush()

    # 2. 逐条沉淀 FAQ 条目并生成原子切片
    faq_dicts = []
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
        faq_dicts.append({
            "standard_question": item.standard_question,
            "similar_questions": item.similar_questions,
            "answer": item.answer,
            "priority": item.priority
        })

    doc.file_size = total_size
    faq_text = "\n\n".join([
        f"【标准问题】: {item.standard_question}\n" +
        (f"【相似问法】: {'；'.join(item.similar_questions)}\n" if item.similar_questions else "") +
        f"【权威解答】: {item.answer}"
        for item in payload.items
    ])
    doc.content = faq_text

    # 归档 FAQ 初始版本
    initial_version = DocumentVersion(
        doc_id=doc.id,
        version=1,
        content=faq_text,
        metadata_snapshot={"name": doc.name, "doc_type": "FAQ", "count": len(payload.items)},
        created_by=doc.created_by
    )
    db.add(initial_version)

    # 3. FAQ 零切片增强
    doc_info = {"id": doc.id, "name": doc.name, "priority": doc.priority}
    chunks = DocumentChunker.chunk_faq(faq_dicts, doc_info)

    # 4. 向量化入库
    for c in chunks:
        emb = await model_service.get_embedding(c.content)
        chunk_model = DocumentChunk(
            kb_id=doc.kb_id,
            doc_id=doc.id,
            chunk_index=c.chunk_index,
            content=c.content,
            embedding=emb,
            heading_path=c.heading_path,
            priority=c.metadata.get("priority", 1),
            metadata_info=c.metadata
        )
        db.add(chunk_model)

    kb.doc_count += 1
    kb.chunk_count += len(chunks)

    await db.commit()
    return {
        "message": f"成功批量导入 {len(payload.items)} 条 FAQ 知识",
        "doc_id": doc.id,
        "chunk_count": len(chunks)
    }

@router.get("", response_model=List[DocumentResponse], summary="条件分页查询知识文档")
async def list_documents(
    kb_id: Optional[str] = Query(None, description="知识库ID"),
    name: Optional[str] = Query(None, description="文档名称模糊匹配"),
    doc_type: Optional[str] = Query(None, description="类型: FAQ, MD, TXT"),
    priority: Optional[int] = Query(None, ge=1, le=5, description="优先级"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Document)
    if kb_id:
        stmt = stmt.where(Document.kb_id == kb_id)
    if name:
        stmt = stmt.where(Document.name.ilike(f"%{name}%"))
    if doc_type:
        stmt = stmt.where(Document.doc_type == doc_type)
    if priority:
        stmt = stmt.where(Document.priority == priority)

    stmt = stmt.order_by(Document.updated_at.desc()).offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(stmt)
    return res.scalars().all()

@router.get("/{doc_id}", summary="获取文档详情及切片信息")
async def get_document_detail(doc_id: str, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    stmt_chunks = select(DocumentChunk).where(DocumentChunk.doc_id == doc_id).order_by(DocumentChunk.chunk_index.asc())
    c_res = await db.execute(stmt_chunks)
    chunks = c_res.scalars().all()

    return {
        "doc": DocumentResponse.model_validate(doc),
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

@router.put("/{doc_id}", response_model=DocumentResponse, summary="编辑文档（版本自动递增并归档快照，重新切片构建索引）")
async def update_document(
    doc_id: str, 
    payload: DocumentUpdate, 
    db: AsyncSession = Depends(get_db)
):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 若更新了内容，执行版本自增与切片重建
    if payload.content is not None and payload.content != doc.content:
        # 1. 查询当前文档已归档的最大版本号，确保严格单调递增，杜绝版本号冲突
        max_ver_stmt = select(func.max(DocumentVersion.version)).where(DocumentVersion.doc_id == doc.id)
        max_v = (await db.execute(max_ver_stmt)).scalar()
        if max_v is None or max_v < 1:
            max_v = doc.version or 1
            # 兼容：若之前没有归档初始版本，先归档当前原始内容作为快照
            init_v = DocumentVersion(
                doc_id=doc.id,
                version=max_v,
                content=doc.content or "",
                metadata_snapshot={"name": doc.name, "doc_type": doc.doc_type, "priority": doc.priority},
                created_by=doc.created_by or "system"
            )
            db.add(init_v)

        # 2. 递增当前主版本号并更新正文
        next_version = max(max_v, doc.version) + 1
        doc.version = next_version
        doc.content = payload.content
        doc.file_size = len(payload.content.encode("utf-8"))
        if payload.updated_by:
            doc.updated_by = payload.updated_by

        # 3. 归档新版本的完整独立快照
        new_version_snapshot = DocumentVersion(
            doc_id=doc.id,
            version=next_version,
            content=payload.content,
            metadata_snapshot={"name": payload.name or doc.name, "doc_type": doc.doc_type, "priority": payload.priority or doc.priority},
            created_by=payload.updated_by or doc.updated_by or "admin"
        )
        db.add(new_version_snapshot)

        # 4. 统计旧切片数量以维护知识库统计计数
        old_count_stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.doc_id == doc.id)
        old_c_count = (await db.execute(old_count_stmt)).scalar() or 0

        # 彻底删除旧切片并重建
        old_chunks_del = delete(DocumentChunk).where(DocumentChunk.doc_id == doc.id)
        await db.execute(old_chunks_del)

        # 5. 根据类型重新生成切片
        doc_info = {"id": doc.id, "name": payload.name or doc.name, "priority": payload.priority or doc.priority}
        if doc.doc_type == "MD":
            new_chunks = DocumentChunker.chunk_markdown(payload.content, doc_info)
        elif doc.doc_type == "TXT":
            new_chunks = DocumentChunker.chunk_txt(payload.content, doc_info)
        else:
            new_chunks = []

        for nc in new_chunks:
            emb = await model_service.get_embedding(nc.content)
            chunk_model = DocumentChunk(
                kb_id=doc.kb_id,
                doc_id=doc.id,
                chunk_index=nc.chunk_index,
                content=nc.content,
                embedding=emb,
                heading_path=nc.heading_path,
                priority=doc.priority,
                metadata_info=nc.metadata
            )
            db.add(chunk_model)

        # 维护知识库切片统计
        kb = await db.get(KnowledgeBase, doc.kb_id)
        if kb:
            kb.chunk_count = max(0, kb.chunk_count - old_c_count + len(new_chunks))

    if payload.name is not None:
        doc.name = payload.name
    if payload.priority is not None:
        doc.priority = payload.priority
    if payload.updated_by is not None:
        doc.updated_by = payload.updated_by

    await db.commit()
    await db.refresh(doc)
    return doc

@router.get("/{doc_id}/versions", response_model=List[DocumentVersionResponse], summary="查询文档所有历史版本")
async def list_document_versions(doc_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(DocumentVersion).where(DocumentVersion.doc_id == doc_id).order_by(DocumentVersion.version.desc(), DocumentVersion.created_at.desc())
    res = await db.execute(stmt)
    versions = res.scalars().all()
    # 内存去重，确保每个版本号只返回最新一条
    seen = set()
    unique_versions = []
    for v in versions:
        if v.version not in seen:
            seen.add(v.version)
            unique_versions.append(v)
    return unique_versions

@router.post("/{doc_id}/rollback", summary="版本回滚（恢复指定历史版本并重新切片）")
async def rollback_document_version(
    doc_id: str, 
    target_version: int = Query(..., description="目标恢复版本号"),
    updated_by: Optional[str] = Query("admin", description="操作人"),
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

    # 执行回滚：恢复历史版本内容并记录回滚人
    return await update_document(
        doc_id=doc_id,
        payload=DocumentUpdate(content=ver_snapshot.content, updated_by=updated_by),
        db=db
    )

@router.delete("/{doc_id}", summary="删除文档（级联清除所有切片与版本）")
async def delete_document(doc_id: str, db: AsyncSession = Depends(get_db)):
    doc = await db.get(Document, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    kb = await db.get(KnowledgeBase, doc.kb_id)
    
    # 统计待删除切片数量以维护知识库统计计数
    chunk_count_stmt = select(func.count(DocumentChunk.id)).where(DocumentChunk.doc_id == doc.id)
    c_count = (await db.execute(chunk_count_stmt)).scalar() or 0

    await db.delete(doc)

    if kb:
        kb.doc_count = max(0, kb.doc_count - 1)
        kb.chunk_count = max(0, kb.chunk_count - c_count)

    await db.commit()
    return {"message": "文档及切片数据已成功级联删除", "doc_id": doc_id}
