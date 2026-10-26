from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.db.session import get_db
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.core.auth import require_roles, ROLE_ADMIN, ROLE_TECH_AGENT

# 检索切片明细流仅对 超级管理员 与 技术客服 开放
router = APIRouter(
    prefix="/chunks", 
    tags=["知识片段管理"],
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TECH_AGENT]))]
)

@router.get("", summary="知识片段列表分页与条件检索")
async def list_chunks(
    kb_id: Optional[str] = Query(None, description="知识库ID"),
    doc_id: Optional[str] = Query(None, description="文档ID"),
    keyword: Optional[str] = Query(None, description="切片文本内容搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(DocumentChunk, Document.name.label("doc_name"), Document.doc_type).outerjoin(
        Document, DocumentChunk.doc_id == Document.id
    )

    if doc_id:
        stmt = stmt.where(DocumentChunk.doc_id == doc_id)
    elif kb_id:
        stmt = stmt.where(DocumentChunk.kb_id == kb_id)
    if keyword:
        stmt = stmt.where(DocumentChunk.content.ilike(f"%{keyword}%"))

    # 总数统计
    count_stmt = select(func.count(DocumentChunk.id))
    if doc_id:
        count_stmt = count_stmt.where(DocumentChunk.doc_id == doc_id)
    elif kb_id:
        count_stmt = count_stmt.where(DocumentChunk.kb_id == kb_id)
    if keyword:
        count_stmt = count_stmt.where(DocumentChunk.content.ilike(f"%{keyword}%"))
    
    total = (await db.execute(count_stmt)).scalar() or 0

    stmt = stmt.order_by(desc(DocumentChunk.created_at)).offset((page - 1) * page_size).limit(page_size)
    results = (await db.execute(stmt)).all()

    items = []
    for chunk, doc_name, doc_type in results:
        items.append({
            "id": chunk.id,
            "kb_id": chunk.kb_id,
            "doc_id": chunk.doc_id,
            "doc_name": doc_name,
            "doc_type": doc_type,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content,
            "char_count": len(chunk.content),
            "heading_path": chunk.heading_path or "正文",
            "priority": chunk.priority,
            "has_embedding": chunk.embedding is not None,
            "metadata": chunk.metadata_info,
            "created_at": chunk.created_at
        })

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": items
    }

@router.get("/{chunk_id}", summary="查看指定切片完整详情")
async def get_chunk_detail(chunk_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(DocumentChunk).where(DocumentChunk.id == chunk_id)
    res = await db.execute(stmt)
    c = res.scalars().first()
    if not c:
        raise HTTPException(status_code=404, detail="切片不存在")
    
    doc = await db.get(Document, c.doc_id)

    return {
        "id": c.id,
        "kb_id": c.kb_id,
        "doc_id": c.doc_id,
        "doc_name": doc.name if doc else "未知",
        "doc_type": doc.doc_type if doc else "未知",
        "chunk_index": c.chunk_index,
        "content": c.content,
        "heading_path": c.heading_path,
        "priority": c.priority,
        "metadata": c.metadata_info,
        "created_at": c.created_at
    }
