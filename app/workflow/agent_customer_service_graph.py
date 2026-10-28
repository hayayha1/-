import re
from typing import TypedDict, List, Dict, Any, Optional
from langgraph.graph import StateGraph, END
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services.agent.memory import memory_hub
from app.services.agent.tools import action_tool_center
from app.services.agent.slot_engine import slot_engine
from app.services.agent.planner import goal_planner, TaskPlan
from app.services.agent.knowledge_tool import knowledge_tool
from app.services.agent.guardrail import action_guardrail
from app.services.model_service import model_service
from app.services.business_service import business_service

class AgentWorkflowState(TypedDict):
    db: AsyncSession
    kb_id: str
    product_code: str
    customer_id: str
    session_id: str
    raw_query: str
    cleaned_query: str
    rewritten_query: str
    is_sensitive: bool
    kb_config: Dict[str, Any]
    task_plan: Optional[Dict[str, Any]]
    intent_category: str
    primary_action: Optional[str]
    slots: Dict[str, Any]
    reply_type: str
    top_score: float
    final_content: str
    sources: List[Dict[str, Any]]
    suggested_questions: List[Dict[str, Any]]
    business_data: Optional[Dict[str, Any]]
    action_buttons: List[Dict[str, Any]]

# ================= 节点函数定义 =================

async def clean_and_safety_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 1：文本清洗与敏感词安全拦截"""
    raw = state["raw_query"]
    cleaned = re.sub(r"[\r\n\t]+", " ", raw).strip()

    is_sensitive = False
    for word in settings.SENSITIVE_WORDS:
        if word in cleaned:
            is_sensitive = True
            break

    return {
        "cleaned_query": cleaned,
        "is_sensitive": is_sensitive
    }

async def block_reply_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """敏感词拦截阻断节点"""
    return {
        "reply_type": "BLOCKED",
        "top_score": 0.0,
        "final_content": "抱歉，您的问题包含敏感或不合规内容，已被系统安全机制拦截，暂无法为您提供解答。如有疑问请规范提问或联系人工客服。",
        "sources": [],
        "suggested_questions": [],
        "business_data": None
    }

async def plan_and_route_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 2：目标理解、挂起确认研判与复合任务规划"""
    session_id = state.get("session_id", "default_sess")
    customer_id = state["customer_id"]
    cleaned_q = state["cleaned_query"]

    # 1. 目标规划中心拆解
    plan: TaskPlan = await goal_planner.analyze_and_plan(
        query=cleaned_q,
        customer_id=customer_id,
        session_id=session_id
    )

    # 2. 槽位抽取与预合并
    extracted_slots = slot_engine.extract_slots_from_text(
        query=cleaned_q,
        customer_id=customer_id,
        session_id=session_id,
        action_type=plan.primary_action or ""
    )
    current_slots = memory_hub.update_slots(session_id, extracted_slots)

    # 记录黑匣子思考步骤
    memory_hub.record_trace_step(session_id, "Thought", {
        "query": cleaned_q,
        "intent": plan.intent_category,
        "plan_summary": plan.summary,
        "slots": current_slots
    })

    return {
        "task_plan": plan.model_dump(),
        "intent_category": plan.intent_category,
        "primary_action": plan.primary_action,
        "slots": current_slots
    }

