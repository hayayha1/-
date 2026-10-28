import re
from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, END
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.model_service import model_service
from app.services.retrieval.engine import retrieval_engine
from app.services.business_service import business_service

class ChatWorkflowState(TypedDict):
    db: AsyncSession
    kb_id: str
    product_code: str
    customer_id: str
    raw_query: str
    cleaned_query: str
    rewritten_query: str
    is_sensitive: bool
    intent_category: str
    kb_config: Dict[str, Any]
    reranked_chunks: List[Dict[str, Any]]
    top_score: float
    reply_type: str
    final_content: str
    sources: List[Dict[str, Any]]
    suggested_questions: List[Dict[str, Any]]
    business_data: Optional[Dict[str, Any]]

# ================= 节点函数定义 =================

async def clean_and_filter_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """节点 1：文本清洗与敏感词拦截"""
    raw = state["raw_query"]
    # 清洗特殊控制字符与无关符号
    cleaned = re.sub(r"[\r\n\t]+", " ", raw).strip()
    
    # 敏感词匹配
    is_sensitive = False
    for word in settings.SENSITIVE_WORDS:
        if word in cleaned:
            is_sensitive = True
            break

    return {
        "cleaned_query": cleaned,
        "is_sensitive": is_sensitive
    }

async def block_reply_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """敏感词拦截阻断节点"""
    return {
        "reply_type": "BLOCKED",
        "top_score": 0.0,
        "final_content": "抱歉，您的问题包含敏感或不合规内容，已被系统安全机制拦截，暂无法为您提供解答。如有疑问请规范提问或联系人工客服。",
        "sources": [],
        "suggested_questions": []
    }

async def intent_classifier_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """节点 2：意图分类识别"""
    intent = await model_service.classify_intent(state["cleaned_query"])
    return {"intent_category": intent}

async def transaction_reply_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """业务接口类真实业务查询节点"""
    biz_result = await business_service.handle_transaction_query(
        customer_id=state["customer_id"],
        query=state["cleaned_query"]
    )
    return {
        "reply_type": "TRANSACTION",
        "top_score": 1.0,
        "final_content": f"{biz_result.get('summary')} 您可直接在下方业务卡片中查看详情或进行后续操作：",
        "sources": [],
        "suggested_questions": [
            {"question_id": "tx_01", "title": "退款几天到账"},
            {"question_id": "tx_02", "title": "发票怎么开具"}
        ],
        "business_data": biz_result
    }

async def chitchat_reply_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """通用闲聊节点"""
    return {
        "reply_type": "CHITCHAT",
        "top_score": 1.0,
        "final_content": "您好！我是您的智能客服助手，请问有什么可以帮助您的？您可以向我咨询产品使用、服务政策或退换货流程等问题。",
        "sources": [],
        "suggested_questions": []
    }

async def query_rewrite_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """节点 3：LLM 轻量改写（标准化指代与专业术语）"""
    rewritten = await model_service.rewrite_query(
        state["cleaned_query"], 
        state["product_code"]
    )
    return {"rewritten_query": rewritten}

async def hybrid_retrieve_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """节点 4：两路送入 BM25 + 向量检索混合召回与 Rerank"""
    cfg = state["kb_config"]
    bm25_top_k = cfg.get("bm25_top_k", 15)
    vector_top_k = cfg.get("vector_top_k", 15)
    rerank_top_k = cfg.get("rerank_top_k", 3)

    chunks, top_score = await retrieval_engine.retrieve_and_rerank(
        db=state["db"],
        kb_id=state["kb_id"],
        raw_query=state["cleaned_query"],
        rewritten_query=state["rewritten_query"],
        bm25_top_k=bm25_top_k,
        vector_top_k=vector_top_k,
        rerank_top_k=rerank_top_k
    )

    return {
        "reranked_chunks": chunks,
        "top_score": top_score
    }

async def direct_answer_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """高置信度 (>=0.80) 节点：基于召回证据生成专业回答，附带来源出处"""
    chunks = state["reranked_chunks"]
    answer = await model_service.generate_direct_answer(state["cleaned_query"], chunks)
    
    # 提炼溯源证据
    sources = []
    for c in chunks:
        meta = c.get("metadata", {})
        snippet = c.get("content", "")
        # 裁剪摘要长度
        snippet_summary = snippet[:120] + "..." if len(snippet) > 120 else snippet
        sources.append({
            "doc_id": c.get("doc_id"),
            "doc_name": c.get("doc_name"),
            "heading": c.get("heading_path") or "正文段落",
            "chunk_id": c.get("chunk_id"),
            "snippet": snippet_summary,
            "priority": c.get("priority", 1)
        })

    return {
        "reply_type": "DIRECT_ANSWER",
        "final_content": answer,
        "sources": sources,
        "suggested_questions": []
    }

