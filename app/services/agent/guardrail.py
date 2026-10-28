from typing import Dict, Any, List, Optional
from datetime import datetime

from app.services.agent.memory import memory_hub

class GuardrailDecision:
    def __init__(
        self,
        needs_confirmation: bool,
        confirmation_card: Optional[Dict[str, Any]] = None,
        content: str = "",
        pending_action_data: Optional[Dict[str, Any]] = None
    ):
        self.needs_confirmation = needs_confirmation
        self.confirmation_card = confirmation_card
        self.content = content
        self.pending_action_data = pending_action_data

class ActionGuardrail:
    """安全风控、高危写操作阻断与人机无缝协作中心"""

    def check_write_risk(
        self,
        tool_name: str,
        slots: Dict[str, Any],
        customer_id: str,
        session_id: str
    ) -> GuardrailDecision:
        """
        高危写操作安全校验：对高危写操作阻断并生成交互式确认卡片
        """
        from app.services.agent.tools import action_tool_center

        tool_def = action_tool_center.get_tool(tool_name)
        if not tool_def or tool_def.action_type != "WRITE":
            return GuardrailDecision(needs_confirmation=False)

        # 1. 高危操作拦截：发起退款
        if tool_name == "apply_refund":
            ord_id = slots.get("order_id", "当前订单")
            reason = slots.get("refund_reason", "用户申请售后")
            amount = slots.get("refund_amount", "订单全额")

            pending_data = {
                "tool_name": "apply_refund",
                "params": dict(slots),
                "summary": f"申请退款（订单 {ord_id}，金额 {amount}）",
                "created_at": datetime.now().isoformat()
            }
            memory_hub.set_pending_action(session_id, pending_data)

            card = {
                "card_type": "CONFIRMATION_CARD",
                "business_type": "HIGH_RISK_ACTION",
                "title": "⚠️ 关键业务授权确认：售后退款申请",
                "action_name": "apply_refund",
                "summary": "根据平台资金安全规范，请您仔细核对本次退款信息，确认无误后请点击【确认办理】：",
                "details": [
                    {"label": "关联订单号", "value": ord_id},
                    {"label": "退款原因", "value": reason},
                    {"label": "退款预估金额", "value": amount},
                    {"label": "资金返还途径", "value": "原支付方式（预计1个工作日到账）"}
                ],
                "confirm_text": "✅ 确认申请退款",
                "cancel_text": "❌ 取消退款",
                "confirm_prompt": "确认办理退款",
                "cancel_prompt": "取消办理退款"
            }

            content = (
                f"为保障您的账户与资金安全，已为您生成【退款申请核对卡片】。\n"
                f"• 订单号：{ord_id}\n"
                f"• 退款原因：{reason}\n"
                f"请核对上方信息，您可以直接点击下方卡片中的【确认申请退款】按钮或回复“确认”继续办理。"
            )

            return GuardrailDecision(
                needs_confirmation=True,
                confirmation_card=card,
                content=content,
                pending_action_data=pending_data
            )

        # 2. 中危操作拦截：修改收货地址
        if tool_name == "update_shipping_address":
            ord_id = slots.get("order_id", "当前订单")
            new_addr = slots.get("new_address", "未指定地址")
            phone = slots.get("contact_phone", "保持原电话")

            pending_data = {
                "tool_name": "update_shipping_address",
                "params": dict(slots),
                "summary": f"修改收件地址（订单 {ord_id} -> {new_addr}）",
                "created_at": datetime.now().isoformat()
            }
            memory_hub.set_pending_action(session_id, pending_data)

            card = {
                "card_type": "CONFIRMATION_CARD",
                "business_type": "MEDIUM_RISK_ACTION",
                "title": "📍 收货地址变更确认",
                "action_name": "update_shipping_address",
                "summary": "请核对即将更新的快递配送信息：",
                "details": [
                    {"label": "目标订单", "value": ord_id},
                    {"label": "新收货地址", "value": new_addr},
                    {"label": "联系电话", "value": phone}
                ],
                "confirm_text": "✅ 确认修改地址",
                "cancel_text": "❌ 取消修改",
                "confirm_prompt": "确认修改地址",
                "cancel_prompt": "取消修改地址"
            }

            content = (
                f"已收到您的地址变更诉求，请最后核实新信息：\n"
                f"• 订单号：{ord_id}\n"
                f"• 新地址：{new_addr}\n"
                f"核对无误后请点击卡片中【确认修改】或回复“确认”即可生效。"
            )

            return GuardrailDecision(
                needs_confirmation=True,
                confirmation_card=card,
                content=content,
                pending_action_data=pending_data
            )

        # 3. 中危操作拦截：取消订单
        if tool_name == "cancel_unpaid_order":
            ord_id = slots.get("order_id", "当前订单")
            pending_data = {
                "tool_name": "cancel_unpaid_order",
                "params": dict(slots),
                "summary": f"取消订单 {ord_id}",
                "created_at": datetime.now().isoformat()
            }
            memory_hub.set_pending_action(session_id, pending_data)

            card = {
                "card_type": "CONFIRMATION_CARD",
                "business_type": "MEDIUM_RISK_ACTION",
                "title": "⚠️ 订单取消确认",
                "action_name": "cancel_unpaid_order",
                "summary": f"订单取消后已锁定的算力配额将立即释放，请确认是否继续关闭订单 {ord_id}？",
                "details": [
                    {"label": "待取消订单", "value": ord_id}
                ],
                "confirm_text": "✅ 确认取消订单",
                "cancel_text": "❌ 暂不取消",
                "confirm_prompt": "确认取消订单",
                "cancel_prompt": "暂不取消"
            }

            content = f"订单 {ord_id} 一旦取消将无法恢复，请问您确认要关闭该订单吗？"
            return GuardrailDecision(
                needs_confirmation=True,
                confirmation_card=card,
                content=content,
                pending_action_data=pending_data
            )

        # 其他低危写操作（如催单、开发票）无需强阻断
        return GuardrailDecision(needs_confirmation=False)

    def build_escalation_package(
        self,
        session_id: str,
        customer_id: str,
        reason: str,
        plan_summary: str = "",
        slots: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """打包人工客服接管数据包"""
        mem = memory_hub.get_working_memory(session_id)
        prof = memory_hub.get_customer_profile(customer_id)

        return {
            "session_id": session_id,
            "customer_id": customer_id,
            "customer_name": prof.get("name", "客户"),
            "customer_vip": prof.get("vip_level", "标准用户"),
            "escalation_reason": reason,
            "plan_summary": plan_summary,
            "collected_slots": slots or mem.slots,
            "trace_steps_count": len(mem.history_trace),
            "escalated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

action_guardrail = ActionGuardrail()
