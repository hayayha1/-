from typing import List, Optional
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

ROLE_USER = "user"               # 普通用户 / 客户
ROLE_TECH_AGENT = "tech_agent"   # 技术客服
ROLE_ADMIN = "admin"             # 超级管理员
ROLE_OPERATOR = "operator"       # 运营人员

ALL_ROLES = [ROLE_USER, ROLE_TECH_AGENT, ROLE_ADMIN, ROLE_OPERATOR]

# 客服工号与负责产品线绑定字典（用于数据权限隔离与工单自动路由）
AGENT_PRODUCT_MAP = {
    "agent_1028": "SAAS_CLOUD",
    "tech": "SAAS_CLOUD",
    "agent_1029": "E_COMMERCE",
    "tech_ec": "E_COMMERCE",
    "tech_ecommerce": "E_COMMERCE",
    "agent_1030": "FIN_AI"
}

# 运营账号与归属知识库绑定字典（用于多组运营数据权限隔离）
# 运营一组负责核心知识库(b34fb60a-392c-4bb2-9e10-57539b37fed2)，运营二组负责次要知识库
OPERATOR_KB_MAP = {
    "ops": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "ops_001": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "operator": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "ops_ecommerce": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "ops_saas": "kb_saas_cloud_02",
    "ops_002": "kb_saas_cloud_02"
}

OPERATOR_PRODUCT_MAP = {
    "ops": "E_COMMERCE",
    "ops_001": "E_COMMERCE",
    "operator": "E_COMMERCE",
    "ops_ecommerce": "E_COMMERCE",
    "ops_saas": "SAAS_CLOUD",
    "ops_002": "SAAS_CLOUD"
}

class CurrentUser(BaseModel):
    user_id: str
    username: str
    role: str
    product_code: Optional[str] = None
    assigned_kb_id: Optional[str] = None

async def get_current_user(
    x_user_role: Optional[str] = Header(default=None, description="当前用户角色: user, tech_agent, admin, operator"),
    x_user_id: Optional[str] = Header(default=None, description="当前用户ID"),
    x_user_product: Optional[str] = Header(default=None, description="所属产品线编码"),
    x_user_kb_id: Optional[str] = Header(default=None, description="归属知识库ID")
) -> CurrentUser:
    """从请求头提取当前用户上下文，未传递请求头时默认为 user 普通客户"""
    role = x_user_role.strip().lower() if x_user_role else ROLE_USER
    if role not in ALL_ROLES:
        role = ROLE_USER
    
    default_ids = {
        ROLE_USER: "user_001",
        ROLE_TECH_AGENT: "agent_1028",
        ROLE_ADMIN: "admin_master",
        ROLE_OPERATOR: "ops_001"
    }
    user_id = x_user_id or default_ids.get(role, "user_001")
    
    # 确定技术客服与运营的归属产品和知识库
    product_code = x_user_product
    assigned_kb_id = x_user_kb_id
    
    if role == ROLE_TECH_AGENT and not product_code:
        product_code = AGENT_PRODUCT_MAP.get(user_id, "SAAS_CLOUD")
        
    if role == ROLE_OPERATOR:
        if not assigned_kb_id:
            assigned_kb_id = OPERATOR_KB_MAP.get(user_id, "b34fb60a-392c-4bb2-9e10-57539b37fed2")
        if not product_code:
            product_code = OPERATOR_PRODUCT_MAP.get(user_id, "SAAS_CLOUD")

    name_map = {
        ROLE_USER: f"客户·{user_id}",
        ROLE_TECH_AGENT: f"技术客服专员({user_id})",
        ROLE_ADMIN: "系统超级管理员",
        ROLE_OPERATOR: f"知识运营专员({user_id})"
    }

    return CurrentUser(
        user_id=user_id,
        username=name_map.get(role, "未知用户"),
        role=role,
        product_code=product_code,
        assigned_kb_id=assigned_kb_id
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
