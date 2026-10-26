from typing import List, Optional
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

ROLE_USER = "user"               # 普通用户 / 客户
ROLE_TECH_AGENT = "tech_agent"   # 技术客服
ROLE_ADMIN = "admin"             # 超级管理员

ALL_ROLES = [ROLE_USER, ROLE_TECH_AGENT, ROLE_ADMIN]

# 客服工号与负责产品线绑定字典（用于数据权限隔离与工单自动路由）
AGENT_PRODUCT_MAP = {
    "agent_1028": "SAAS_CLOUD",
    "tech": "SAAS_CLOUD",
    "agent_1029": "E_COMMERCE",
    "agent_1030": "FIN_AI"
}

class CurrentUser(BaseModel):
    user_id: str
    username: str
    role: str
    product_code: Optional[str] = None

async def get_current_user(
    x_user_role: Optional[str] = Header(default=None, description="当前用户角色: user, tech_agent, admin"),
    x_user_id: Optional[str] = Header(default=None, description="当前用户ID"),
    x_user_product: Optional[str] = Header(default=None, description="所属产品线编码")
) -> CurrentUser:
    """从请求头提取当前用户上下文，未传递请求头时默认为 user 普通客户"""
    role = x_user_role.strip().lower() if x_user_role else ROLE_USER
    if role not in ALL_ROLES:
        role = ROLE_USER
    
    user_id = x_user_id or ("user_001" if role == ROLE_USER else ("agent_1028" if role == ROLE_TECH_AGENT else "admin_master"))
    
    # 确定技术客服归属产品
    product_code = x_user_product
    if role == ROLE_TECH_AGENT and not product_code:
        product_code = AGENT_PRODUCT_MAP.get(user_id, "SAAS_CLOUD")

    name_map = {
        ROLE_USER: f"客户·{user_id}",
        ROLE_TECH_AGENT: f"技术客服专员({user_id})",
        ROLE_ADMIN: "系统超级管理员"
    }

    return CurrentUser(
        user_id=user_id,
        username=name_map.get(role, "未知用户"),
        role=role,
        product_code=product_code
    )

def require_roles(allowed_roles: List[str]):
    """角色权限拦截器 Dependency"""
    async def role_checker(
        x_user_role: Optional[str] = Header(default=ROLE_ADMIN)
    ):
        current_role = x_user_role.strip().lower() if x_user_role else ROLE_ADMIN
        if current_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：当前角色 [{current_role}] 无权访问该资源，需要角色: {allowed_roles}"
            )
        return current_role
    return role_checker
