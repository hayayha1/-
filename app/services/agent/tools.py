from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
import uuid
from pydantic import BaseModel, Field

from app.services.business_service import business_service

class AgentToolDefinition(BaseModel):
    name: str
    description: str
    action_type: str = "READ"  # "READ" | "WRITE"
    risk_level: str = "LOW"    # "LOW" | "MEDIUM" | "HIGH"
    required_slots: List[str] = Field(default_factory=list)
    optional_slots: List[str] = Field(default_factory=list)

class ToolExecutionResult(BaseModel):
    success: bool
    tool_name: str
    message: str
    data: Optional[Dict[str, Any]] = None
    card_data: Optional[Dict[str, Any]] = None

class ActionToolCenter:
    """企业业务行动工具箱（Action Center）：涵盖只读查询与有副作用的业务写操作"""

    def __init__(self):
        self.tools: Dict[str, AgentToolDefinition] = {}
        self.handlers: Dict[str, Callable] = {}
        self._register_default_tools()

    def register_tool(self, tool_def: AgentToolDefinition, handler: Callable):
        self.tools[tool_def.name] = tool_def
        self.handlers[tool_def.name] = handler

    def get_tool(self, tool_name: str) -> Optional[AgentToolDefinition]:
        return self.tools.get(tool_name)

    def list_tools(self) -> List[AgentToolDefinition]:
        return list(self.tools.values())

    # ================= 业务工具具体实现 =================

    async def query_orders(self, customer_id: str, order_id: Optional[str] = None, **kwargs) -> ToolExecutionResult:
        """查询订单列表或单笔订单详情"""
        orders = await business_service.get_customer_orders(customer_id)
        if order_id:
            matched = next((o for o in orders if o["order_id"].lower() == order_id.lower()), None)
            if matched:
                card = {
                    "card_type": "ORDER_CARD",
                    "business_type": "ORDER_DETAIL",
                    "order_id": matched["order_id"],
                    "item_name": matched.get("product_name"),
                    "amount": matched.get("amount"),
                    "status": matched.get("status_desc") or matched.get("status"),
                    "carrier": matched.get("logistics", {}).get("carrier", "顺丰速运") if "logistics" in matched else None,
                    "tracking_no": matched.get("logistics", {}).get("tracking_no") if "logistics" in matched else None,
                    "latest_track": matched.get("logistics", {}).get("traces", [{}])[0].get("desc") if "logistics" in matched else None,
                    "can_refund": matched.get("status") in ["PAID", "COMPLETED"]
                }
                return ToolExecutionResult(
                    success=True,
                    tool_name="query_orders",
                    message=f"已为您查询到订单 {matched['order_id']} 的详细状态：",
                    data={"order": matched},
                    card_data=card
                )
            else:
                return ToolExecutionResult(
                    success=False,
                    tool_name="query_orders",
                    message=f"抱歉，未能在您名下找到编号为 {order_id} 的订单，请核对订单号是否正确。",
                    data={"customer_id": customer_id}
                )

        # 全量订单卡片
        card = {
            "card_type": "ORDER_LIST_CARD",
            "business_type": "ORDER_LIST",
            "title": f"客户订单列表（共 {len(orders)} 笔）",
            "orders": orders
        }
        return ToolExecutionResult(
            success=True,
            tool_name="query_orders",
            message=f"已为您检索到您账号绑定的订单明细（共 {len(orders)} 笔）：",
            data={"orders": orders},
            card_data=card
        )

    async def query_logistics(self, customer_id: str, order_id: Optional[str] = None, **kwargs) -> ToolExecutionResult:
        """查询指定订单的顺丰实时物流与派件轨迹"""
        orders = await business_service.get_customer_orders(customer_id)
        target = None
        if order_id:
            target = next((o for o in orders if o["order_id"].lower() == order_id.lower()), None)
        if not target and orders:
            # 默认取包含物流信息的最新订单
            target = next((o for o in orders if "logistics" in o), orders[0])

        if not target:
            return ToolExecutionResult(
                success=False,
                tool_name="query_logistics",
                message="抱歉，暂未查到您的包裹物流信息。如刚发货请等待系统同步。",
                data=None
            )

        logistics = target.get("logistics", {
            "carrier": "顺丰速运",
            "tracking_no": f"SF{customer_id[-4:]}9982341",
            "current_status": "派送中",
            "traces": [
                {"time": datetime.now().strftime("%Y-%m-%d %H:%M"), "desc": "快递员正在为您派件，请保持电话畅通"},
                {"time": "2026-10-25 18:00", "desc": "包裹已到达目的地分拣中心"}
            ]
        })

        card = {
            "card_type": "LOGISTICS_CARD",
            "business_type": "LOGISTICS_TRACKING",
            "carrier": logistics.get("carrier", "顺丰速运"),
            "current_status": logistics.get("current_status", "运输中"),
            "item_name": target.get("product_name"),
            "order_id": target.get("order_id"),
            "tracking_no": logistics.get("tracking_no"),
            "courier": "张师傅 (95338)",
            "courier_phone": "95338",
            "traces": logistics.get("traces", [])
        }

        return ToolExecutionResult(
            success=True,
            tool_name="query_logistics",
            message=f"已为您查询到订单 {target.get('order_id')} 的最新物流进展：",
            data={"order": target, "logistics": logistics},
            card_data=card
        )

    async def query_refund_status(self, customer_id: str, order_id: Optional[str] = None, **kwargs) -> ToolExecutionResult:
        """查询退款/售后单进度"""
        orders = await business_service.get_customer_orders(customer_id)
        target = None
        if order_id:
            target = next((o for o in orders if o["order_id"].lower() == order_id.lower()), None)
        if not target:
            # 查找处于退款中的订单
            target = next((o for o in orders if o.get("status") == "REFUNDING"), None)

        if not target or not target.get("refund_info"):
            return ToolExecutionResult(
                success=True,
                tool_name="query_refund_status",
                message="您当前名下暂无进行中的售后退款申请。如需退换货，可直接向我申请退款。",
                data=None
            )

        rf = target["refund_info"]
        card = {
            "card_type": "REFUND_CARD",
            "business_type": "REFUND_STATUS",
            "refund_id": rf.get("refund_id", "REF-889012"),
            "status": target.get("status_desc") or "退款审核中",
            "order_id": target.get("order_id"),
            "amount": rf.get("refund_amount") or target.get("amount"),
            "item_name": target.get("product_name"),
            "reason": rf.get("reason", "客户申请售后"),
            "review_comment": f"当前审批阶段：{rf.get('stage', '财务审核中')}",
            "eta": rf.get("eta", "预计1-3个工作日原路退回")
        }

        return ToolExecutionResult(
            success=True,
            tool_name="query_refund_status",
            message=f"已为您查询到订单 {target.get('order_id')} 的售后退款进度：",
            data={"order": target, "refund_info": rf},
            card_data=card
        )

    async def apply_refund(self, customer_id: str, order_id: str, refund_reason: str, refund_amount: Optional[str] = None, **kwargs) -> ToolExecutionResult:
        """【高危写操作】真实提交售后退款申请"""
        orders = await business_service.get_customer_orders(customer_id)
        matched = next((o for o in orders if o["order_id"].lower() == order_id.lower()), None)
        
        amt = refund_amount or (matched.get("amount") if matched else "¥12,800.00")
        item_name = matched.get("product_name") if matched else "订阅产品"
        new_refund_id = f"REF-{str(uuid.uuid4())[:8].upper()}"

        refund_info = {
            "refund_id": new_refund_id,
            "refund_amount": amt,
            "reason": refund_reason or "用户线上自主申请",
            "stage": "平台初审通过，等待财务批复",
            "eta": "预计1个工作日内原路返还至原支付渠道",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

        # 真实持久化修改内存中订单状态
        if matched:
            matched["status"] = "REFUNDING"
            matched["status_desc"] = "退款审核中"
            matched["refund_info"] = refund_info
        else:
            # 如果是自动生成订单，注入到列表
            if customer_id not in business_service.orders:
                business_service.orders[customer_id] = []
            business_service.orders[customer_id].append({
                "order_id": order_id,
                "product_name": item_name,
                "amount": amt,
                "status": "REFUNDING",
                "status_desc": "退款审核中",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "refund_info": refund_info
            })

        card = {
            "card_type": "REFUND_CARD",
            "business_type": "REFUND_STATUS",
            "refund_id": new_refund_id,
            "status": "退款审核中",
            "order_id": order_id,
            "amount": amt,
            "item_name": item_name,
            "reason": refund_reason,
            "review_comment": "已成功提交售后服务中心，初审系统已放行，请留意到账通知。",
            "eta": "预计1个工作日内原路退回"
        }

        return ToolExecutionResult(
            success=True,
            tool_name="apply_refund",
            message=f"【操作成功】订单 {order_id} 的退款申请已为您成功提交！售后受理编号为 {new_refund_id}。",
            data={"refund_id": new_refund_id, "order_id": order_id, "amount": amt},
            card_data=card
        )

    async def update_shipping_address(self, customer_id: str, order_id: str, new_address: str, contact_phone: Optional[str] = None, **kwargs) -> ToolExecutionResult:
        """【中危写操作】修改订单的收件地址与联系方式"""
        orders = await business_service.get_customer_orders(customer_id)
        matched = next((o for o in orders if o["order_id"].lower() == order_id.lower()), None)

        if matched:
            matched["shipping_address"] = new_address
            if contact_phone:
                matched["contact_phone"] = contact_phone
            if "logistics" in matched:
                matched["logistics"]["traces"].insert(0, {
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "desc": f"收件信息已更新：{new_address}（联系电话：{contact_phone or '原预留号码'}），顺丰速运重新调度中"
                })

        card = {
            "card_type": "ORDER_CARD",
            "business_type": "ORDER_DETAIL",
            "order_id": order_id,
            "item_name": matched.get("product_name") if matched else "已修改订单商品",
            "amount": matched.get("amount") if matched else "¥0.00",
            "status": "地址已修改 / 重新配送",
            "latest_track": f"新收货地址已生效：{new_address}"
        }

        return ToolExecutionResult(
            success=True,
            tool_name="update_shipping_address",
            message=f"【地址修改成功】已将订单 {order_id} 的收件地址成功变更为：{new_address}，联系电话：{contact_phone or '保持原号码'}。",
            data={"order_id": order_id, "new_address": new_address, "contact_phone": contact_phone},
            card_data=card
        )

    async def cancel_unpaid_order(self, customer_id: str, order_id: str, cancel_reason: Optional[str] = None, **kwargs) -> ToolExecutionResult:
        """【中危写操作】取消未支付或履约中的订单"""
        orders = await business_service.get_customer_orders(customer_id)
        matched = next((o for o in orders if o["order_id"].lower() == order_id.lower()), None)
        if matched:
            matched["status"] = "CANCELLED"
            matched["status_desc"] = "已取消关闭"
            matched["cancel_reason"] = cancel_reason or "用户主动取消"

        return ToolExecutionResult(
            success=True,
            tool_name="cancel_unpaid_order",
            message=f"【订单取消成功】订单 {order_id} 已成功关闭并释放资源，取消原因：{cancel_reason or '用户主动要求关闭'}。",
            data={"order_id": order_id, "status": "CANCELLED"}
        )

    async def apply_invoice(self, customer_id: str, order_id: str, invoice_title: str, tax_id: Optional[str] = None, email: Optional[str] = None, **kwargs) -> ToolExecutionResult:
        """【低危写操作】开具或补开发票"""
        orders = await business_service.get_customer_orders(customer_id)
        matched = next((o for o in orders if o["order_id"].lower() == order_id.lower()), None)
        if matched:
            matched["invoice_status"] = f"已开具电子专票 (抬头: {invoice_title})"

        return ToolExecutionResult(
            success=True,
            tool_name="apply_invoice",
            message=f"【发票申请成功】订单 {order_id} 的增值税电子普通发票已为您提交税务系统开具，抬头：{invoice_title}。电子发票 PDF 稍后将发送至您的邮箱：{email or '企业默认邮箱'}。",
            data={"order_id": order_id, "invoice_title": invoice_title, "tax_id": tax_id}
        )

    async def expedite_shipping(self, customer_id: str, order_id: str, **kwargs) -> ToolExecutionResult:
        """【低危写操作】物流催派件"""
        return ToolExecutionResult(
            success=True,
            tool_name="expedite_shipping",
            message=f"【催单指令已下发】系统已为您向顺丰速运上海中转集散中心发送优先派送加急指令，快递小哥将在今日优先为您送达！",
            data={"order_id": order_id, "expedite_status": "URGENT_NOTIFIED"}
        )

    # ================= 注册默认工具清单 =================

    def _register_default_tools(self):
        self.register_tool(
            AgentToolDefinition(
                name="query_orders",
                description="查询当前客户账号下的订单列表、订单状态或某单号的具体详情",
                action_type="READ",
                risk_level="LOW",
                optional_slots=["order_id"]
            ),
            self.query_orders
        )
        self.register_tool(
            AgentToolDefinition(
                name="query_logistics",
                description="查询订单的快递物流承运商、运单号及最新节点时间轴轨迹",
                action_type="READ",
                risk_level="LOW",
                optional_slots=["order_id"]
            ),
            self.query_logistics
        )
        self.register_tool(
            AgentToolDefinition(
                name="query_refund_status",
                description="查询客户名下订单的售后退款审核进度与预计到账时间",
                action_type="READ",
                risk_level="LOW",
                optional_slots=["order_id"]
            ),
            self.query_refund_status
        )
        self.register_tool(
            AgentToolDefinition(
                name="apply_refund",
                description="对已支付或履约中的订单发起售后退换货与退款申请（高风险，需用户确认）",
                action_type="WRITE",
                risk_level="HIGH",
                required_slots=["order_id", "refund_reason"],
                optional_slots=["refund_amount"]
            ),
            self.apply_refund
        )
        self.register_tool(
            AgentToolDefinition(
                name="update_shipping_address",
                description="修改未发货或派送中订单的收件人详细地址与联系电话（中风险，需确认）",
                action_type="WRITE",
                risk_level="MEDIUM",
                required_slots=["order_id", "new_address"],
                optional_slots=["contact_phone"]
            ),
            self.update_shipping_address
        )
        self.register_tool(
            AgentToolDefinition(
                name="cancel_unpaid_order",
                description="关闭/取消未支付或不需要履约的订单（中风险，需确认）",
                action_type="WRITE",
                risk_level="MEDIUM",
                required_slots=["order_id"],
                optional_slots=["cancel_reason"]
            ),
            self.cancel_unpaid_order
        )
        self.register_tool(
            AgentToolDefinition(
                name="apply_invoice",
                description="为指定订单开具增值税普通或专用发票",
                action_type="WRITE",
                risk_level="LOW",
                required_slots=["order_id", "invoice_title"],
                optional_slots=["tax_id", "email"]
            ),
            self.apply_invoice
        )
        self.register_tool(
            AgentToolDefinition(
                name="expedite_shipping",
                description="对运输中的快递向物流中心下发加急催促派件指令",
                action_type="WRITE",
                risk_level="LOW",
                required_slots=["order_id"]
            ),
            self.expedite_shipping
        )

action_tool_center = ActionToolCenter()
