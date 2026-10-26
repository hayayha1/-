import math
import numpy as np
from typing import List, Dict, Any, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import jieba
from rank_bm25 import BM25Okapi

from app.models.chunk import DocumentChunk
from app.services.model_service import model_service
from app.core.config import settings

class HybridRetrievalEngine:
    """BM25 + 向量检索混合引擎与融合重排器"""

    @staticmethod
    async def retrieve_and_rerank(
        db: AsyncSession,
        kb_id: str,
        raw_query: str,
        rewritten_query: str,
        bm25_top_k: int = 15,
        vector_top_k: int = 15,
        rerank_top_k: int = 3
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        全流程检索：
        1. 原始Query + 改写Query -> BM25检索
        2. 原始Query + 改写Query -> 向量检索
        3. RRF融合打分 + 优先级加权
        4. Cross-Encoder Rerank
        返回: (reranked_chunks, top_score)
        """
        # 1. 查询当前知识库所有可用 chunk
        stmt = select(DocumentChunk).where(DocumentChunk.kb_id == kb_id)
        result = await db.execute(stmt)
        chunks: List[DocumentChunk] = result.scalars().all()

        if not chunks:
            return [], 0.0

        chunk_dict = {c.id: c for c in chunks}
        chunk_texts = [c.content for c in chunks]
        chunk_ids = [c.id for c in chunks]

        # 2. 构建内存 BM25 索引
        tokenized_corpus = [list(jieba.cut(t.lower())) for t in chunk_texts]
        bm25 = BM25Okapi(tokenized_corpus)

        # 2.1 BM25 检索（原始 + 改写）
        bm25_raw_ranks = HybridRetrievalEngine._run_bm25(bm25, chunk_ids, raw_query, bm25_top_k)
        bm25_rewritten_ranks = HybridRetrievalEngine._run_bm25(bm25, chunk_ids, rewritten_query, bm25_top_k)

        # 2.2 向量检索（原始 + 改写）
        raw_emb = await model_service.get_embedding(raw_query)
        rewritten_emb = await model_service.get_embedding(rewritten_query)

        vec_raw_ranks = await HybridRetrievalEngine._run_vector_search(chunks, raw_emb, vector_top_k)
        vec_rewritten_ranks = await HybridRetrievalEngine._run_vector_search(chunks, rewritten_emb, vector_top_k)

        # 3. RRF 倒数排名融合打分 (加权融合 + 知识优先级)
        # 赋予改写问句更高的召回信任度权重
        rank_lists = [
            (bm25_raw_ranks, 0.4),
            (bm25_rewritten_ranks, 0.6),
            (vec_raw_ranks, 0.5),
            (vec_rewritten_ranks, 0.8),
        ]

        rrf_scores: Dict[str, float] = {}
        k_param = 60.0

        for r_list, weight in rank_lists:
            for rank_idx, cid in enumerate(r_list):
                rrf_scores[cid] = rrf_scores.get(cid, 0.0) + (weight / (k_param + rank_idx + 1))

        # 结合知识优先级进行加权增益
        # Priority 1: 1.0x, Priority 5: 1.6x
        candidate_scores: List[Tuple[str, float]] = []
        for cid, rrf in rrf_scores.items():
            chunk_obj = chunk_dict[cid]
            prio = getattr(chunk_obj, "priority", 1) or 1
            priority_multiplier = 1.0 + (prio - 1) * 0.15
            candidate_scores.append((cid, rrf * priority_multiplier))

        # 排序获取前 15 个候选项进入 Reranker
        candidate_scores.sort(key=lambda x: x[1], reverse=True)
        top_candidates = candidate_scores[:15]

        if not top_candidates:
            return [], 0.0

        cand_chunks = [chunk_dict[cid] for cid, _ in top_candidates]
        cand_texts = [c.content for c in cand_chunks]

        # 4. Cross-Encoder Rerank 精排打分
        rerank_scores = await model_service.rerank(rewritten_query, cand_texts)

        # 组装返回结果
        combined_results = []
        for c, score in zip(cand_chunks, rerank_scores):
            meta = c.metadata_info or {}
            combined_results.append({
                "chunk_id": c.id,
                "doc_id": c.doc_id,
                "doc_name": meta.get("doc_name", "未知文档"),
                "heading_path": c.heading_path,
                "content": c.content,
                "priority": c.priority,
                "score": float(score),
                "metadata": meta
            })

        # 按 Rerank 得分从高到低排序
        combined_results.sort(key=lambda x: x["score"], reverse=True)
        
        top_score = combined_results[0]["score"] if combined_results else 0.0
        final_selected = combined_results[:rerank_top_k]

        return final_selected, top_score

    @staticmethod
    def _run_bm25(bm25: BM25Okapi, chunk_ids: List[str], query: str, top_k: int) -> List[str]:
        q_tokens = list(jieba.cut(query.lower()))
        scores = bm25.get_scores(q_tokens)
        top_indices = np.argsort(scores)[::-1][:top_k]
        return [chunk_ids[i] for i in top_indices if scores[i] > 0]

    @staticmethod
    async def _run_vector_search(chunks: List[DocumentChunk], query_emb: List[float], top_k: int) -> List[str]:
        q_vec = np.array(query_emb, dtype=np.float32)
        q_norm = np.linalg.norm(q_vec)
        if q_norm == 0:
            return [c.id for c in chunks[:top_k]]

        scored = []
        for c in chunks:
            c_emb = c.embedding
            if not c_emb:
                continue
            if isinstance(c_emb, str):
                import json
                try:
                    c_emb = json.loads(c_emb)
                except Exception:
                    continue
            c_vec = np.array(c_emb, dtype=np.float32)
            c_norm = np.linalg.norm(c_vec)
            if c_norm == 0:
                continue
            cosine = float(np.dot(q_vec, c_vec) / (q_norm * c_norm))
            scored.append((c.id, cosine))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [cid for cid, _ in scored[:top_k]]

retrieval_engine = HybridRetrievalEngine()