async def confirmation_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 3：高危写操作二次确认处理（执行已授权动作 或 安全取消）"""
    session_id = state.get("session_id", "default_sess")
    customer_id = state["customer_id"]
    action_type = state["primary_action"]

    pending = memory_hub.get_pending_action(session_id)
    if not pending:
        return {
            "reply_type": "TRANSACTION",
            "top_score": 1.0,
            "final_content": "当前暂无需要确认的待办业务。请问有什么可以帮助您的？",
            "business_data": None
        }

    # 用户明确确认：正式执行写接口
    if action_type == "EXECUTE_CONFIRMED":
        tool_name = pending["tool_name"]
        params = dict(pending.get("params", {}))
        handler = action_tool_center.handlers.get(tool_name)

        if handler:
            res = await handler(customer_id=customer_id, **params)
            memory_hub.clear_pending_action(session_id)
            memory_hub.clear_slots(session_id)
            return {
                "reply_type": "TRANSACTION",
                "top_score": 1.0,
                "final_content": f"已收到您的授权确认！{res.message}",
                "business_data": res.card_data
            }
        else:
            memory_hub.clear_pending_action(session_id)
            memory_hub.clear_slots(session_id)
            return {
                "reply_type": "TRANSACTION",
                "top_score": 1.0,
                "final_content": "业务执行异常：未找到对应的系统执行器。",
                "business_data": None
            }

    # 用户主动取消
    else:
        memory_hub.clear_pending_action(session_id)
        memory_hub.clear_slots(session_id)
        return {
            "reply_type": "TRANSACTION",
            "top_score": 1.0,
            "final_content": "已为您安全取消本次操作。所有参数均未提交，您的账户与订单数据保持不变。如需办理其他业务请随时告诉我。",
            "business_data": None
        }

async def escalation_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 4：转人工客服与上下文打包"""
    session_id = state.get("session_id", "default_sess")
    customer_id = state["customer_id"]

    escalation_pack = action_guardrail.build_escalation_package(
        session_id=session_id,
        customer_id=customer_id,
        reason="用户主动请求人工专员处理",
        plan_summary=state.get("task_plan", {}).get("summary", "")
    )

    return {
        "reply_type": "HUMAN_AGENT",
        "top_score": 1.0,
        "final_content": "为您正在转接人工客服专员... 已将您近期的订单及对话上下文自动同步给接单坐席，请稍候回复。",
        "sources": [],
        "suggested_questions": [],
        "business_data": None
    }

async def chitchat_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 5：智能亲和闲聊"""
    return {
        "reply_type": "CHITCHAT",
        "top_score": 1.0,
        "final_content": "您好！我是您的智能服务管家。我不仅可以为您解答产品服务政策，还可以直接为您查询订单、追踪物流、办理退换货或开具发票。请问有什么可以帮助您？",
        "sources": [],
        "suggested_questions": [],
        "business_data": None
    }

async def business_action_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 6：业务工具执行中心（含槽位反问、风控确认拦截与真实调用）"""
    session_id = state.get("session_id", "default_sess")
    customer_id = state["customer_id"]
    tool_name = state["primary_action"] or "query_orders"
    current_slots = state["slots"]

    # 1. 动态槽位完整度校验与反向追问
    validation = await slot_engine.validate_and_build_clarification(
        action_name=tool_name,
        current_slots=current_slots,
        customer_id=customer_id,
        session_id=session_id
    )

    if not validation.is_complete:
        # 槽位缺失，主动向用户精准追问（带选项卡片）
        return {
            "reply_type": "TRANSACTION",
            "top_score": 0.95,
            "final_content": validation.clarification_prompt,
            "business_data": validation.clarification_card,
            "sources": [],
            "suggested_questions": []
        }

    # 2. 高危写操作安全风控校验（生成二次确认卡片挂起）
    guard_dec = action_guardrail.check_write_risk(
        tool_name=tool_name,
        slots=current_slots,
        customer_id=customer_id,
        session_id=session_id
    )

    if guard_dec.needs_confirmation:
        return {
            "reply_type": "TRANSACTION",
            "top_score": 0.98,
            "final_content": guard_dec.content,
            "business_data": guard_dec.confirmation_card,
            "sources": [],
            "suggested_questions": []
        }

    # 3. 低危或只读操作，直接执行工具
    handler = action_tool_center.handlers.get(tool_name)
    if not handler:
        # 回退至通用订单查询
        handler = action_tool_center.query_orders

    exec_res = await handler(customer_id=customer_id, **current_slots)

    # 任务完成清理对应槽位
    if action_tool_center.get_tool(tool_name) and action_tool_center.get_tool(tool_name).action_type == "WRITE":
        memory_hub.clear_slots(session_id)

    return {
        "reply_type": "TRANSACTION",
        "top_score": 1.0,
        "final_content": exec_res.message,
        "business_data": exec_res.card_data,
        "sources": [],
        "suggested_questions": []
    }

