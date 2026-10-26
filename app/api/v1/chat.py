from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, update

from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.chat_session import ChatSession, ChatMessage
from app.schemas.chat import (
    ChatMessageRequest,
    ChatResponse,
    SourceCitation,
    SuggestedQuestion,
    ActionButton,
    ChatSessionCreate,
    ChatSessionResponse,
    ChatMessageResponse,
    TransferHumanRequest
)
from app.workflow.customer_service_graph import customer_service_graph
from app.core.auth import get_current_user, CurrentUser, require_roles, ROLE_USER, ROLE_TECH_AGENT, ROLE_ADMIN, AGENT_PRODUCT_MAP

router = APIRouter(prefix="/chat", tags=["智能客服与会话管理"])

# 产品线默认对应技术客服路由表
PRODUCT_DEFAULT_AGENT = {
    "SAAS_CLOUD": "agent_1028",
    "E_COMMERCE": "agent_1029",
    "FIN_AI": "agent_1030"
}

# 1. 查询会话列表 (普通用户仅看本人会话，技术客服仅看分配给自己的进行中会话，超管可查看全局包括历史会话)
@router.get("/sessions", response_model=List[ChatSessionResponse], summary="查询多客户客服会话列表")
async def list_chat_sessions(
    product_code: Optional[str] = Query(None, description="产品线筛选"),
    status: Optional[str] = Query(None, description="状态筛选: AI_SERVICE, HUMAN_CONNECTED, CLOSED 等"),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(ChatSession)

    # 普通客户行级隔离
    if current_user.role == ROLE_USER:
        stmt = stmt.where(ChatSession.customer_id == current_user.user_id)
    # 技术客服行级隔离：只能看到分配给自己的活跃会话（不看已结单历史），超管才看历史会话
    elif current_user.role == ROLE_TECH_AGENT:
        stmt = stmt.where(
            ChatSession.assigned_agent == current_user.user_id,
            ChatSession.status != "CLOSED"
        )
    # 超级管理员角色：若传了 status 按 status 查，否则可看全量会话

    if product_code:
        stmt = stmt.where(ChatSession.product_code == product_code)
    if status:
        stmt = stmt.where(ChatSession.status == status)

    stmt = stmt.order_by(desc(ChatSession.last_message_at))
    res = await db.execute(stmt)
    return res.scalars().all()

# 2. 创建或获取客户会话 (普通用户重新登录/进入时传入 force_new=True 开启全新会话)
@router.post("/sessions", response_model=ChatSessionResponse, summary="创建客户独立客服会话")
async def create_chat_session(payload: ChatSessionCreate, db: AsyncSession = Depends(get_db)):
    if payload.force_new:
        # 将用户历史所有未结单会话标记为 CLOSED，确保开启纯净新会话
        close_old_stmt = (
            update(ChatSession)
            .where(
                ChatSession.customer_id == payload.customer_id,
                ChatSession.status != "CLOSED"
            )
            .values(status="CLOSED")
        )
        await db.execute(close_old_stmt)

        session = ChatSession(
            customer_id=payload.customer_id,
            customer_name=payload.customer_name,
            product_code=payload.product_code,
            status="AI_SERVICE",
            last_message="新会话已开启",
            last_message_at=datetime.now()
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    # 非强制新建时查找活跃会话
    stmt = select(ChatSession).where(
        ChatSession.customer_id == payload.customer_id,
        ChatSession.product_code == payload.product_code,
        ChatSession.status != "CLOSED"
    ).order_by(desc(ChatSession.last_message_at))
    res = await db.execute(stmt)
    existing = res.scalars().first()
    if existing:
        return existing

    session = ChatSession(
        customer_id=payload.customer_id,
        customer_name=payload.customer_name,
        product_code=payload.product_code,
        status="AI_SERVICE",
        last_message="会话已建立",
        last_message_at=datetime.now()
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session

# 3. 获取指定会话的历史消息
@router.get("/sessions/{session_id}/messages", response_model=List[ChatMessageResponse], summary="加载会话历史消息流")
async def get_session_messages(session_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc())
    res = await db.execute(stmt)
    return res.scalars().all()

# 4. 对话主入口（集成会话消息持久化与业务卡片）
@router.post("", response_model=ChatResponse, summary="多客户客服对话接口（LangGraph编排与业务接入）")
async def chat_interaction(
    payload: ChatMessageRequest, 
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # 4.1 确保会话存在
    session = None
    if payload.session_id:
        session = await db.get(ChatSession, payload.session_id)
    if not session:
        stmt = select(ChatSession).where(
            ChatSession.customer_id == payload.customer_id,
            ChatSession.product_code == payload.product_code,
            ChatSession.status != "CLOSED"
        ).order_by(desc(ChatSession.last_message_at))
        res = await db.execute(stmt)
        session = res.scalars().first()
        if not session:
            session = ChatSession(
                customer_id=payload.customer_id,
                customer_name=f"客户 · {payload.customer_id}",
                product_code=payload.product_code,
                status="AI_SERVICE",
                last_message=payload.query[:30]
            )
            db.add(session)
            await db.flush()

    # 4.2 若是技术客服或人工接管状态下的管理员发送消息，则直接作为人工客服消息入库
    if current_user.role == ROLE_TECH_AGENT or (current_user.role == ROLE_ADMIN and session.status == "HUMAN_CONNECTED"):
        agent_name = current_user.user_id if current_user.user_id != "system" else "客服专员"
        human_msg = ChatMessage(
            session_id=session.id,
            role="human_agent",
            content=payload.query,
            reply_type="HUMAN_AGENT",
            confidence_score=1.0,
            raw_query=payload.query
        )
        db.add(human_msg)
        session.status = "HUMAN_CONNECTED"
        session.assigned_agent = agent_name
        session.last_message = f"[人工客服]: {payload.query}"
        session.last_message_at = datetime.now()
        await db.commit()

        return ChatResponse(
            session_id=session.id,
            reply_type="HUMAN_AGENT",
            confidence_score=1.0,
            content=payload.query,
            raw_query=payload.query,
            rewritten_query=payload.query,
            sources=[],
            suggested_questions=[],
            action_buttons=[ActionButton(action="FINISH_HUMAN", label="结束人工服务，切回AI")]
        )

    # 4.3 记录普通用户提问入库
    user_msg = ChatMessage(
        session_id=session.id,
        role="user",
        content=payload.query,
        raw_query=payload.query
    )
    db.add(user_msg)
    await db.flush()

    # 4.4 若当前会话已被人工客服接管，且是客户发来的消息
    if session.status == "HUMAN_CONNECTED":
        session.last_message = payload.query
        session.last_message_at = datetime.now()
        await db.commit()

        return ChatResponse(
            session_id=session.id,
            reply_type="HUMAN_AGENT",
            confidence_score=1.0,
            content="[系统提示] 您的消息已送达专属人工客服专员，专员正在为您处理，请稍候回复。",
            raw_query=payload.query,
            rewritten_query=payload.query,
            sources=[],
            suggested_questions=[],
            action_buttons=[ActionButton(action="FINISH_HUMAN", label="结束人工服务，返回智能助手")]
        )

    # 4.5 查找指定或当前产品下的生效知识库
    kb_stmt = select(KnowledgeBase).where(
        KnowledgeBase.product_code == payload.product_code,
        KnowledgeBase.status == "ACTIVE"
    )
    if payload.kb_id:
        kb_stmt = kb_stmt.where(KnowledgeBase.id == payload.kb_id)
    
    kb_res = await db.execute(kb_stmt)
    kb = kb_res.scalars().first()
    if not kb:
        # 兜底：如果没按产品找到，尝试获取任意一个启用的知识库
        fallback_stmt = select(KnowledgeBase).where(KnowledgeBase.status == "ACTIVE")
        kb = (await db.execute(fallback_stmt)).scalars().first()

    if not kb:
        raise HTTPException(
            status_code=404, 
            detail=f"当前系统中未找到任何处于启用状态的知识库，请先由管理员创建并启用知识库"
        )

    # 4.6 LangGraph 编排驱动
    initial_state = {
        "db": db,
        "kb_id": kb.id,
        "product_code": payload.product_code,
        "customer_id": payload.customer_id,
        "raw_query": payload.query,
        "cleaned_query": "",
        "rewritten_query": "",
        "is_sensitive": False,
        "intent_category": "",
        "kb_config": kb.config or {},
        "reranked_chunks": [],
        "top_score": 0.0,
        "reply_type": "FALLBACK",
        "final_content": "",
        "sources": [],
        "suggested_questions": [],
        "business_data": None
    }

    final_state = await customer_service_graph.ainvoke(initial_state)

    # 4.7 组装响应与溯源
    sources_raw = final_state.get("sources", [])
    suggested_raw = final_state.get("suggested_questions", [])
    biz_data = final_state.get("business_data")
    reply_type = final_state.get("reply_type", "FALLBACK")
    content = final_state.get("final_content", "")
    score = round(final_state.get("top_score", 0.0), 3)

    sources = [
        SourceCitation(
            doc_id=s["doc_id"],
            doc_name=s["doc_name"],
            heading=s.get("heading"),
            chunk_id=s["chunk_id"],
            snippet=s.get("snippet"),
            priority=s.get("priority", 1)
        )
        for s in sources_raw
    ]

    suggested = [
        SuggestedQuestion(
            question_id=q["question_id"],
            title=q["title"]
        )
        for q in suggested_raw
    ]

    # 常驻“转接人工客服”操作按钮，支持结束人工后可再次转接
    actions = [
        ActionButton(action="TRANSFER_TO_HUMAN", label="转接人工客服")
    ]

    # 4.8 将助手消息持久化入库
    assistant_msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content=content,
        reply_type=reply_type,
        confidence_score=score,
        raw_query=payload.query,
        rewritten_query=final_state.get("rewritten_query"),
        sources=[s.model_dump() for s in sources],
        suggested_questions=[q.model_dump() for q in suggested],
        business_data=biz_data
    )
    db.add(assistant_msg)

    # 更新会话最后消息
    session.last_message = payload.query
    session.last_message_at = datetime.now()
    await db.commit()

    return ChatResponse(
        session_id=session.id,
        reply_type=reply_type,
        confidence_score=score,
        content=content,
        raw_query=payload.query,
        rewritten_query=final_state.get("rewritten_query"),
        sources=sources,
        suggested_questions=suggested,
        business_data=biz_data,
        action_buttons=actions
    )

# 5. 转人工客服接口（按产品自动路由对应客服工号）
@router.post("/transfer-human", summary="用户申请接入人工座席")
async def transfer_to_human(payload: TransferHumanRequest, db: AsyncSession = Depends(get_db)):
    session = await db.get(ChatSession, payload.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    # 根据产品线路由专属技术客服工号
    target_agent = PRODUCT_DEFAULT_AGENT.get(session.product_code, "agent_1028")

    session.status = "HUMAN_CONNECTED"
    session.assigned_agent = target_agent

    # 插入系统通知与人工打招呼消息
    system_msg = ChatMessage(
        session_id=session.id,
        role="system",
        content=f"已为您成功接入产品专属人工座席（工号: {target_agent}），智能助手已暂停，当前由人工专员为您服务。",
        reply_type="SYSTEM"
    )
    db.add(system_msg)

    agent_greeting = ChatMessage(
        session_id=session.id,
        role="human_agent",
        content=f"您好！我是负责本产品线的人工客服专员（工号: {target_agent}）。已接入您的咨询，请问有什么可以协助您的？",
        reply_type="HUMAN_AGENT"
    )
    db.add(agent_greeting)

    session.last_message = f"[人工客服 {target_agent} 已接入]"
    session.last_message_at = datetime.now()
    await db.commit()

    return {
        "status": "HUMAN_CONNECTED",
        "agent": target_agent,
        "product_code": session.product_code,
        "message": f"人工客服专员 {target_agent} 已成功接入会话"
    }

# 6. 用户主动挂断人工客服接口（用户主动结束或离开页面自动挂断）
@router.post("/hangup", summary="用户主动挂断人工客服，恢复AI接待")
async def hangup_human_service(session_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    session.status = "AI_SERVICE"
    assigned = session.assigned_agent
    session.assigned_agent = None

    sys_msg = ChatMessage(
        session_id=session.id,
        role="system",
        content="人工客服服务已挂断/结束。智能客服助手已重新为您就绪，您随时可以继续向我提问，或再次点击右上角转接人工。",
        reply_type="SYSTEM"
    )
    db.add(sys_msg)
    session.last_message = "[人工客服已结束，切回AI]"
    session.last_message_at = datetime.now()
    await db.commit()

    return {
        "status": "AI_SERVICE", 
        "message": "人工服务已挂断，智能客服已重新接管",
        "previous_agent": assigned
    }

# 7. 客服或管理员结束人工服务，恢复AI接待
@router.post("/finish-human", summary="结束人工客服会话返回AI")
async def finish_human_service(session_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    session = await db.get(ChatSession, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    session.status = "AI_SERVICE"
    session.assigned_agent = None

    sys_msg = ChatMessage(
        session_id=session.id,
        role="system",
        content="本次人工服务已圆满结单。智能客服小助手已重新为您就绪，您可以随时继续向小助手提问！",
        reply_type="SYSTEM"
    )
    db.add(sys_msg)
    session.last_message = "[人工服务已结束]"
    session.last_message_at = datetime.now()
    await db.commit()
    return {"status": "AI_SERVICE", "message": "已恢复AI客服接待"}
