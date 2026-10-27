import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from app.main import app, seed_demo_data
from app.db.session import init_db

@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    await init_db()
    await seed_demo_data()

@pytest.mark.asyncio
async def test_admin_list_tenants():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Platform admin can list tenants
        res = await client.get("/api/v1/tenants", headers={"X-Role": "admin"})
        assert res.status_code == 200
        data = res.json()
        assert len(data) >= 1
        tenant_codes = [t["tenant_code"] for t in data]
        assert "tenant_default" in tenant_codes

        # 2. Non-admin cannot list all tenants (403)
        res_non_admin = await client.get("/api/v1/tenants", headers={"X-Role": "user"})
        assert res_non_admin.status_code == 403

@pytest.mark.asyncio
async def test_create_and_update_tenant():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        import uuid
        test_code = f"tenant_{uuid.uuid4().hex[:8]}"
        payload = {
            "tenant_code": test_code,
            "name": "自动化测试企业",
            "tier": "STARTER",
            "contact_name": "张总",
            "contact_phone": "13912345678",
            "max_seats": 5,
            "max_kbs": 2,
            "monthly_token_quota": 500000,
            "monthly_query_quota": 10000,
            "brand_config": {
                "brand_name": "测试企业客服",
                "bot_name": "测小宝",
                "bot_avatar": "⚡",
                "welcome_message": "欢迎光临测试企业！",
                "primary_color": "#2563EB",
                "theme": "blue"
            }
        }

        # 创建租户
        create_res = await client.post("/api/v1/tenants", json=payload, headers={"X-Role": "admin"})
        assert create_res.status_code == 200
        created = create_res.json()
        assert created["tenant_code"] == test_code
        assert created["tier"] == "STARTER"
        assert created["brand_config"]["bot_name"] == "测小宝"

        # 校验租户重复编码拒绝
        dup_res = await client.post("/api/v1/tenants", json=payload, headers={"X-Role": "admin"})
        assert dup_res.status_code == 400

        # 更新配额
        upd_res = await client.put(
            f"/api/v1/tenants/{test_code}",
            json={"max_seats": 8, "monthly_query_quota": 20000},
            headers={"X-Role": "admin"}
        )
        assert upd_res.status_code == 200
        updated = upd_res.json()
        assert updated["max_seats"] == 8
        assert updated["monthly_query_quota"] == 20000

        # 更新品牌配置
        brand_payload = {
            "brand_name": "新测试企业",
            "bot_name": "小新助理",
            "bot_avatar": "🌟",
            "welcome_message": "很高兴服务您！",
            "primary_color": "#10B981",
            "theme": "emerald"
        }
        b_res = await client.put(
            f"/api/v1/tenants/{test_code}/brand",
            json=brand_payload,
            headers={"X-Role": "admin", "X-Tenant-Id": test_code}
        )
        assert b_res.status_code == 200
        assert b_res.json()["brand_config"]["bot_name"] == "小新助理"

@pytest.mark.asyncio
async def test_tenant_data_isolation():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Tenant A: tenant_default
        # Tenant B: tenant_retail_a
        res_a = await client.get("/api/v1/kbs", headers={"X-Role": "admin", "X-Tenant-Id": "tenant_default"})
        assert res_a.status_code == 200
        kbs_a = res_a.json()
        for k in kbs_a:
            assert k.get("tenant_id") == "tenant_default"

        res_b = await client.get("/api/v1/kbs", headers={"X-Role": "admin", "X-Tenant-Id": "tenant_retail_a"})
        assert res_b.status_code == 200
        kbs_b = res_b.json()
        for k in kbs_b:
            assert k.get("tenant_id") == "tenant_retail_a"

        # 验证会话隔离
        sess_a = await client.get("/api/v1/chat/sessions", headers={"X-Role": "admin", "X-Tenant-Id": "tenant_default"})
        assert sess_a.status_code == 200
        sess_b = await client.get("/api/v1/chat/sessions", headers={"X-Role": "admin", "X-Tenant-Id": "tenant_retail_a"})
        assert sess_b.status_code == 200
        ids_a = {s["id"] for s in sess_a.json()}
        ids_b = {s["id"] for s in sess_b.json()}
        # 两者的会话 ID 互不重叠
        assert len(ids_a.intersection(ids_b)) == 0

@pytest.mark.asyncio
async def test_tenant_quota_limits_and_usage():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        import uuid
        tiny_code = f"tenant_{uuid.uuid4().hex[:8]}"
        payload = {
            "tenant_code": tiny_code,
            "name": "超限测试企业",
            "tier": "STARTER",
            "max_seats": 1,
            "max_kbs": 1,
            "monthly_token_quota": 10000,
            "monthly_query_quota": 100
        }
        await client.post("/api/v1/tenants", json=payload, headers={"X-Role": "admin"})

        # 第 1 个知识库成功
        kb1_res = await client.post(
            "/api/v1/kbs",
            json={"product_code": "PROD_TINY", "name": "知识库01"},
            headers={"X-Role": "admin", "X-Tenant-Id": tiny_code}
        )
        assert kb1_res.status_code == 200

        # 第 2 个知识库触碰配额上限 (max_kbs=1)，应报 400
        kb2_res = await client.post(
            "/api/v1/kbs",
            json={"product_code": "PROD_TINY", "name": "知识库02"},
            headers={"X-Role": "admin", "X-Tenant-Id": tiny_code}
        )
        assert kb2_res.status_code == 400
        assert "配额上限" in kb2_res.json()["detail"]

        # 查看用量看板
        usage_res = await client.get(f"/api/v1/tenants/{tiny_code}/usage", headers={"X-Role": "admin"})
        assert usage_res.status_code == 200
        u_data = usage_res.json()
        assert u_data["metrics"]["kbs"]["used"] == 1
        assert u_data["metrics"]["kbs"]["quota"] == 1
        assert u_data["metrics"]["kbs"]["percent"] == 100.0

@pytest.mark.asyncio
async def test_suspended_tenant_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        import uuid
        susp_code = f"tenant_{uuid.uuid4().hex[:8]}"
        payload = {
            "tenant_code": susp_code,
            "name": "已冻结企业",
            "tier": "STARTER"
        }
        await client.post("/api/v1/tenants", json=payload, headers={"X-Role": "admin"})
        # 冻结租户
        await client.put(f"/api/v1/tenants/{susp_code}", json={"status": "SUSPENDED"}, headers={"X-Role": "admin"})

        # 尝试提问或创建知识库应被拒绝 403
        kb_res = await client.post(
            "/api/v1/kbs",
            json={"product_code": "SAAS_CLOUD", "name": "冻结知识库"},
            headers={"X-Role": "admin", "X-Tenant-Id": susp_code}
        )
        assert kb_res.status_code == 403
        assert "冻结" in kb_res.json()["detail"] or "暂停" in kb_res.json()["detail"]

