import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app, seed_demo_data
from app.db.session import init_db

@pytest_asyncio.fixture(autouse=True)
async def setup_database():
    await init_db()
    await seed_demo_data()

@pytest.mark.asyncio
async def test_roles_authentication_and_headers():
    """测试两级五角色体系（已删除质检主管角色）的请求头上下文注入与登录验证"""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. 有效五角色登录验证
        roles_to_test = [
            ("platform_admin", "admin123", "platform_admin"),
            ("tenant_admin", "admin123", "tenant_admin"),
            ("knowledge_master", "km123", "knowledge_master"),
            ("service_agent", "agent123", "service_agent"),
            ("end_user", "user123", "end_user"),
        ]
        for uname, pwd, expected_role in roles_to_test:
            res = await ac.post("/api/v1/auth/login", json={"username": uname, "password": pwd})
            assert res.status_code == 200, f"Login failed for {uname}: {res.text}"
            data = res.json()
            assert data["role"] == expected_role
            assert data["username"] == uname
            # 确认除平台超管外，其余角色均处于统一租户与产品组
            if expected_role != "platform_admin":
                assert data["tenant_id"] == "tenant_default"
                assert data["product_code"] == "SAAS_CLOUD"

        # 2. 确认已彻底删除质检主管角色账号
        res_qa = await ac.post("/api/v1/auth/login", json={"username": "qa_supervisor", "password": "any"})
        assert res_qa.status_code == 401

@pytest.mark.asyncio
async def test_platform_vs_tenant_admin_matrix():
    """测试平台超管 vs 企业超管的权限边界（租户开通 vs 品牌定制）"""
    transport = ASGITransport(app=app)
    platform_headers = {"X-User-Role": "platform_admin", "X-User-Id": "admin_platform"}
    tenant_headers = {"X-User-Role": "tenant_admin", "X-User-Id": "admin_tenant", "X-Tenant-Id": "tenant_default"}
    agent_headers = {"X-User-Role": "service_agent", "X-User-Id": "agent_1028", "X-Tenant-Id": "tenant_default"}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. 平台租户列表检索：平台超管允许，企业超管和坐席严格 403
        res = await ac.get("/api/v1/tenants", headers=platform_headers)
        assert res.status_code == 200

        res = await ac.get("/api/v1/tenants", headers=tenant_headers)
        assert res.status_code == 403

        res = await ac.get("/api/v1/tenants", headers=agent_headers)
        assert res.status_code == 403

        # 2. 品牌白标更新：企业超管允许定制本企业品牌人设，一线坐席严格 403
        brand_payload = {
            "brand_name": "极速重构测试品牌",
            "bot_name": "智能极速君",
            "bot_avatar": "🚀",
            "welcome_message": "欢迎光临测试服务！",
            "primary_color": "#059669",
            "theme": "emerald"
        }
        res = await ac.put("/api/v1/tenants/tenant_default/brand", json=brand_payload, headers=tenant_headers)
        assert res.status_code == 200
        assert res.json()["brand_config"]["brand_name"] == "极速重构测试品牌"

        res = await ac.put("/api/v1/tenants/tenant_default/brand", json=brand_payload, headers=agent_headers)
        assert res.status_code == 403

        # 3. 平台超管严禁窥视企业私有数据（知识库、会话、文档），企业超管允许管理本租户数据
        res_platform_sessions = await ac.get("/api/v1/chat/sessions", headers=platform_headers)
        assert res_platform_sessions.status_code == 403

        res_platform_kbs = await ac.get("/api/v1/kbs", headers=platform_headers)
        assert res_platform_kbs.status_code == 403

        res_platform_docs = await ac.get("/api/v1/documents", headers=platform_headers)
        assert res_platform_docs.status_code == 403

        # 企业超管有权访问本租户知识库和会话
        res_tenant_kbs = await ac.get("/api/v1/kbs", headers=tenant_headers)
        assert res_tenant_kbs.status_code == 200

        res_tenant_sessions = await ac.get("/api/v1/chat/sessions", headers=tenant_headers)
        assert res_tenant_sessions.status_code == 200

@pytest.mark.asyncio
async def test_knowledge_draft_and_approval_closed_loop():
    """测试采审分离闭环：一线坐席提报草稿 -> 质审通过上线入库"""
    transport = ASGITransport(app=app)
    agent_headers = {"X-User-Role": "service_agent", "X-User-Id": "agent_1028", "X-Tenant-Id": "tenant_default"}
    km_headers = {"X-User-Role": "knowledge_master", "X-User-Id": "km_001", "X-Tenant-Id": "tenant_default"}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 获取可用知识库
        kbs_res = await ac.get("/api/v1/kbs", headers=km_headers)
        assert kbs_res.status_code == 200
        kb_id = kbs_res.json()[0]["id"]

        # 1. 一线坐席提交新知识文档 -> 必须置为 PENDING_REVIEW 待审核草稿
        doc_payload = {
            "kb_id": kb_id,
            "name": "采审分离测试规范草案.md",
            "doc_type": "MD",
            "content": "# 采审分离操作手册\n一线坐席提交的草案必须经过知识主管终审发布方可生效切片。",
            "priority": 3
        }
        create_res = await ac.post("/api/v1/documents", json=doc_payload, headers=agent_headers)
        assert create_res.status_code == 200
        doc_data = create_res.json()
        doc_id = doc_data["id"]
        assert doc_data["status"] == "PENDING_REVIEW"

        # 2. 一线坐席越权试图审核上线 -> 必须被 403 拦截
        approve_fail = await ac.post(f"/api/v1/documents/{doc_id}/approve", headers=agent_headers)
        assert approve_fail.status_code == 403

        # 3. 知识主管调用 approve 审核通过 -> 状态变为 ACTIVE，并自动完成切片入库
        approve_success = await ac.post(f"/api/v1/documents/{doc_id}/approve", headers=km_headers)
        assert approve_success.status_code == 200
        assert approve_success.json()["status"] == "ACTIVE"
        assert approve_success.json()["chunk_count"] >= 1

        # 清理该测试文档
        del_res = await ac.delete(f"/api/v1/documents/{doc_id}", headers=km_headers)
        assert del_res.status_code == 200

