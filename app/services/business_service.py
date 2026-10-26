from typing import Dict, Any, List, Optional
from datetime import datetime

class BusinessService:
    """真实业务系统适配层（模拟企业内部订单中心、物流中心与售后中心）"""

    def __init__(self):
        # 预置业务系统数据（按客户 customer_id 隔离）
        self.orders = {
            "user_001": [
                {
                    "order_id": "ORD-20261025-8891",
                    "product_name": "智能云平台 企业级年付订阅套餐",
                    "amount": "¥12,800.00",
                    "status": "PAID",
                    "status_desc": "已支付 / 履约中",
                    "created_at": "2026-10-25 14:20:00",
                    "invoice_status": "已开具增值税专票",
                    "logistics": {
                        "carrier": "顺丰速运",
                        "tracking_no": "SF142980311234",
                        "current_status": "派件中",
                        "traces": [
                            {"time": "2026-10-26 09:30", "desc": "快递员正在为您派件，联系电话：95338"},
                            {"time": "2026-10-25 21:10", "desc": "快件已到达【上海浦东集散中心】"},
                            {"time": "2026-10-25 16:00", "desc": "包裹已由顺丰速运揽收并完成分拣"}
                        ]
                    }
                }
            ],
            "user_002": [
                {
                    "order_id": "ORD-20261020-3320",
                    "product_name": "高并发GPU算力云主机 (A100*8)",
                    "amount": "¥24,000.00",
                    "status": "REFUNDING",
                    "status_desc": "退款审核中",
                    "created_at": "2026-10-20 10:15:00",
                    "invoice_status": "未开票",
                    "refund_info": {
                        "refund_id": "REF-998231",
                        "refund_amount": "¥24,000.00",
                        "reason": "配额未满足实际算力需求",
                        "stage": "财务复核",
                        "eta": "预计1个工作日内原路退回银行卡"
                    }
                }
            ],
            "user_003": [
                {
                    "order_id": "ORD-20261026-0012",
                    "product_name": "API数据调用通用资源包 (1000万次)",
                    "amount": "¥3,500.00",
                    "status": "COMPLETED",
                    "status_desc": "已生效使用",
                    "created_at": "2026-10-26 08:30:00",
                    "invoice_status": "待申请"
                }
            ]
        }

    async def get_customer_orders(self, customer_id: str) -> List[Dict[str, Any]]:
        """查询客户名下订单及物流"""
        return self.orders.get(customer_id, [
            {
                "order_id": f"ORD-AUTO-{customer_id[-4:]}",
                "product_name": "云标准计算实例 (2C4G)",
                "amount": "¥680.00",
                "status": "PAID",
                "status_desc": "正常运行中",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "invoice_status": "已推送电子发票"
            }
        ])

    async def handle_transaction_query(self, customer_id: str, query: str) -> Dict[str, Any]:
        """识别具体业务并返回卡片结构数据"""
        orders = await self.get_customer_orders(customer_id)
        latest_order = orders[0] if orders else None

        if any(k in query for k in ["物流", "快递", "运单", "到哪了", "发货"]):
            return {
                "business_type": "LOGISTICS_TRACKING",
                "title": "实时物流与订单轨迹",
                "summary": f"为您查询到订单 {latest_order.get('order_id')} 的最新物流进展：",
                "order": latest_order
            }
        elif any(k in query for k in ["退款", "售后", "退款进度", "退款审核"]):
            return {
                "business_type": "REFUND_STATUS",
                "title": "售后退款审批单详情",
                "summary": f"您名下最新订单的退款审核状态如下：",
                "order": latest_order
            }
        else:
            return {
                "business_type": "ORDER_LIST",
                "title": "客户订单业务卡片",
                "summary": f"已为您检索到您账号绑定的订单明细（共 {len(orders)} 笔）：",
                "orders": orders
            }

business_service = BusinessService()