async def suggest_clarify_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """中置信度 (0.50~0.80) 节点：推荐相似/相关问题"""
    chunks = state["reranked_chunks"]
    suggested = []
    for idx, c in enumerate(chunks[:3]):
        meta = c.get("metadata", {})
        title = meta.get("standard_question")
        if not title:
            # 取段落第一句话或标题
            first_line = c.get("content", "").split("\n")[0].replace("#", "").strip()
            title = first_line[:30] if first_line else f"相关参考内容 {idx+1}"
        suggested.append({
            "question_id": f"q_{idx+1}",
            "title": title
        })

    return {
        "reply_type": "RECOMMEND_QUESTIONS",
        "final_content": "您是不是想咨询以下问题？您可以直接点击下方卡片快速查看详细解答：",
        "sources": [],
        "suggested_questions": suggested
    }

async def fallback_node(state: ChatWorkflowState) -> Dict[str, Any]:
    """低置信度 (<0.50) 节点：兜底话术"""
    return {
        "reply_type": "FALLBACK",
        "final_content": "抱歉，暂时没有检索到与您的问题高度匹配的知识库内容。您可以尝试简化问法、补充关键词，或者点击下方按钮直接转接人工客服为您处理。",
        "sources": [],
        "suggested_questions": []
    }

# ================= 状态图构建 =================

def create_customer_service_graph():
    graph = StateGraph(ChatWorkflowState)

    # 注册节点
    graph.add_node("clean_and_filter", clean_and_filter_node)
    graph.add_node("block_reply", block_reply_node)
    graph.add_node("intent_classifier", intent_classifier_node)
    graph.add_node("transaction_reply", transaction_reply_node)
    graph.add_node("chitchat_reply", chitchat_reply_node)
    graph.add_node("query_rewrite", query_rewrite_node)
    graph.add_node("hybrid_retrieve", hybrid_retrieve_node)
    graph.add_node("direct_answer", direct_answer_node)
    graph.add_node("suggest_clarify", suggest_clarify_node)
    graph.add_node("fallback", fallback_node)

    # 1. 入口与安全检查
    graph.set_entry_point("clean_and_filter")

    def route_safety(state: ChatWorkflowState) -> str:
        return "block_reply" if state["is_sensitive"] else "intent_classifier"

    graph.add_conditional_edges(
        "clean_and_filter",
        route_safety,
        {
            "block_reply": "block_reply",
            "intent_classifier": "intent_classifier"
        }
    )

    # 2. 意图分类路由
    def route_intent(state: ChatWorkflowState) -> str:
        cat = state["intent_category"]
        if cat == "TRANSACTION":
            return "transaction_reply"
        elif cat == "CHITCHAT":
            return "chitchat_reply"
        return "query_rewrite"

    graph.add_conditional_edges(
        "intent_classifier",
        route_intent,
        {
            "transaction_reply": "transaction_reply",
            "chitchat_reply": "chitchat_reply",
            "query_rewrite": "query_rewrite"
        }
    )

    # 3. 改写后直接检索
    graph.add_edge("query_rewrite", "hybrid_retrieve")

    # 4. 检索得分三层决策路由
    def route_decision(state: ChatWorkflowState) -> str:
        score = state["top_score"]
        cfg = state["kb_config"]
        th_direct = cfg.get("threshold_direct", 0.80)
        th_clarify = cfg.get("threshold_clarify", 0.50)

        if score >= th_direct:
            return "direct_answer"
        elif score >= th_clarify:
            return "suggest_clarify"
        else:
            return "fallback"

    graph.add_conditional_edges(
        "hybrid_retrieve",
        route_decision,
        {
            "direct_answer": "direct_answer",
            "suggest_clarify": "suggest_clarify",
            "fallback": "fallback"
        }
    )

    # 5. 终端汇聚
    graph.add_edge("block_reply", END)
    graph.add_edge("transaction_reply", END)
    graph.add_edge("chitchat_reply", END)
    graph.add_edge("direct_answer", END)
    graph.add_edge("suggest_clarify", END)
    graph.add_edge("fallback", END)

    return graph.compile()

# 升级为新一代 Agent 智能体全流程状态图编排
from app.workflow.agent_customer_service_graph import agent_customer_service_graph
customer_service_graph = agent_customer_service_graph
