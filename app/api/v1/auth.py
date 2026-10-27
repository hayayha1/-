from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
from app.core.auth import (
    ROLE_ADMIN, 
    ROLE_TECH_AGENT, 
    ROLE_USER, 
    ROLE_OPERATOR,
    AGENT_PRODUCT_MAP,
    OPERATOR_KB_MAP,
    OPERATOR_PRODUCT_MAP
)

router = APIRouter(prefix="/auth", tags=["用户认证与角色鉴权"])

class LoginRequest(BaseModel):
    username: str = Field(..., description="登录账号")
    password: str = Field(..., description="登录密码")

class LoginResponse(BaseModel):
    user_id: str
    username: str
    role: str
    display_name: str
    token: str
    product_code: Optional[str] = None
    assigned_kb_id: Optional[str] = None

# 预置账号字典
PRESET_USERS = {
    "admin": {
        "passwords": ["admin123", "123456", "admin"],
        "role": ROLE_ADMIN,
        "display_name": "系统超级管理员",
        "user_id": "admin_master",
        "product_code": None,
        "assigned_kb_id": None
    },
    "tech": {
        "passwords": ["tech123", "123456", "tech"],
        "role": ROLE_TECH_AGENT,
        "display_name": "SaaS云平台技术客服(工号#1028)",
        "user_id": "agent_1028",
        "product_code": "SAAS_CLOUD",
        "assigned_kb_id": None
    },
    "tech_ec": {
        "passwords": ["tech123", "123456", "tech", "agent123"],
        "role": ROLE_TECH_AGENT,
        "display_name": "电商系统技术客服(工号#1029)",
        "user_id": "agent_1029",
        "product_code": "E_COMMERCE",
        "assigned_kb_id": None
    },
    "tech_ecommerce": {
        "passwords": ["tech123", "123456", "tech", "agent123"],
        "role": ROLE_TECH_AGENT,
        "display_name": "电商系统技术客服(工号#1029)",
        "user_id": "agent_1029",
        "product_code": "E_COMMERCE",
        "assigned_kb_id": None
    },
    "agent": {
        "passwords": ["agent123", "123456", "agent"],
        "role": ROLE_TECH_AGENT,
        "display_name": "SaaS云平台技术客服(工号#1028)",
        "user_id": "agent_1028",
        "product_code": "SAAS_CLOUD",
        "assigned_kb_id": None
    },
    "agent_1029": {
        "passwords": ["agent123", "123456", "agent", "tech123"],
        "role": ROLE_TECH_AGENT,
        "display_name": "电商系统技术客服(工号#1029)",
        "user_id": "agent_1029",
        "product_code": "E_COMMERCE",
        "assigned_kb_id": None
    },
    "ops": {
        "passwords": ["ops123", "123456", "ops"],
        "role": ROLE_OPERATOR,
        "display_name": "电商知识运营专员(工号#ops01)",
        "user_id": "ops_001",
        "product_code": "SAAS_CLOUD",
        "assigned_kb_id": "b34fb60a-392c-4bb2-9e10-57539b37fed2"
    },
    "operator": {
        "passwords": ["ops123", "123456", "operator"],
        "role": ROLE_OPERATOR,
        "display_name": "电商知识运营专员(工号#ops01)",
        "user_id": "ops_001",
        "product_code": "SAAS_CLOUD",
        "assigned_kb_id": "b34fb60a-392c-4bb2-9e10-57539b37fed2"
    },
    "ops_saas": {
        "passwords": ["ops123", "123456", "ops"],
        "role": ROLE_OPERATOR,
        "display_name": "SaaS平台知识运营专员(工号#ops02)",
        "user_id": "ops_002",
        "product_code": "SAAS_CLOUD",
        "assigned_kb_id": "kb_saas_cloud_02"
    },
    "user": {
        "passwords": ["user123", "123456", "user"],
        "role": ROLE_USER,
        "display_name": "普通客户·001",
        "user_id": "user_001",
        "product_code": None,
        "assigned_kb_id": None
    }
}

@router.post("/login", response_model=LoginResponse, summary="多角色账号密码登录")
async def login(payload: LoginRequest):
    u = payload.username.strip().lower()
    p = payload.password.strip()

    if u in PRESET_USERS:
        conf = PRESET_USERS[u]
        if p in conf["passwords"]:
            return LoginResponse(
                user_id=conf["user_id"],
                username=u,
                role=conf["role"],
                display_name=conf["display_name"],
                token=f"token_{u}_{conf['role']}",
                product_code=conf.get("product_code"),
                assigned_kb_id=conf.get("assigned_kb_id")
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="密码错误，请核对后重试"
            )

    # 动态匹配：若账号包含 admin，视为管理员；ops/operator 视为运营；tech/agent 视为技术客服；其余视为客户
    product_code = None
    assigned_kb_id = None
    if "admin" in u:
        role = ROLE_ADMIN
        display_name = f"管理员 · {u}"
        default_pwd = "admin123"
    elif "ops" in u or "operator" in u:
        role = ROLE_OPERATOR
        assigned_kb_id = OPERATOR_KB_MAP.get(u, "b34fb60a-392c-4bb2-9e10-57539b37fed2")
        product_code = OPERATOR_PRODUCT_MAP.get(u, "SAAS_CLOUD")
        display_name = f"知识运营 · {u}"
        default_pwd = "ops123"
    elif "tech" in u or "agent" in u:
        role = ROLE_TECH_AGENT
        product_code = AGENT_PRODUCT_MAP.get(u, "SAAS_CLOUD")
        display_name = f"技术客服 · {u} ({product_code})"
        default_pwd = "tech123"
    else:
        role = ROLE_USER
        display_name = f"普通客户 · {u}"
        default_pwd = "123456"

    # 简单允许匹配或者密码为 123456 / default_pwd
    if p in [default_pwd, "123456", u]:
        return LoginResponse(
            user_id=u,
            username=u,
            role=role,
            display_name=display_name,
            token=f"token_{u}_{role}",
            product_code=product_code,
            assigned_kb_id=assigned_kb_id
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="账号或密码错误。预置账号：admin/admin123, ops/ops123, tech/tech123, user/user123"
    )