@pytest.mark.asyncio
async def test_qa_review_and_agent_reassign_closed_loop():
    """测试企业管理员执行质检打分、BadCase联动与坐席改派闭环（质检主管角色已删除）"""
    transport = ASGITransport(app=app)
    admin_headers = {"X-User-Role": "tenant_admin", "X-User-Id": "admin_tenant", "X-Tenant-Id": "tenant_default"}
    agent_headers = {"X-User-Role": "service_agent", "X-User-Id": "agent_1028", "X-Tenant-Id": "tenant_default"}
    qa_headers = {"X-User-Role": "qa_supervisor", "X-User-Id": "qa_001", "X-Tenant-Id": "tenant_default"}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. 已删除的质检主管角色请求 -> 必须被 403 拒绝
        qa_rejected = await ac.get("/api/v1/chat/sessions", headers=qa_headers)
        assert qa_rejected.status_code == 403

        # 2. 企业超管可查看全租户会话列表
        sessions_res = await ac.get("/api/v1/chat/sessions", headers=admin_headers)
        assert sessions_res.status_code == 200
        sessions = sessions_res.json()
        assert len(sessions) >= 1
        target_session = sessions[0]
        sess_id = target_session["id"]

        # 3. 坐席试图越权质检评分 -> 必须被 403 拦截
        fail_qa = await ac.post(f"/api/v1/chat/sessions/{sess_id}/qa-review", json={
            "score": 4,
            "feedback": "客服越权质检"
        }, headers=agent_headers)
        assert fail_qa.status_code == 403

        # 4. 企业管理员进行质检评分与打标，并勾选一键推送到 BadCase 待办池
        qa_res = await ac.post(f"/api/v1/chat/sessions/{sess_id}/qa-review", json={
            "score": 2,
            "feedback": "坐席应答延迟偏高，专业安抚术语欠缺",
            "tags": ["服务超时", "沟通生硬"],
            "push_to_badcase": True
        }, headers=admin_headers)
        assert qa_res.status_code == 200
        assert qa_res.json()["qa_score"] == 2
        assert qa_res.json()["pushed_to_badcase"] is True
        assert qa_res.json()["badcase_id"] is not None

        # 5. 管理员针对该疑难会话执行坐席改派调度
        reassign_res = await ac.post(f"/api/v1/chat/sessions/{sess_id}/reassign", json={
            "target_agent": "agent_1029",
            "reason": "客户疑难技术问题升级至资深专员处理"
        }, headers=admin_headers)
        assert reassign_res.status_code == 200
        assert reassign_res.json()["assigned_agent"] == "agent_1029"

@pytest.mark.asyncio
async def test_user_profile_data_privacy_matrix():
    """测试用户画像的权限分层与脱敏合规"""
    transport = ASGITransport(app=app)
    admin_headers = {"X-User-Role": "tenant_admin", "X-User-Id": "admin_tenant", "X-Tenant-Id": "tenant_default"}
    agent_headers = {"X-User-Role": "service_agent", "X-User-Id": "agent_1028", "X-Tenant-Id": "tenant_default"}
    km_headers = {"X-User-Role": "knowledge_master", "X-User-Id": "km_001", "X-Tenant-Id": "tenant_default"}
    platform_headers = {"X-User-Role": "platform_admin", "X-User-Id": "admin_platform", "X-Tenant-Id": "tenant_default"}
    user_headers = {"X-User-Role": "end_user", "X-User-Id": "user_001", "X-Tenant-Id": "tenant_default"}
    qa_headers = {"X-User-Role": "qa_supervisor", "X-User-Id": "qa_001", "X-Tenant-Id": "tenant_default"}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. 企业管理员调阅客户画像 -> 允许查看
        res_admin = await ac.get("/api/v1/chat/user-profile?customer_id=U10086", headers=admin_headers)
        assert res_admin.status_code == 200
        profile_admin = res_admin.json()
        assert profile_admin["vip_level"] == "企业VIP"

        # 2. 一线技术坐席调阅 -> 允许，但手机号必须合规脱敏
        res_agent = await ac.get("/api/v1/chat/user-profile?customer_id=U10086", headers=agent_headers)
        assert res_agent.status_code == 200
        profile_agent = res_agent.json()
        assert "****" in profile_agent["phone"]

        # 3. 平台超管与知识主管无权窥视租户内客户隐私 -> 必须 403 拦截
        res_platform = await ac.get("/api/v1/chat/user-profile?customer_id=U10086", headers=platform_headers)
        assert res_platform.status_code == 403

        res_km = await ac.get("/api/v1/chat/user-profile?customer_id=U10086", headers=km_headers)
        assert res_km.status_code == 403

        # 4. 已删除的质检主管角色访问 -> 必须 403 拦截
        res_qa = await ac.get("/api/v1/chat/user-profile?customer_id=U10086", headers=qa_headers)
        assert res_qa.status_code == 403

        # 5. 终端普通用户越权查看他人画像 -> 必须 403 拦截
        res_user_other = await ac.get("/api/v1/chat/user-profile?customer_id=OTHER_9999", headers=user_headers)
        assert res_user_other.status_code == 403
