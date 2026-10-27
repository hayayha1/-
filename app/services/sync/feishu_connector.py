import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import httpx

from app.services.sync.base import BaseKnowledgeConnector, SourceDocumentMetadata, RawSourceDocument
from app.services.sync.cleaner import ContentCleaner

logger = logging.getLogger(__name__)

# 内置沙盒/模拟数据（用于无真实飞书企业账号时的端到端开箱即测）
MOCK_FEISHU_DOCS = {
    "doc_fs_001": {
        "title": "飞书-售后政策与7天无理由退换货指南",
        "folder_path": "/客服业务中心/售后政策",
        "url": "https://feishu.cn/docx/doc_fs_001",
        "updated_at": "2026-10-25T10:00:00Z",
        "is_draft": False,
        "content": """# 售后政策与7天无理由退换货指南

## 1. 退货基本原则
客户自签收商品次日零时起7天内，在保证商品完好、配件齐全且不影响二次销售的前提下，可享受7天无理由退换货服务。

## 2. 运费承担规则
- 因商品质量问题、发错货或少件引起的退换货，运费由我司全额承担。
- 客户个人原因（如不喜欢、拍错规格）发起的无理由退货，寄回运费由客户自行承担。

## 3. 退款到账时效
退回商品经仓库签收验收合格后，系统将在1至3个工作日内原路退回退款金额。"""
    },
    "doc_fs_002": {
        "title": "飞书-VIP企业客户专属服务权益说明",
        "folder_path": "/客户成功中心/会员服务",
        "url": "https://feishu.cn/docx/doc_fs_002",
        "updated_at": "2026-10-26T14:30:00Z",
        "is_draft": False,
        "content": """# VIP企业客户专属服务权益说明

## 1. 专属技术座席秒级直连
企业认证客户享有专属VIP客服热线与在线优先排队通道，平均排队等待时长不超过5秒。

## 2. 故障响应SLA保障
- P1级紧急业务中断故障：15分钟内响应，2小时内提供应急临时恢复方案。
- P2级功能异常：30分钟内响应，4小时内排查并同步进度。

## 3. 专属客户成功顾问
每个VIP企业配备1对1专属技术专家与解决方案顾问，支持月度服务复盘报告与业务优化建议。"""
    },
    "doc_fs_draft_001": {
        "title": "飞书-内部草稿_双十一活动客服预备方案",
        "folder_path": "/内部机密/未发布草稿",
        "url": "https://feishu.cn/docx/doc_fs_draft_001",
        "updated_at": "2026-10-27T08:00:00Z",
        "is_draft": True,
        "content": """# 内部草稿：双十一活动客服预备方案 (未发布)
*注意：本方案尚处于内部草拟阶段，禁止对外流转*
- 预计咨询量将达到日常的3.5倍，已增配外包客服20人。"""
    }
}

