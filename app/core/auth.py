from typing import List, Optional
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

ROLE_USER = "user"               # 普通用户 / 客户
ROLE_TECH_AGENT = "tech_agent"   # 技术客服
ROLE_ADMIN = "admin"             # 超级管理员

ALL_ROLES = [ROLE_USER, ROLE_TECH_AGENT, ROLE_ADMIN]

class CurrentUser(BaseModel):
    user_id: str
    username: str
    role: str

async def get_current_user(
    x_user_role: Optional[str] = Header(default=None, description="当前用户角色: user, tech_agent, admin"),
    x_user_id: Optional[str] = Header(default=None, description="当前用户ID")
) -> CurrentUser:
    """从请求头提取当前用户上下文，未传递请求头时默认为 user 普通客户"""
    role = x_user_role.strip().lower() if x_user_role else ROLE_USER
    if role not in ALL_ROLES:
        role = ROLE_USER
    
    user_id = x_user_id or "user_001"
    name_map = {
        ROLE_USER: f"客户·{user_id}",
        ROLE_TECH_AGENT: "技术客服专员(工号#1028)",
        ROLE_ADMIN: "系统超级管理员"
    }

    return CurrentUser(
        user_id=user_id,
        username=name_map.get(role, "未知用户"),
        role=role
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
