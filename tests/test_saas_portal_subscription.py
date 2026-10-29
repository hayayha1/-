import pytest
import asyncio
import uuid
from fastapi.testclient import TestClient
from app.main import app, seed_demo_data

client = TestClient(app)

@pytest.fixture(autouse=True)
def setup_db():
    asyncio.run(seed_demo_data())

def test_user_register_with_enterprise_profile():
    test_u = f"founder_{uuid.uuid4().hex[:6]}"
    test_sub = f"sub{uuid.uuid4().hex[:6]}"
    payload = {
        "username": test_u,
        "password": "Password123!",
        "company_name": "未来智航科技有限公司",
        "subdomain": test_sub,
        "contact_name": "张总",
        "contact_phone": "13800138000",
        "credit_code": "91330100MA2ABCDE18",
        "industry": "人工智能/SaaS"
    }
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["username"] == test_u
    assert data["role"] == "end_user"
    assert "token" in data
    assert data["enterprise_profile"]["subdomain"] == test_sub
    assert data["enterprise_profile"]["company_name"] == "未来智航科技有限公司"

    # Test me endpoint with token
    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {data['token']}"})
    assert me_resp.status_code == 200
    me_data = me_resp.json()
    assert me_data["username"] == test_u
    assert me_data["enterprise_profile"]["company_name"] == "未来智航科技有限公司"

def test_subscription_audit_success():
    test_sub = f"zhiyu{uuid.uuid4().hex[:4]}"
    audit_payload = {
        "name": "杭州知语网络科技有限公司",
        "subdomain": test_sub,
        "contact_phone": "13912345678",
        "credit_code": "91330108MA28ABCD12",
        "industry": "科技服务",
        "tier": "PROFESSIONAL"
    }
    resp = client.post("/api/v1/tenants/audit-subscription", json=audit_payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["audit_passed"] is True
    assert data["score"] >= 95
    assert data["subdomain"] == test_sub
    assert len(data["reasons"]) >= 4

def test_subscription_audit_reserved_subdomain_rejected():
    audit_payload = {
        "name": "管理员测试公司",
        "subdomain": "admin",  # Reserved keyword
        "contact_phone": "13912345678",
        "credit_code": "91330108MA28ABCD12",
        "industry": "科技服务",
        "tier": "PROFESSIONAL"
    }
    resp = client.post("/api/v1/tenants/audit-subscription", json=audit_payload)
    assert resp.status_code == 400
    assert "保留词" in resp.json()["detail"] or "reserved" in resp.json()["detail"].lower()

def test_full_subscription_flow_and_onboarding():
    test_sub = f"yunfan{uuid.uuid4().hex[:4]}"
    # 1. Audit
    audit_payload = {
        "name": "云帆创新智能有限公司",
        "subdomain": test_sub,
        "contact_phone": "13766668888",
        "credit_code": "91330106MA28888888",
        "industry": "企业服务",
        "tier": "PROFESSIONAL"
    }
    audit_resp = client.post("/api/v1/tenants/audit-subscription", json=audit_payload)
    assert audit_resp.status_code == 200, audit_resp.text
    audit_data = audit_resp.json()
    assert audit_data["audit_passed"] is True

    # 2. Subscribe (simulate WeChat / Alipay payment)
    sub_payload = {
        "name": "云帆创新智能有限公司",
        "subdomain": test_sub,
        "tier": "PROFESSIONAL",
        "contact_name": "王创新",
        "contact_phone": "13766668888",
        "contact_email": "wang@yunfan.com",
        "credit_code": "91330106MA28888888",
        "industry": "企业服务",
        "payment_method": "ONLINE_WECHAT",
        "payment_order_id": "WX_PAY_MOCK_889911"
    }
    sub_resp = client.post("/api/v1/tenants/subscribe", json=sub_payload)
    assert sub_resp.status_code == 200, sub_resp.text
    sub_data = sub_resp.json()
    assert sub_data["tenant_code"] == test_sub
    assert f"{test_sub}.cs.cloud" in sub_data["subdomain"]
    admin_uname = sub_data["admin_credentials"]["username"]
    admin_pwd = sub_data["admin_credentials"]["temp_password"]
    assert admin_uname == f"admin_{test_sub}"

    # 3. Verify public tenant subdomain query
    pub_resp = client.get(f"/api/v1/tenants/by-subdomain/{test_sub}")
    assert pub_resp.status_code == 200
    assert pub_resp.json()["tenant_code"] == test_sub
    assert pub_resp.json()["is_onboarded"] is False

    # 4. Login with newly generated admin account
    login_resp = client.post("/api/v1/auth/login", json={
        "username": admin_uname,
        "password": admin_pwd
    })
    assert login_resp.status_code == 200
    admin_token = login_resp.json()["token"]

    # 5. Finish onboarding wizard as newly created admin
    onboard_payload = {
        "brand_name": "云帆AI智能客服中心",
        "bot_name": "小帆",
        "welcome_message": "您好！我是云帆AI智能助理，很高兴为您服务！",
        "primary_color": "#4F46E5",
        "daily_model": "deepseek-v3",
        "reasoning_model": "kimi-k1.5",
        "business_line": "云端SaaS智能协同"
    }
    onboard_resp = client.post(
        "/api/v1/tenants/current/finish-onboarding",
        json=onboard_payload,
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert onboard_resp.status_code == 200, onboard_resp.text
    onboard_data = onboard_resp.json()
    assert onboard_data["success"] is True
    assert onboard_data["brand_config"]["is_onboarded"] is True
    assert "embed_script_code" in onboard_data
    assert "widget.js" in onboard_data["embed_script_code"]
    assert f'data-tenant="{test_sub}"' in onboard_data["embed_script_code"]
