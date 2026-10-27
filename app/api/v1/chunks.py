from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.db.session import get_db
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.knowledge_base import KnowledgeBase
from app.services.retrieval.engine import retrieval_engine
from app.services.model_service import model_service
from app.core.auth import get_current_user, CurrentUser, require_roles, ROLE_ADMIN, ROLE_TECH_AGENT, ROLE_OPERATOR

# 检索切片明细流对 超级管理员、技术客服与运营 开放
router = APIRouter(
    prefix="/chunks", 
    tags=["知识片段管理"],
    dependencies=[Depends(require_roles([ROLE_ADMIN, ROLE_TECH_AGENT, ROLE_OPERATOR]))]
)

@router.get("", summary="知识片段列表分页与条件检索")
async def list_chunks(
    kb_id: Optional[str] = Query(None, description="知识库ID"),
    doc_id: Optional[str] = Query(None, description="文档ID"),
    keyword: Optional[str] = Query(None, description="切片文本内容搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(DocumentChunk, Document.name.label("doc_name"), Document.doc_type).outerjoin(
        Document, DocumentChunk.doc_id == Document.id
    )

    # 运营数据权限隔离
    if current_user.role == ROLE_OPERATOR:
        assigned_kb = current_user.assigned_kb_id or "b34fb60a-392c-4bb2-9e10-57539b37fed2"
        stmt = stmt.where(DocumentChunk.kb_id == assigned_kb)
    elif doc_id:
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

class ChunkSearchRequest(BaseModel):
    query: str = Field(..., description="用户测试问句", example="买家签收后申请7天无理由退货，这7天是从什么时候开始计算的？")
    kb_id: Optional[str] = Field(None, description="知识库ID（选填，为空或保留Swagger默认'string'时自动选用当前启用知识库）")
    top_k: int = Field(3, ge=1, le=20, description="返回候选切片数量，默认3（或5）")

@router.post("/search", summary="纯检索器测试：输入问句获取 Top-3/Top-5 文本块 (不调用大模型)")
async def search_chunks_only(
    payload: ChunkSearchRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    纯检索器能力测试接口：
    1. Query 语义改写 (术语与指代补全)
    2. BM25 词法检索 (Jieba分词)
    3. Dense Vector 密集语义向量检索 (SiliconFlow 1024维)
    4. 加权 RRF 融合打分 + 优先级加权
    5. Cross-Encoder 精排重排 (SiliconFlow BGE-Reranker)
    仅返回最终 Top-K 文本块及得分，不调用大模型生成答案。
    """
    # 智能识别有效知识库（容错 Swagger 默认的 'string' 占位符或空值）
    kb = None
    cleaned_kb_id = (payload.kb_id or "").strip()
    if cleaned_kb_id and cleaned_kb_id.lower() not in ["string", "null", "undefined", "none", ""]:
        kb = await db.get(KnowledgeBase, cleaned_kb_id)

    if not kb:
        # 默认使用系统当前处于 ACTIVE 状态的知识库
        res = await db.execute(select(KnowledgeBase).where(KnowledgeBase.status == "ACTIVE"))
        kb = res.scalars().first()
        if not kb:
            # 降级：如果无 ACTIVE，尝试取第一个知识库
            res_all = await db.execute(select(KnowledgeBase))
            kb = res_all.scalars().first()
            if not kb:
                raise HTTPException(status_code=404, detail="未找到任何知识库，请先创建知识库并上传文档")

    target_kb_id = kb.id

    rewritten_query = await model_service.rewrite_query(payload.query, "电商平台")
    chunks, top_score = await retrieval_engine.retrieve_and_rerank(
        db=db,
        kb_id=target_kb_id,
        raw_query=payload.query,
        rewritten_query=rewritten_query,
        bm25_top_k=15,
        vector_top_k=15,
        rerank_top_k=payload.top_k
    )

    # 提取末级章节作为精确 source_section（对齐评测集标注格式）
    def extract_source_section(heading_path: str) -> str:
        if not heading_path:
            return "正文"
        parts = [p.strip() for p in heading_path.split(" > ") if p.strip()]
        return parts[-1] if parts else heading_path

    # 对照评测集自动核对 HitRate 状态（如匹配到 TEST 用例）
    import json, os
    benchmark_info = None
    dataset_path = os.path.join("data", "evaluation", "test_dataset.json")
    if os.path.exists(dataset_path):
        try:
            with open(dataset_path, "r", encoding="utf-8") as f:
                test_ds = json.load(f)
            q_clean = payload.query.strip().lower()
            matched_case = next((item for item in test_ds if item["question"].strip().lower() == q_clean or item["question"] in payload.query or payload.query in item["question"]), None)
            if matched_case:
                tgt_doc = matched_case["source_doc"]
                tgt_sec = matched_case["source_section"]
                hit_rank = None
                for idx, c in enumerate(chunks):
                    c_doc = c["doc_name"]
                    c_sec = extract_source_section(c.get("heading_path") or "")
                    c_path = c.get("heading_path") or ""
                    # 匹配文档与章节
                    if (c_doc == tgt_doc or c_doc.startswith(tgt_doc.split(".")[0])) and (tgt_sec in c_path or c_sec in tgt_sec or tgt_sec[:4] in c_path):
                        hit_rank = idx + 1
                        break
                benchmark_info = {
                    "test_id": matched_case["id"],
                    "target_source_doc": tgt_doc,
                    "target_source_section": tgt_sec,
                    "hit_rank": hit_rank,
                    "is_hit_top3": hit_rank is not None and hit_rank <= 3,
                    "is_hit_top5": hit_rank is not None and hit_rank <= 5,
                    "status": f"HIT@{hit_rank} (Top-3 命中)" if hit_rank and hit_rank <= 3 else (f"HIT@{hit_rank}" if hit_rank else "未在Top-K召回")
                }
        except Exception:
            pass

    return {
        "query": payload.query,
        "rewritten_query": rewritten_query,
        "kb_id": target_kb_id,
        "kb_name": kb.name,
        "top_score": round(top_score, 4),
        "total_returned": len(chunks),
        "benchmark_evaluation": benchmark_info,
        "chunks": [
            {
                "rank": i + 1,
                "score": round(c["score"], 4),
                "source_doc": c["doc_name"],
                "source_section": extract_source_section(c.get("heading_path") or ""),
                "doc_name": c["doc_name"],
                "heading_path": c["heading_path"],
                "priority": c["priority"],
                "content": c["content"]
            }
            for i, c in enumerate(chunks)
        ]
    }