async def composite_task_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 7：复合任务协同执行中心（多步骤串行协同与结果融合）"""
    session_id = state.get("session_id", "default_sess")
    customer_id = state["customer_id"]
    plan = state.get("task_plan", {})
    subtasks = plan.get("subtasks", [])

    results_summary = []
    combined_card = None

    for st in subtasks:
        ttype = st.get("task_type")
        tname = st.get("tool_name")

        if ttype in ["READ_TOOL", "WRITE_TOOL"] and tname:
            handler = action_tool_center.handlers.get(tname)
            if handler:
                slots = state.get("slots", {})
                res = await handler(customer_id=customer_id, **slots)
                results_summary.append(f"【{st.get('description')}】：{res.message}")
                if res.card_data:
                    combined_card = res.card_data

        elif ttype == "KNOWLEDGE_QA":
            # 知识库协同
            kb_res = await knowledge_tool.execute_retrieval(
                db=state["db"],
                kb_id=state["kb_id"],
                raw_query=state["cleaned_query"],
                rewritten_query=state.get("rewritten_query") or state["cleaned_query"],
                kb_config=state.get("kb_config", {})
            )
            results_summary.append(f"【政策说明】：{kb_res.content}")

    final_text = "已为您协同完成以下事项：\n\n" + "\n\n".join(results_summary)
    return {
        "reply_type": "TRANSACTION",
        "top_score": 1.0,
        "final_content": final_text,
        "business_data": combined_card,
        "sources": [],
        "suggested_questions": []
    }

async def knowledge_retrieval_node(state: AgentWorkflowState) -> Dict[str, Any]:
    """节点 8：知识协同工具（RAG 降维接入）"""
    session_id = state.get("session_id", "default_sess")
    customer_id = state["customer_id"]

    # 轻量 Query 改写
    rewritten = await model_service.rewrite_query(
        state["cleaned_query"],
        state["product_code"]
    )

    # 查阅用户最新订单作为实时业务上下文，辅助事实校验
    orders = await business_service.get_customer_orders(customer_id)
    latest_ord = orders[0] if orders else None
    biz_ctx = {"order": latest_ord} if latest_ord else None

    # 执行知识协同检索
    res = await knowledge_tool.execute_retrieval(
        db=state["db"],
        kb_id=state["kb_id"],
        raw_query=state["cleaned_query"],
        rewritten_query=rewritten,
        kb_config=state.get("kb_config", {}),
        live_business_context=biz_ctx
    )

    return {
        "rewritten_query": rewritten,
        "reply_type": res.reply_type,
        "top_score": res.top_score,
        "final_content": res.content,
        "sources": res.sources,
        "suggested_questions": res.suggested_questions,
        "business_data": None
    }

# ================= 状态图构建与路由 =================

def create_agent_customer_service_graph():
    graph = StateGraph(AgentWorkflowState)

    # 注册节点
    graph.add_node("clean_and_safety", clean_and_safety_node)
    graph.add_node("block_reply", block_reply_node)
    graph.add_node("plan_and_route", plan_and_route_node)
    graph.add_node("confirmation", confirmation_node)
    graph.add_node("escalation", escalation_node)
    graph.add_node("chitchat", chitchat_node)
    graph.add_node("business_action", business_action_node)
    graph.add_node("composite_task", composite_task_node)
    graph.add_node("knowledge_retrieval", knowledge_retrieval_node)

    # 1. 入口与安全检查
    graph.set_entry_point("clean_and_safety")

    def route_safety(state: AgentWorkflowState) -> str:
        return "block_reply" if state["is_sensitive"] else "plan_and_route"

    graph.add_conditional_edges(
        "clean_and_safety",
        route_safety,
        {
            "block_reply": "block_reply",
            "plan_and_route": "plan_and_route"
        }
    )

    # 2. 核心路由分流
    def route_by_plan(state: AgentWorkflowState) -> str:
        intent = state.get("intent_category", "KNOWLEDGE_QA")
        if intent == "ACTION_CONFIRMATION":
            return "confirmation"
        elif intent == "HUMAN_ESCALATION":
            return "escalation"
        elif intent == "CHITCHAT":
            return "chitchat"
        elif intent in ["INFORMATION_QUERY", "TASK_EXECUTION"]:
            return "business_action"
        elif intent == "COMPOSITE_TASK":
            return "composite_task"
        else:
            return "knowledge_retrieval"

    graph.add_conditional_edges(
        "plan_and_route",
        route_by_plan,
        {
            "confirmation": "confirmation",
            "escalation": "escalation",
            "chitchat": "chitchat",
            "business_action": "business_action",
            "composite_task": "composite_task",
            "knowledge_retrieval": "knowledge_retrieval"
        }
    )

    # 3. 汇聚到终端 END
    graph.add_edge("block_reply", END)
    graph.add_edge("confirmation", END)
    graph.add_edge("escalation", END)
    graph.add_edge("chitchat", END)
    graph.add_edge("business_action", END)
    graph.add_edge("composite_task", END)
    graph.add_edge("knowledge_retrieval", END)

    return graph.compile()

agent_customer_service_graph = create_agent_customer_service_graph()
