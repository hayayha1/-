import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field

from app.services.agent.memory import memory_hub

class SubTask(BaseModel):
    step_id: int
    task_type: str  # "READ_TOOL" | "WRITE_TOOL" | "KNOWLEDGE_QA" | "CLARIFICATION"
    tool_name: Optional[str] = None
    description: str
    status: str = "PENDING"  # "PENDING" | "EXECUTING" | "COMPLETED" | "FAILED"

class TaskPlan(BaseModel):
    intent_category: str
    primary_action: Optional[str] = None
    is_composite: bool = False
    requires_knowledge: bool = False
    requires_tools: bool = True
    subtasks: List[SubTask] = Field(default_factory=list)
    raw_query: str = ""
    summary: str = ""

class GoalPlanner:
    """目标理解与复合任务拆解中心"""

    def __init__(self):
        pass

    async def analyze_and_plan(
        self,
        query: str,
        customer_id: str,
        session_id: str
    ) -> TaskPlan:
        """理解用户核心目标、判断多意图并生成结构化任务计划"""
        clean_q = query.strip()

        # 0. 优先检测是否是对“挂起高危动作”的二次确认/取消信号
        pending_action = memory_hub.get_pending_action(session_id)
        if pending_action:
            if any(w in clean_q for w in ["确认", "好的", "同意", "提交", "是的", "办理", "没问题", "确定", "yes", "ok"]):
                return TaskPlan(
                    intent_category="ACTION_CONFIRMATION",
                    primary_action="EXECUTE_CONFIRMED",
                    is_composite=False,
                    requires_knowledge=False,
                    requires_tools=True,
                    subtasks=[SubTask(step_id=1, task_type="WRITE_TOOL", tool_name=pending_action["tool_name"], description="正式执行已授权的高危写操作")],
                    raw_query=clean_q,
                    summary="用户二次确认执行挂起操作"
                )
            elif any(w in clean_q for w in ["取消", "不要了", "算了", "放弃", "暂不", "不需要", "no"]):
                return TaskPlan(
                    intent_category="ACTION_CONFIRMATION",
                    primary_action="CANCEL_CONFIRMED",
                    is_composite=False,
                    requires_knowledge=False,
                    requires_tools=False,
                    subtasks=[],
                    raw_query=clean_q,
                    summary="用户主动取消挂起操作"
                )

        # 1. 显式转人工关键词拦截
        if any(w in clean_q for w in ["转人工", "人工客服", "找客服", "呼叫人工", "真人客服"]):
            return TaskPlan(
                intent_category="HUMAN_ESCALATION",
                primary_action="TRANSFER_TO_HUMAN",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=False,
                subtasks=[],
                raw_query=clean_q,
                summary="用户主动请求转接人工客服"
            )

        # 2. 纯闲聊/问候检测
        if clean_q in ["你好", "您好", "在吗", "在不在", "嗨", "hello", "hi", "谢谢", "再见", "客气了", "早安", "晚上好"]:
            return TaskPlan(
                intent_category="CHITCHAT",
                primary_action="CHITCHAT_REPLY",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=False,
                subtasks=[],
                raw_query=clean_q,
                summary="通用日常问候或礼貌交流"
            )

        # 3. 复合意图拆解（如：“查一下订单物流，顺便开下发票” 或 “查下订单，如果不想要怎么退款”）
        has_logistics = any(k in clean_q for k in ["物流", "快递", "运单", "到哪了", "发货"])
        has_refund_action = any(k in clean_q for k in ["申请退款", "发起退款", "我要退款", "退货退款", "把订单退了", "退掉"])
        has_refund_query = any(k in clean_q for k in ["退款进度", "退款状态", "退款审核", "退款几天到账"])
        has_policy_query = any(k in clean_q for k in ["政策", "怎么退", "规则", "规范", "流程", "说明书", "教程", "使用手册", "怎么配置", "七天无理由"])
        has_invoice = any(k in clean_q for k in ["发票", "专票", "普票", "开票", "报销"])
        has_address = any(k in clean_q for k in ["改地址", "修改地址", "收件地址", "改派", "换地址"])
        has_order_query = any(k in clean_q for k in ["查订单", "我的订单", "订单列表", "买了什么"])

        subtasks: List[SubTask] = []

        # 场景 A: 复合意图 - 物流/订单查询 + 发票开具
        if (has_logistics or has_order_query) and has_invoice:
            subtasks.append(SubTask(step_id=1, task_type="READ_TOOL", tool_name="query_logistics" if has_logistics else "query_orders", description="查询订单及物流信息"))
            subtasks.append(SubTask(step_id=2, task_type="WRITE_TOOL", tool_name="apply_invoice", description="提交发票开具申请"))
            return TaskPlan(
                intent_category="COMPOSITE_TASK",
                primary_action="COMPOSITE_EXECUTION",
                is_composite=True,
                requires_knowledge=False,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="复合任务：核对物流信息并开具电子发票"
            )

        # 场景 B: 复合意图 - 订单/物流查询 + 政策规章答疑
        if (has_logistics or has_order_query) and has_policy_query:
            subtasks.append(SubTask(step_id=1, task_type="READ_TOOL", tool_name="query_logistics" if has_logistics else "query_orders", description="核实用户名下订单状态"))
            subtasks.append(SubTask(step_id=2, task_type="KNOWLEDGE_QA", tool_name="knowledge_retriever", description="检索知识库规章与售后政策"))
            return TaskPlan(
                intent_category="COMPOSITE_TASK",
                primary_action="COMPOSITE_EXECUTION",
                is_composite=True,
                requires_knowledge=True,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="复合任务：核对当前订单履约状态并给出官方政策说明"
            )

        # 4. 单意图识别 - 真实业务写操作
        if has_refund_action:
            subtasks.append(SubTask(step_id=1, task_type="WRITE_TOOL", tool_name="apply_refund", description="申请订单退换货与退款"))
            return TaskPlan(
                intent_category="TASK_EXECUTION",
                primary_action="apply_refund",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="业务办理：发起订单售后退款申请"
            )

        if has_address:
            subtasks.append(SubTask(step_id=1, task_type="WRITE_TOOL", tool_name="update_shipping_address", description="修改订单收件地址与联系方式"))
            return TaskPlan(
                intent_category="TASK_EXECUTION",
                primary_action="update_shipping_address",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="业务办理：修改收货地址"
            )

        if has_invoice:
            subtasks.append(SubTask(step_id=1, task_type="WRITE_TOOL", tool_name="apply_invoice", description="开具订单电子发票"))
            return TaskPlan(
                intent_category="TASK_EXECUTION",
                primary_action="apply_invoice",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="业务办理：开具电子发票"
            )

        # 5. 单意图识别 - 只读信息查询
        if has_refund_query:
            subtasks.append(SubTask(step_id=1, task_type="READ_TOOL", tool_name="query_refund_status", description="查询退款单流转进度"))
            return TaskPlan(
                intent_category="INFORMATION_QUERY",
                primary_action="query_refund_status",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="业务查询：查看退款审批进度"
            )

        if has_logistics:
            subtasks.append(SubTask(step_id=1, task_type="READ_TOOL", tool_name="query_logistics", description="查询顺丰实时物流轨迹"))
            return TaskPlan(
                intent_category="INFORMATION_QUERY",
                primary_action="query_logistics",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="业务查询：实时物流动态跟踪"
            )

        if has_order_query:
            subtasks.append(SubTask(step_id=1, task_type="READ_TOOL", tool_name="query_orders", description="查询名下订单列表"))
            return TaskPlan(
                intent_category="INFORMATION_QUERY",
                primary_action="query_orders",
                is_composite=False,
                requires_knowledge=False,
                requires_tools=True,
                subtasks=subtasks,
                raw_query=clean_q,
                summary="业务查询：获取订单概览"
            )

        # 6. 默认进入知识库问答检索（RAG 协同工具）
        subtasks.append(SubTask(step_id=1, task_type="KNOWLEDGE_QA", tool_name="knowledge_retriever", description="检索知识库相关段落与标准问答"))
        return TaskPlan(
            intent_category="KNOWLEDGE_QA",
            primary_action="knowledge_retriever",
            is_composite=False,
            requires_knowledge=True,
            requires_tools=False,
            subtasks=subtasks,
            raw_query=clean_q,
            summary="知识政策咨询与问答"
        )

goal_planner = GoalPlanner()
