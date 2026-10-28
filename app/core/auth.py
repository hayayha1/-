from typing import List, Optional, Set, Dict
from fastapi import Header, HTTPException, status
from pydantic import BaseModel

# ===== 两级六角色架构体系 =====

# 1. SaaS 平台层
ROLE_PLATFORM_ADMIN = "platform_admin"  # 👑 平台超级管理员 (租户生命周期、配额包、全平台基础设施)

# 2. 企业租户层
ROLE_TENANT_ADMIN = "tenant_admin"      # 🏢 企业租户管理员 (企业组织架构、品牌白标人设、配额大盘)
ROLE_KNOWLEDGE_MASTER = "knowledge_master"  # 🧠 知识库架构主管 (知识采审终审、阈值算法调优、BadCase聚类闭环)
ROLE_QA_SUPERVISOR = "qa_supervisor"    # 🔍 质检主管 / 客服组长 (全线会话巡检、质检评分、工单改派调度)
ROLE_SERVICE_AGENT = "service_agent"    # 👩‍💼 一线技术客服坐席 (在线排错接单、360°画像赋能、提报知识草稿)
ROLE_END_USER = "end_user"              # 👤 终端消费者 / 用户 (智能问答、服务评价点赞点踩、申请转人工)

# ===== 历史兼容别名 =====
ROLE_ADMIN = "admin"                    # 兼容原超级管理员
ROLE_OPERATOR = "operator"              # 兼容原知识运营专员
ROLE_TECH_AGENT = "tech_agent"          # 兼容原技术客服
ROLE_USER = "user"                      # 兼容原客户

# 全量角色集合
ALL_ROLES = [
    ROLE_PLATFORM_ADMIN,
    ROLE_TENANT_ADMIN,
    ROLE_KNOWLEDGE_MASTER,
    ROLE_QA_SUPERVISOR,
    ROLE_SERVICE_AGENT,
    ROLE_END_USER,
    ROLE_ADMIN,
    ROLE_OPERATOR,
    ROLE_TECH_AGENT,
    ROLE_USER
]

# 角色等价与兼容映射字典
ROLE_ALIASES: Dict[str, Set[str]] = {
    ROLE_PLATFORM_ADMIN: {ROLE_PLATFORM_ADMIN, ROLE_ADMIN},
    ROLE_TENANT_ADMIN: {ROLE_TENANT_ADMIN, ROLE_ADMIN},
    ROLE_KNOWLEDGE_MASTER: {ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR},
    ROLE_QA_SUPERVISOR: {ROLE_QA_SUPERVISOR},
    ROLE_SERVICE_AGENT: {ROLE_SERVICE_AGENT, ROLE_TECH_AGENT},
    ROLE_END_USER: {ROLE_END_USER, ROLE_USER},
    # 历史角色别名向新体系投影
    ROLE_ADMIN: {ROLE_ADMIN, ROLE_PLATFORM_ADMIN, ROLE_TENANT_ADMIN},
    ROLE_OPERATOR: {ROLE_OPERATOR, ROLE_KNOWLEDGE_MASTER},
    ROLE_TECH_AGENT: {ROLE_TECH_AGENT, ROLE_SERVICE_AGENT},
    ROLE_USER: {ROLE_USER, ROLE_END_USER},
}

# 客服工号与负责产品线绑定字典（用于数据权限隔离与工单自动路由）
AGENT_PRODUCT_MAP = {
    "agent_1028": "SAAS_CLOUD",
    "tech": "SAAS_CLOUD",
    "agent_1029": "E_COMMERCE",
    "tech_ec": "E_COMMERCE",
    "tech_ecommerce": "E_COMMERCE",
    "agent_1030": "FIN_AI",
    "service_agent": "SAAS_CLOUD"
}

# 知识架构与运营账号与归属知识库绑定字典（用于多组运营数据权限隔离）
OPERATOR_KB_MAP = {
    "ops": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "ops_001": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "km_001": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "operator": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "ops_ecommerce": "b34fb60a-392c-4bb2-9e10-57539b37fed2",
    "ops_saas": "kb_saas_cloud_02",
    "ops_002": "kb_saas_cloud_02"
}

OPERATOR_PRODUCT_MAP = {
    "ops": "E_COMMERCE",
    "ops_001": "E_COMMERCE",
    "km_001": "SAAS_CLOUD",
    "operator": "E_COMMERCE",
    "ops_ecommerce": "E_COMMERCE",
    "ops_saas": "SAAS_CLOUD",
    "ops_002": "SAAS_CLOUD"
}