class FeishuConnector(BaseKnowledgeConnector):
    """飞书开放平台文档/知识库连接器"""

    def __init__(self, credentials: Dict[str, Any], sync_rules: Dict[str, Any]):
        super().__init__(credentials, sync_rules)
        self.app_id = self.credentials.get("app_id", "").strip()
        self.app_secret = self.credentials.get("app_secret", "").strip()
        self.folder_token = self.credentials.get("folder_token", "").strip()
        # 判断是否进入模拟沙盒模式
        self.is_mock = (
            self.credentials.get("is_mock", False) or 
            self.app_id.startswith("mock_") or 
            self.app_id.startswith("test_") or
            not (self.app_id and self.app_secret)
        )

    async def _get_tenant_access_token(self) -> str:
        """调用飞书开放平台获取 tenant_access_token"""
        if self.is_mock:
            return "mock_feishu_tenant_token_12345"

        url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={
                "app_id": self.app_id,
                "app_secret": self.app_secret
            })
            data = resp.json()
            if resp.status_code == 200 and data.get("code") == 0:
                return data.get("tenant_access_token")
            raise ValueError(f"飞书鉴权失败: code={data.get('code')}, msg={data.get('msg')}")

    async def test_connection(self) -> Dict[str, Any]:
        """连通性与权限测试"""
        if self.is_mock:
            return {
                "success": True,
                "message": "飞书沙盒连接测试成功 (Mock Sandbox Mode)",
                "details": {
                    "mode": "SANDBOX_MOCK",
                    "accessible_docs_count": len(MOCK_FEISHU_DOCS),
                    "app_id": self.app_id or "mock_feishu_app"
                }
            }

        try:
            token = await self._get_tenant_access_token()
            msg = "飞书开放平台接入认证成功"
            doc_count = 0
            # 探测可访问的文档
            try:
                probe_docs = await self.list_documents()
                doc_count = len(probe_docs)
                if self.folder_token:
                    if probe_docs:
                        msg = f"飞书连接成功！已精准探测到指定文档: 《{probe_docs[0].title}》"
                    else:
                        msg = "飞书认证成功，但未探测到指定文档内容。请检查：1. 是否在文档右上角【分享】添加了本应用；2. 若是知识库文档，请在应用权限管理中开通【查看知识库 (wiki:wiki:readonly)】权限。"
                else:
                    if doc_count > 0:
                        msg = f"飞书连接成功！当前可探测到 {doc_count} 篇授权文档。"
                    else:
                        msg = "飞书认证成功，但暂未扫描到可见文档。请确认已在飞书云文档右上角【分享】将自建应用添加为协作者（阅读者）。"
            except Exception as pe:
                logger.warning(f"Probe documents error during test_connection: {pe}")

            return {
                "success": True,
                "message": msg,
                "details": {
                    "mode": "LIVE_API",
                    "app_id": self.app_id,
                    "token_prefix": token[:8] + "..." if token else "",
                    "doc_count": doc_count
                }
            }
        except Exception as e:
            logger.error(f"Feishu connection test failed: {e}")
            return {
                "success": False,
                "message": f"连接飞书失败: {str(e)}",
                "details": {"error": str(e)}
            }

    async def list_documents(self, last_sync_time: Optional[datetime] = None) -> List[SourceDocumentMetadata]:
        """扫描外部飞书文档目录"""
        if self.is_mock:
            docs = []
            for doc_id, item in MOCK_FEISHU_DOCS.items():
                docs.append(SourceDocumentMetadata(
                    source_doc_id=doc_id,
                    title=item["title"],
                    folder_path=item["folder_path"],
                    source_url=item["url"],
                    source_updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
                    source_version="v1",
                    is_draft=item["is_draft"],
                    is_deleted=False
                ))
            return docs

        token = await self._get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        # 优先读取知识空间 Wiki 节点，其次读取云空间目录文件或单篇文档
        docs: List[SourceDocumentMetadata] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            clean_token = self.folder_token.strip()
            wiki_token = None
            # 自动提取飞书文档各类 URL
            if "/wiki/" in clean_token:
                wiki_token = clean_token.split("/wiki/")[-1].split("?")[0].strip("/")
            elif "/docx/" in clean_token:
                clean_token = clean_token.split("/docx/")[-1].split("?")[0].strip("/")
            elif "/doc/" in clean_token:
                clean_token = clean_token.split("/doc/")[-1].split("?")[0].strip("/")

            # 1. 优先解析知识库 Wiki 节点
            if wiki_token:
                wiki_url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?token={wiki_token}"
                wiki_resp = await client.get(wiki_url, headers=headers)
                wiki_data = wiki_resp.json()
                if wiki_data.get("code") == 0:
                    node = wiki_data.get("data", {}).get("node", {})
                    obj_token = node.get("obj_token") or wiki_token
                    title = node.get("title") or "飞书知识库文档"
                    docs.append(SourceDocumentMetadata(
                        source_doc_id=obj_token,
                        title=title,
                        folder_path="/知识库",
                        source_url=f"https://feishu.cn/wiki/{wiki_token}",
                        source_version=str(node.get("node_token", "v1")),
                        is_draft=False,
                        is_deleted=False
                    ))
                    return docs
                else:
                    logger.warning(f"Wiki get_node returned code {wiki_data.get('code')}: {wiki_data.get('msg')}")
                    clean_token = wiki_token

            if clean_token:
                # 2. 如果是单篇 docx/doc 文档 Token (doxcn... 或 doccn...)
                if clean_token.startswith("dox") or clean_token.startswith("doc"):
                    doc_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{clean_token}"
                    doc_resp = await client.get(doc_url, headers=headers)
                    doc_data = doc_resp.json()
                    if doc_data.get("code") == 0:
                        doc_info = doc_data.get("data", {}).get("document", {})
                        docs.append(SourceDocumentMetadata(
                            source_doc_id=clean_token,
                            title=doc_info.get("title") or "飞书单篇文档",
                            folder_path="/单篇文档",
                            source_url=f"https://feishu.cn/docx/{clean_token}",
                            source_version=str(doc_info.get("revision_id", "1")),
                            is_draft=False,
                            is_deleted=False
                        ))
                        return docs

                # 3. 尝试作为文件夹 folder_token 读取
                url = f"https://open.feishu.cn/open-apis/drive/v1/files?folder_token={clean_token}"
                resp = await client.get(url, headers=headers)
                data = resp.json()
                if data.get("code") == 0:
                    for f in data.get("data", {}).get("files", []):
                        if f.get("type") in ("docx", "doc"):
                            docs.append(SourceDocumentMetadata(
                                source_doc_id=f.get("token"),
                                title=f.get("name"),
                                folder_path=f"/{clean_token}",
                                source_url=f.get("url", ""),
                                source_version=str(f.get("modified_time")),
                                is_draft=False,
                                is_deleted=False
                            ))
                elif not docs:
                    # 4. 兜底降级：尝试当做单篇文档探测
                    doc_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{clean_token}"
                    doc_resp = await client.get(doc_url, headers=headers)
                    doc_data = doc_resp.json()
                    if doc_data.get("code") == 0:
                        doc_info = doc_data.get("data", {}).get("document", {})
                        docs.append(SourceDocumentMetadata(
                            source_doc_id=clean_token,
                            title=doc_info.get("title") or "飞书单篇文档",
                            folder_path="/单篇文档",
                            source_url=f"https://feishu.cn/docx/{clean_token}",
                            source_version=str(doc_info.get("revision_id", "1")),
                            is_draft=False,
                            is_deleted=False
                        ))
            else:
                # 5. 未指定文档时：先扫描云空间根目录文件 (Drive)
                url = "https://open.feishu.cn/open-apis/drive/v1/files"
                resp = await client.get(url, headers=headers)
                data = resp.json()
                if data.get("code") == 0:
                    for f in data.get("data", {}).get("files", []):
                        if f.get("type") in ("docx", "doc"):
                            docs.append(SourceDocumentMetadata(
                                source_doc_id=f.get("token"),
                                title=f.get("name"),
                                folder_path="/云空间",
                                source_url=f.get("url", ""),
                                source_version=str(f.get("modified_time")),
                                is_draft=False,
                                is_deleted=False
                            ))

                # 6. 同时自动扫描可见的知识库空间 (Wiki Spaces)
                try:
                    spaces_url = "https://open.feishu.cn/open-apis/wiki/v2/spaces"
                    sp_resp = await client.get(spaces_url, headers=headers)
                    sp_data = sp_resp.json()
                    if sp_data.get("code") == 0:
                        for sp in sp_data.get("data", {}).get("items", []):
                            sp_id = sp.get("space_id")
                            sp_name = sp.get("name") or "知识库"
                            nodes_url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/{sp_id}/nodes"
                            nodes_resp = await client.get(nodes_url, headers=headers)
                            nodes_data = nodes_resp.json()
                            if nodes_data.get("code") == 0:
                                for node in nodes_data.get("data", {}).get("items", []):
                                    if node.get("obj_type") in ("docx", "doc"):
                                        docs.append(SourceDocumentMetadata(
                                            source_doc_id=node.get("obj_token"),
                                            title=node.get("title") or "未命名知识库文档",
                                            folder_path=f"/知识库/{sp_name}",
                                            source_url=f"https://feishu.cn/wiki/{node.get('node_token')}",
                                            source_version=str(node.get("node_token")),
                                            is_draft=False,
                                            is_deleted=False
                                        ))
                except Exception as wiki_err:
                    logger.warning(f"Failed to scan wiki spaces: {wiki_err}")

        return docs

    async def fetch_document_content(self, source_doc_id: str) -> RawSourceDocument:
        """获取飞书文档正文载荷"""
        if self.is_mock:
            item = MOCK_FEISHU_DOCS.get(source_doc_id)
            if not item:
                raise ValueError(f"Mock 飞书文档未找到: {source_doc_id}")
            meta = SourceDocumentMetadata(
                source_doc_id=source_doc_id,
                title=item["title"],
                folder_path=item["folder_path"],
                source_url=item["url"],
                source_updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
                source_version="v1",
                is_draft=item["is_draft"],
                is_deleted=False
            )
            cleaned = ContentCleaner.clean_markdown(item["content"])
            return RawSourceDocument(
                metadata=meta,
                raw_content=cleaned,
                content_format="MARKDOWN"
            )

        token = await self._get_tenant_access_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient(timeout=20.0) as client:
            real_doc_id = source_doc_id
            # 如果是 wiki 节点，先解析出真正的 docx obj_token
            if not real_doc_id.startswith("dox") and not real_doc_id.startswith("doc"):
                try:
                    w_url = f"https://open.feishu.cn/open-apis/wiki/v2/spaces/get_node?token={real_doc_id}"
                    w_resp = await client.get(w_url, headers=headers)
                    w_data = w_resp.json()
                    if w_data.get("code") == 0:
                        real_token = w_data.get("data", {}).get("node", {}).get("obj_token")
                        if real_token:
                            real_doc_id = real_token
                except Exception:
                    pass

            # 优先调用飞书 Docx 批量获取块内容接口
            url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{real_doc_id}/blocks"
            resp = await client.get(url, headers=headers)
            data = resp.json()
            if data.get("code") == 0:
                blocks = data.get("data", {}).get("items", [])
                md_content = ContentCleaner.convert_feishu_blocks_to_markdown(blocks)
                title = real_doc_id
                if blocks and blocks[0].get("block_type") == 1:
                    title = blocks[0].get("page", {}).get("elements", [{}])[0].get("text_run", {}).get("content", real_doc_id)
                meta = SourceDocumentMetadata(
                    source_doc_id=source_doc_id,
                    title=title,
                    source_url=f"https://feishu.cn/docx/{real_doc_id}"
                )
                return RawSourceDocument(
                    metadata=meta,
                    raw_content=md_content,
                    content_format="MARKDOWN"
                )
            
            # 降级尝试获取纯文本
            raw_url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{real_doc_id}/raw_content"
            resp2 = await client.get(raw_url, headers=headers)
            data2 = resp2.json()
            if data2.get("code") == 0:
                raw_text = data2.get("data", {}).get("content", "")
                meta = SourceDocumentMetadata(
                    source_doc_id=source_doc_id,
                    title=real_doc_id,
                    source_url=f"https://feishu.cn/docx/{real_doc_id}"
                )
                return RawSourceDocument(
                    metadata=meta,
                    raw_content=ContentCleaner.clean_markdown(raw_text),
                    content_format="MARKDOWN"
                )
            
            raise ValueError(f"拉取飞书文档内容失败: {data.get('msg') or data2.get('msg')}")

    async def parse_webhook_event(self, headers: Dict[str, str], payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        解析飞书开放平台 Webhook 事件推送
        支持:
        1. 飞书开通/配置回调时的 url_verification challenge
        2. 文档修改、创建、删除事件
        """
        # URL 验证握手
        if payload.get("type") == "url_verification":
            return {
                "action": "CHALLENGE",
                "challenge": payload.get("challenge")
            }

        # 事件回调
        header = payload.get("header", {})
        event_type = header.get("event_type") or payload.get("event", {}).get("type")
        event_body = payload.get("event", {})

        # 飞书常见事件: docx.document.updated_v1, drive.file.edit_v1, drive.file.deleted_v1
        file_token = (
            event_body.get("file_token") or 
            event_body.get("document_id") or 
            event_body.get("doc_token")
        )

        if not file_token:
            return None

        action = "UPDATE"
        if "deleted" in str(event_type).lower():
            action = "DELETE"
        elif "created" in str(event_type).lower():
            action = "CREATE"

        return {
            "action": action,
            "source_doc_id": file_token,
            "event_type": event_type
        }
