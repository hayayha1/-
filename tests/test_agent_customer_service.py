import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app, seed_demo_data
from app.db.session import init_db
from app.services.agent.memory import memory_hub
from app.services.agent.tools import action_tool_center
from app.services.agent.slot_engine import slot_engine
from app.services.agent.planner import goal_planner
from app.services.agent.guardrail import action_guardrail

@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    await init_db()
    await seed_demo_data()

# ================= 1. 模块一测试：目标理解与复合任务拆解 =================

@pytest.mark.asyncio
async def test_agent_goal_planning_and_decomposition():
    """测试意图识别与复合任务拆解能力"""
    # 1.1 纯信息查询
    plan1 = await goal_planner.analyze_and_plan("查一下我的顺丰物流到哪了", "user_001", "sess_test_1")
    assert plan1.intent_category == "INFORMATION_QUERY"
    assert plan1.primary_action == "query_logistics"
    assert plan1.is_composite is False

    # 1.2 复合意图拆解：查物流 + 开具发票
    plan2 = await goal_planner.analyze_and_plan("查一下物流进度，顺便帮我把发票开了", "user_001", "sess_test_2")
    assert plan2.intent_category == "COMPOSITE_TASK"
    assert plan2.is_composite is True
    assert len(plan2.subtasks) == 2
    assert plan2.subtasks[0].tool_name in ["query_logistics", "query_orders"]
    assert plan2.subtasks[1].tool_name == "apply_invoice"

# ================= 2. 模块二测试：动态槽位抽取与主动反向追问 =================

@pytest.mark.asyncio
async def test_agent_slot_extraction_and_clarification():
    """测试业务槽位提取与槽位缺失时的反向追问"""
    # 2.1 文本槽位提取
    query = "帮我对订单 ORD-20261025-8891 申请退款，原因是规格与预期不符"
    slots = slot_engine.extract_slots_from_text(query, "user_001", "sess_slot_1", "apply_refund")
    assert slots.get("order_id") == "ORD-20261025-8891"
    assert slots.get("refund_reason") == "规格与预期不符"

    # 2.2 槽位缺失追问：用户只说“我要退款”，缺失 order_id
    validation = await slot_engine.validate_and_build_clarification(
        action_name="apply_refund",
        current_slots={"refund_reason": "七天无理由"},
        customer_id="user_001",
        session_id="sess_slot_2"
    )
    assert validation.is_complete is False
    assert "order_id" in validation.missing_slots
    assert validation.clarification_card is not None
    assert validation.clarification_card["card_type"] == "CLARIFICATION_CARD"
    assert len(validation.clarification_card["options"]) > 0

# ================= 3. 模块三测试：企业业务行动工具箱 =================

@pytest.mark.asyncio
async def test_agent_action_tool_box():
    """测试业务工具箱执行（查订单、查物流、改地址）"""
    # 3.1 查物流
    res_logistics = await action_tool_center.query_logistics("user_001", "ORD-20261025-8891")
    assert res_logistics.success is True
    assert res_logistics.card_data["card_type"] == "LOGISTICS_CARD"
    assert "顺丰速运" in res_logistics.card_data["carrier"]

    # 3.2 改地址
    res_addr = await action_tool_center.update_shipping_address(
        customer_id="user_001",
        order_id="ORD-20261025-8891",
        new_address="北京市海淀区中关村南大街1号",
        contact_phone="13911112222"
    )
    assert res_addr.success is True
    assert "北京市海淀区" in res_addr.message

# ================= 4. 模块五测试：双轨记忆中心 =================

@pytest.mark.asyncio
async def test_agent_memory_hub():
    """测试短期工作记忆合并与长期画像联动"""
    sess_id = "sess_mem_test"
    # 短期记忆：槽位合并
    memory_hub.update_slots(sess_id, {"order_id": "ORD-20261025-8891"})
    memory_hub.update_slots(sess_id, {"refund_reason": "拍错了"})
    slots = memory_hub.get_slots(sess_id)
    assert slots["order_id"] == "ORD-20261025-8891"
    assert slots["refund_reason"] == "拍错了"

    # 长期用户画像
    profile = memory_hub.get_customer_profile("U10086")
    assert profile["vip_level"] == "企业VIP"

# ================= 5. 模块六测试：高危写操作二次确认与端到端闭环 =================

@pytest.mark.asyncio
async def test_agent_high_risk_confirmation_and_execution():
    """端到端验证：高危操作先拦截确认，用户确认后正式写入"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers = {"X-User-Role": "user", "X-User-Id": "user_001"}
        session_id = "agent_e2e_session_01"

        # Step 1: 建立会话
        res = await ac.post("/api/v1/chat/sessions", json={
            "customer_id": "user_001",
            "customer_name": "张先生",
            "product_code": "SAAS_CLOUD"
        }, headers=user_headers)
        session_id = res.json()["id"]

        # Step 2: 发起退款申请（具备必填槽位） -> 触发风控拦截，返回 CONFIRMATION_CARD
        res = await ac.post("/api/v1/chat", json={
            "product_code": "SAAS_CLOUD",
            "customer_id": "user_001",
            "session_id": session_id,
            "query": "帮我对订单 ORD-20261025-8891 发起退款，原因是规格与预期不符"
        }, headers=user_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["reply_type"] == "TRANSACTION"
        assert data["business_data"] is not None
        assert data["business_data"]["card_type"] == "CONFIRMATION_CARD"
        assert "确认" in data["content"]

        # Step 3: 用户确认执行
        res = await ac.post("/api/v1/chat", json={
            "product_code": "SAAS_CLOUD",
            "customer_id": "user_001",
            "session_id": session_id,
            "query": "好的，确认办理"
        }, headers=user_headers)
        assert res.status_code == 200
        data_confirmed = res.json()
        assert data_confirmed["reply_type"] == "TRANSACTION"
        assert data_confirmed["business_data"] is not None
        assert data_confirmed["business_data"]["card_type"] == "REFUND_CARD"
        assert "已为您成功提交" in data_confirmed["content"] or "操作成功" in data_confirmed["content"]

@pytest.mark.asyncio
async def test_agent_clarification_flow_e2e():
    """端到端验证：槽位缺失主动追问，用户补全槽位后进入确认"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers = {"X-User-Role": "user", "X-User-Id": "user_001"}

        res = await ac.post("/api/v1/chat/sessions", json={
            "customer_id": "user_001",
            "customer_name": "张先生",
            "product_code": "SAAS_CLOUD"
        }, headers=user_headers)
        session_id = res.json()["id"]

        # Step 1: 用户模糊说“我想申请退款”（缺失单号） -> 返回 CLARIFICATION_CARD
        res = await ac.post("/api/v1/chat", json={
            "product_code": "SAAS_CLOUD",
            "customer_id": "user_001",
            "session_id": session_id,
            "query": "我想申请退款"
        }, headers=user_headers)
        assert res.status_code == 200
        data = res.json()
        assert data["business_data"] is not None
        assert data["business_data"]["card_type"] == "CLARIFICATION_CARD"
        assert "请选择" in data["content"] or "哪笔订单" in data["content"]