class CurrentUser(BaseModel):
    user_id: str
    username: str
    role: str
    tenant_id: str = "tenant_default"
    is_platform_admin: bool = False
    product_code: Optional[str] = None
    assigned_kb_id: Optional[str] = None

    @property
    def is_tenant_admin(self) -> bool:
        return self.role in [ROLE_TENANT_ADMIN, ROLE_ADMIN, ROLE_PLATFORM_ADMIN]

    @property
    def is_knowledge_master(self) -> bool:
        return self.role in [ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR]

    @property
    def is_qa_supervisor(self) -> bool:
        return self.role in [ROLE_QA_SUPERVISOR]

    @property
    def is_service_agent(self) -> bool:
        return self.role in [ROLE_SERVICE_AGENT, ROLE_TECH_AGENT]

    @property
    def is_end_user(self) -> bool:
        return self.role in [ROLE_END_USER, ROLE_USER]

async def get_current_user(
    x_user_role: Optional[str] = Header(default=None, description="当前用户角色"),
    x_role: Optional[str] = Header(default=None, description="角色别名请求头"),
    x_user_id: Optional[str] = Header(default=None, description="当前用户ID"),
    x_user_product: Optional[str] = Header(default=None, description="所属产品线编码"),
    x_user_kb_id: Optional[str] = Header(default=None, description="归属知识库ID"),
    x_tenant_id: Optional[str] = Header(default=None, description="归属企业租户ID")
) -> CurrentUser:
    """从请求头提取当前用户上下文，未传递请求头时默认为 end_user / user 普通客户"""
    raw_role = x_user_role or x_role
    role = raw_role.strip().lower() if raw_role else ROLE_USER
    if role not in ALL_ROLES:
        role = ROLE_USER
    
    tenant_id = (x_tenant_id or "tenant_default").strip()
    is_platform_admin = (role in [ROLE_PLATFORM_ADMIN, ROLE_ADMIN])
    
    default_ids = {
        ROLE_PLATFORM_ADMIN: "admin_platform",
        ROLE_TENANT_ADMIN: "admin_tenant",
        ROLE_KNOWLEDGE_MASTER: "km_001",
        ROLE_QA_SUPERVISOR: "qa_001",
        ROLE_SERVICE_AGENT: "agent_1028",
        ROLE_END_USER: "user_001",
        ROLE_USER: "user_001",
        ROLE_TECH_AGENT: "agent_1028",
        ROLE_ADMIN: "admin_master",
        ROLE_OPERATOR: "ops_001"
    }
    user_id = x_user_id or default_ids.get(role, "user_001")
    
    # 确定技术客服与知识运营主管的归属产品和知识库
    product_code = x_user_product
    assigned_kb_id = x_user_kb_id
    
    if role in [ROLE_SERVICE_AGENT, ROLE_TECH_AGENT] and not product_code:
        product_code = AGENT_PRODUCT_MAP.get(user_id, "SAAS_CLOUD")
        
    if role in [ROLE_KNOWLEDGE_MASTER, ROLE_OPERATOR]:
        if not assigned_kb_id:
            assigned_kb_id = OPERATOR_KB_MAP.get(user_id, "b34fb60a-392c-4bb2-9e10-57539b37fed2")
        if not product_code:
            product_code = OPERATOR_PRODUCT_MAP.get(user_id, "SAAS_CLOUD")

    name_map = {
        ROLE_PLATFORM_ADMIN: "SaaS平台超级管理员",
        ROLE_TENANT_ADMIN: f"企业租户管理员({user_id})",
        ROLE_KNOWLEDGE_MASTER: f"知识架构主管({user_id})",
        ROLE_QA_SUPERVISOR: f"质检主管({user_id})",
        ROLE_SERVICE_AGENT: f"一线技术坐席({user_id})",
        ROLE_END_USER: f"终端客户·{user_id}",
        ROLE_USER: f"客户·{user_id}",
        ROLE_TECH_AGENT: f"技术客服专员({user_id})",
        ROLE_ADMIN: "系统超级管理员",
        ROLE_OPERATOR: f"知识运营专员({user_id})"
    }

    return CurrentUser(
        user_id=user_id,
        username=name_map.get(role, "未知用户"),
        role=role,
        tenant_id=tenant_id,
        is_platform_admin=is_platform_admin,
        product_code=product_code,
        assigned_kb_id=assigned_kb_id
    )

def require_roles(allowed_roles: List[str]):
    """角色权限拦截器 Dependency，支持两级六角色模型与旧角色别名兼容"""
    expanded_allowed: Set[str] = set()
    for r in allowed_roles:
        expanded_allowed.add(r)
        expanded_allowed.update(ROLE_ALIASES.get(r, set()))

    async def role_checker(
        x_user_role: Optional[str] = Header(default=None),
        x_role: Optional[str] = Header(default=None)
    ):
        raw_role = x_user_role or x_role
        current_role = raw_role.strip().lower() if raw_role else ROLE_ADMIN
        if current_role not in expanded_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"权限不足：当前角色 [{current_role}] 无权访问该资源，需要角色: {allowed_roles}"
            )
        return current_role
    return role_checker
