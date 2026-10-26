from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
from app.core.auth import ROLE_ADMIN, ROLE_TECH_AGENT, ROLE_USER, AGENT_PRODUCT_MAP

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

# 预置账号字典
PRESET_USERS = {
    "admin": {
        "passwords": ["admin123", "123456", "admin"],
        "role": ROLE_ADMIN,
        "display_name": "系统超级管理员",
        "user_id": "admin_master",
        "product_code": None
    },
    "tech": {
        "passwords": ["tech123", "123456", "tech"],
        "role": ROLE_TECH_AGENT,
        "display_name": "SaaS云平台技术客服(工号#1028)",
        "user_id": "agent_1028",
        "product_code": "SAAS_CLOUD"
    },
    "agent": {
        "passwords": ["agent123", "123456", "agent"],
        "role": ROLE_TECH_AGENT,
        "display_name": "SaaS云平台技术客服(工号#1028)",
        "user_id": "agent_1028",
        "product_code": "SAAS_CLOUD"
    },
    "agent_1029": {
        "passwords": ["agent123", "123456", "agent"],
        "role": ROLE_TECH_AGENT,
        "display_name": "电商系统技术客服(工号#1029)",
        "user_id": "agent_1029",
        "product_code": "E_COMMERCE"
    },
    "user": {
        "passwords": ["user123", "123456", "user"],
        "role": ROLE_USER,
        "display_name": "普通客户·001",
        "user_id": "user_001",
        "product_code": None
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
                product_code=conf.get("product_code")
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="密码错误，请核对后重试"
            )

    # 动态匹配：若账号包含 admin，视为管理员；tech/agent 视为技术客服；其余视为客户
    product_code = None
    if "admin" in u:
        role = ROLE_ADMIN
        display_name = f"管理员 · {u}"
        default_pwd = "admin123"
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
            product_code=product_code
        )

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="账号或密码错误。预置账号：admin/admin123, tech/tech123, user/user123"
    )
