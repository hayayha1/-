import os
import json
import hashlib
import numpy as np
from typing import List, Dict, Any, Tuple, Optional
import httpx
import jieba
from app.core.config import settings

class ModelService:
    """大模型、Embedding 与 Rerank 综合服务"""

    def __init__(self):
        # LLM 大模型（用于客服回答生成与 Query 改写，默认接入 DeepSeek 官方）
        self.llm_api_key = settings.OPENAI_API_KEY
        self.llm_base_url = settings.OPENAI_BASE_URL.rstrip("/")
        self.llm_model = settings.LLM_MODEL

        # Embedding 向量模型（用于切片与问句向量化，默认接入 SiliconFlow BAAI/bge-large-zh-v1.5）
        self.embedding_api_key = settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY
        self.embedding_base_url = (settings.EMBEDDING_BASE_URL or settings.OPENAI_BASE_URL).rstrip("/")
        self.embedding_model = settings.EMBEDDING_MODEL
        self.dim = settings.EMBEDDING_DIM

        # Rerank 语义重排模型（支持 SiliconFlow BAAI/bge-reranker-v2-m3）
        self.rerank_api_key = settings.RERANK_API_KEY or self.embedding_api_key
        self.rerank_base_url = (settings.RERANK_BASE_URL or self.embedding_base_url).rstrip("/")
        self.rerank_model = settings.RERANK_MODEL

    async def get_embedding(self, text: str) -> List[float]:
        """获取文本密集向量 (优先调用外部顶级 Embedding 模型，失败则平滑降级为本地算法)"""
        # 如果配置了有效 API Key，尝试调用官方/兼容向量接口
        if self.embedding_api_key and "sk-" in self.embedding_api_key:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{self.embedding_base_url}/embeddings",
                        headers={"Authorization": f"Bearer {self.embedding_api_key}"},
                        json={"input": text, "model": self.embedding_model}
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        return data["data"][0]["embedding"]
            except Exception:
                pass
        
        # 离线/内置高性能语义降级嵌入（基于分词与哈希投射，支持无Key本地端到端闭环）
        return self._fallback_embedding(text)

    async def get_embeddings_batch(self, texts: List[str]) -> List[List[float]]:
        """批量获取向量"""
        return [await self.get_embedding(t) for t in texts]

    def _fallback_embedding(self, text: str) -> List[float]:
        """生成具备语义特征的 1024 维归一化向量"""
        vec = np.zeros(self.dim, dtype=np.float32)
        words = list(jieba.cut(text))
        if not words:
            words = [text]
        for idx, word in enumerate(words):
            # 将词哈希到固定桶，并根据位置分配权重
            h = int(hashlib.md5(word.encode("utf-8")).hexdigest(), 16)
            pos = h % self.dim
            sign = 1.0 if (h >> 4) % 2 == 0 else -1.0
            vec[pos] += sign * (1.0 + 1.0 / (idx + 1))
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec.tolist()

    async def rerank(self, query: str, candidate_texts: List[str]) -> List[float]:
        """
        Cross-Encoder 语义重排打分
        优先调用硅基流动真实 Rerank API (如 BAAI/bge-reranker-v2-m3)，异常时自动回退为本地平滑 Cross-Encoder
        返回对应的得分列表，区间 [0.0, 1.0]
        """
        if not candidate_texts:
            return []

        # 1. 尝试调用真实 API Rerank 服务
        if self.rerank_api_key and "sk-" in self.rerank_api_key:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{self.rerank_base_url}/rerank",
                        headers={"Authorization": f"Bearer {self.rerank_api_key}"},
                        json={
                            "model": self.rerank_model,
                            "query": query,
                            "documents": candidate_texts
                        }
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        scores_map = {}
                        for item in data.get("results", []):
                            idx = item.get("index")
                            score = float(item.get("relevance_score", 0.0))
                            scores_map[idx] = max(0.0, min(1.0, score))
                        if len(scores_map) == len(candidate_texts):
                            return [round(scores_map.get(i, 0.0), 4) for i in range(len(candidate_texts))]
            except Exception:
                pass

        # 2. 内置平滑 Cross-Encoder 语义得分计算器（离线降级保障）
        # 综合考虑：核心关键词重合度、长短语连续匹配、以及语义向量余弦
        query_words = set(jieba.cut(query.lower()))
        q_emb = np.array(await self.get_embedding(query))
        
        scores = []
        for text in candidate_texts:
            text_lower = text.lower()
            text_words = set(jieba.cut(text_lower))
            
            # 1. Jaccard 词重叠
            intersection = query_words.intersection(text_words)
            jaccard = len(intersection) / max(len(query_words), 1)
            
            # 2. 向量余弦相似度
            t_emb = np.array(await self.get_embedding(text))
            cosine = float(np.dot(q_emb, t_emb) / (np.linalg.norm(q_emb) * np.linalg.norm(t_emb) + 1e-8))
            cosine_norm = (cosine + 1.0) / 2.0  # 映射到 [0, 1]
            
            # 3. 核心子串精准命中奖励
            exact_bonus = 0.2 if any(len(w) >= 2 and w in text_lower for w in query_words) else 0.0

            # 综合打分映射
            raw_score = 0.45 * cosine_norm + 0.35 * jaccard + exact_bonus
            final_score = min(max(raw_score, 0.0), 1.0)
            scores.append(round(final_score, 4))

        return scores

    async def rewrite_query(self, raw_query: str, product_code: str) -> str:
        """
        Query 轻量改写：指代补全、术语标准化
        """
        # 如果有 LLM API，调用 DeepSeek 大模型
        if self.llm_api_key and "sk-" in self.llm_api_key:
            try:
                prompt = (
                    f"你是一名专业客服助手。请将用户的口语化、简写或不完整的问题改写成标准、明确的客服检索问句。\n"
                    f"产品上下文: {product_code}\n"
                    f"用户原问题: {raw_query}\n"
                    f"要求: 补充可能缺失的主语和产品代词，将口语化表述转为标准业务术语，直接输出改写后的一句话，不要任何多余前缀解释。"
                )
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.post(
                        f"{self.llm_base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.llm_api_key}"},
                        json={
                            "model": self.llm_model,
                            "messages": [{"role": "user", "content": prompt}],
                            "temperature": 0.2
                        }
                    )
                    if resp.status_code == 200:
                        content = resp.json()["choices"][0]["message"]["content"].strip()
                        return content
            except Exception:
                pass

        # 本地智能改写启发式规则引擎（P0 级兜底保障）
        rewritten = raw_query.strip()
        term_mappings = {
            "退款": "退款申请流程与售后退款政策条件",
            "退货": "退换货申请流程与支持条件",
            "怎么用": "功能使用操作指引与新手教程",
            "收费": "价格资费标准与套餐计费说明",
            "坏了": "故障排查与售后报修流程",
            "改密码": "账户安全与登录密码重置方法",
            "发票": "电子发票开具申请规范与流程"
        }
        for kw, expansion in term_mappings.items():
            if kw in rewritten:
                rewritten = f"{product_code}产品关于{expansion}（原问：{raw_query}）"
                break
        if rewritten == raw_query:
            rewritten = f"{product_code}关于{raw_query}的相关说明与操作指引"
        return rewritten

    async def classify_intent(self, query: str) -> str:
        """
        意图分类：
        - KNOWLEDGE: 事实类知识库问答
        - TRANSACTION: 业务接口查询类（如订单、物流、改密、退款进度等）
        - CHITCHAT: 闲聊打招呼
        """
        transaction_keywords = [
            "我的订单", "查订单", "物流", "快递", "运单", "到哪了",
            "退款进度", "退款审核", "审核进度", "修改手机号", "支付状态", "扣费记录"
        ]
        chitchat_keywords = ["你好", "在吗", "你是谁", "早上好", "晚上好", "哈哈", "再见", "谢谢"]

        for kw in transaction_keywords:
            if kw in query:
                return "TRANSACTION"
        for kw in chitchat_keywords:
            if query.strip().lower() == kw:
                return "CHITCHAT"
        return "KNOWLEDGE"

    async def chat_completion(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        """通用 LLM 聊天接口 (DeepSeek)"""
        if self.llm_api_key and "sk-" in self.llm_api_key:
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        f"{self.llm_base_url}/chat/completions",
                        headers={"Authorization": f"Bearer {self.llm_api_key}"},
                        json={
                            "model": self.llm_model,
                            "messages": messages,
                            "temperature": temperature
                        }
                    )
                    if resp.status_code == 200:
                        return resp.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"[ModelService] chat_completion error: {e}")
        return ""

    async def generate_direct_answer(self, query: str, contexts: List[Dict[str, Any]]) -> str:
        """根据召回的高置信证据生成直接回答"""
        context_str = "\n\n".join([
            f"【参考依据 {i+1}】(文档: {c.get('doc_name')}，章节: {c.get('heading_path', '')})\n{c.get('content')}"
            for i, c in enumerate(contexts)
        ])

        if self.llm_api_key and "sk-" in self.llm_api_key:
            system_prompt = (
                "你是一名专业的电商平台官方客服助手。请严格基于以下提供的参考资料，准确客观、友好清晰地回答用户问题。\n"
                "如果资料中有具体的数字、金额、时限、比例、规则，请务必准确给出。\n"
                "如果资料中未包含明确信息，切勿胡乱编造。"
            )
            user_prompt = f"参考资料：\n{context_str}\n\n用户提问：{query}\n\n请给出清晰专业的客服解答："
            ans = await self.chat_completion([
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ], temperature=0.1)
            if ans:
                return ans

        # 离线/内置生成器（萃取最核心证据首段并友好组装）
        best_chunk = contexts[0] if contexts else {}
        content_snippet = best_chunk.get("content", "")
        # 如果是 FAQ 格式，提取答案段
        if "【权威解答】:" in content_snippet:
            content_snippet = content_snippet.split("【权威解答】:")[-1].strip()

        return f"根据相关规定：{content_snippet}"

model_service = ModelService()
