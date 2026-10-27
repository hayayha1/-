import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import httpx

from app.services.sync.base import BaseKnowledgeConnector, SourceDocumentMetadata, RawSourceDocument
from app.services.sync.cleaner import ContentCleaner

logger = logging.getLogger(__name__)

# 内置沙盒/模拟语雀知识库数据
MOCK_YUQUE_DOCS = {
    "yq_doc_101": {
        "slug": "yq_doc_101",
        "title": "语雀-SaaS系统快速部署与初始化手册",
        "folder_path": "/技术架构与运维",
        "url": "https://www.yuque.com/saas/docs/yq_doc_101",
        "updated_at": "2026-10-25T11:20:00Z",
        "is_draft": False,
        "content": """# SaaS系统快速部署与初始化手册

## 1. 部署前置基础环境
- **操作系统推荐**：Ubuntu 22.04 LTS 或 Debian 12。
- **计算资源规格**：生产环境建议不低于 4核 CPU / 16GB 内存。
- **持久化依赖**：PostgreSQL 15+ 搭配 pgvector 向量插件，以及 Redis 7.0 缓存。

## 2. 环境变量配置
请在根目录下创建 `.env` 文件，务必配置合法的 `DATABASE_URL` 与 `OPENAI_API_KEY`（或本地 Ollama/vLLM 服务地址）。

## 3. 首次启动与管理员初始化
执行 `python run.py`，系统将自动检查数据库表并填充基础产品库，默认生成超管账号 `admin`，初始密码 `admin123`，请上线后第一时间重置。"""
    },
    "yq_doc_102": {
        "slug": "yq_doc_102",
        "title": "语雀-智能客服检索阈值与Rerank调优实战",
        "folder_path": "/AI与算法工程",
        "url": "https://www.yuque.com/saas/docs/yq_doc_102",
        "updated_at": "2026-10-26T16:45:00Z",
        "is_draft": False,
        "content": """# 智能客服检索阈值与Rerank调优实战

## 1. 混合检索分流机制
知识库采用 BM25 关键词匹配 + 密集向量语义召回双路并发检索。召回后经由 Cross-Encoder 语义重排模型进行统一打分（0 ~ 1.0）。

## 2. 关键业务阈值配置推荐
- **直接应答阈值 (Direct Threshold)**：建议配置在 `0.80`。当重排最高分 >= 0.80 时，判定知识完全命中，直接提取上下文生成回答。
- **澄清反问阈值 (Clarify Threshold)**：建议配置在 `0.50`。当最高分在 0.50 ~ 0.80 之间时，触发引导反问或澄清意图。
- **转人工/兜底**：当最高分 < 0.50 时，主动提示未找到匹配知识并建议转人工客服。"""
    },
    "yq_draft_201": {
        "slug": "yq_draft_201",
        "title": "语雀-草稿_新财年产品商业化报价表",
        "folder_path": "/商务与定价",
        "url": "https://www.yuque.com/saas/docs/yq_draft_201",
        "updated_at": "2026-10-27T09:10:00Z",
        "is_draft": True,
        "content": """# 新财年产品商业化报价表 (草稿未发布)
*内部评估文件，未经财务审批前严禁公开*"""
    }
}

class YuqueConnector(BaseKnowledgeConnector):
    """语雀 OpenAPI v2 文档与知识库连接器"""

    def __init__(self, credentials: Dict[str, Any], sync_rules: Dict[str, Any]):
        super().__init__(credentials, sync_rules)
        self.token = self.credentials.get("token", "").strip()
        self.repo_slug = self.credentials.get("repo_slug", "").strip()  # 如 "namespace/repo"
        self.base_url = "https://www.yuque.com/api/v2"
        # 判断是否进入模拟沙盒模式
        self.is_mock = (
            self.credentials.get("is_mock", False) or 
            self.token.startswith("mock_") or 
            self.token.startswith("test_") or 
            not self.token
        )

    async def test_connection(self) -> Dict[str, Any]:
        """连通性与权限测试"""
        if self.is_mock:
            return {
                "success": True,
                "message": "语雀沙盒连接测试成功 (Mock Sandbox Mode)",
                "details": {
                    "mode": "SANDBOX_MOCK",
                    "repo_slug": self.repo_slug or "mock/customer-service-kb",
                    "accessible_docs_count": len(MOCK_YUQUE_DOCS)
                }
            }

        headers = {
            "X-Auth-Token": self.token,
            "User-Agent": "SmartCustomerService-SyncEngine/1.0"
        }
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 1. 验证用户 Token 有效性
                user_res = await client.get(f"{self.base_url}/user", headers=headers)
                if user_res.status_code != 200:
                    return {
                        "success": False,
                        "message": f"语雀 Token 认证失败: HTTP {user_res.status_code} ({user_res.text})",
                        "details": {"status_code": user_res.status_code}
                    }
                user_info = user_res.json().get("data", {})

                # 2. 若指定了知识库，进一步检查知识库访问权限
                if self.repo_slug:
                    repo_res = await client.get(f"{self.base_url}/repos/{self.repo_slug}", headers=headers)
                    if repo_res.status_code != 200:
                        return {
                            "success": False,
                            "message": f"无法访问语雀知识库 '{self.repo_slug}'，请检查名称或授权: HTTP {repo_res.status_code}",
                            "details": {"repo_slug": self.repo_slug}
                        }
                    repo_info = repo_res.json().get("data", {})
                    return {
                        "success": True,
                        "message": f"语雀连接成功！已授权访问知识库: {repo_info.get('name')}",
                        "details": {
                            "user": user_info.get("name"),
                            "repo_name": repo_info.get("name"),
                            "doc_count": repo_info.get("items_count")
                        }
                    }

                return {
                    "success": True,
                    "message": f"语雀认证成功 (用户: {user_info.get('name')})",
                    "details": {"user": user_info.get("name")}
                }
        except Exception as e:
            logger.error(f"Yuque connection test error: {e}")
            return {
                "success": False,
                "message": f"语雀接口网络访问异常: {str(e)}",
                "details": {"error": str(e)}
            }

    async def list_documents(self, last_sync_time: Optional[datetime] = None) -> List[SourceDocumentMetadata]:
        """扫描外部语雀文档列表"""
        if self.is_mock:
            docs = []
            for doc_id, item in MOCK_YUQUE_DOCS.items():
                docs.append(SourceDocumentMetadata(
                    source_doc_id=item["slug"],
                    title=item["title"],
                    folder_path=item["folder_path"],
                    source_url=item["url"],
                    source_updated_at=datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")),
                    source_version="v1",
                    is_draft=item["is_draft"],
                    is_deleted=False
                ))
            return docs

        headers = {
            "X-Auth-Token": self.token,
            "User-Agent": "SmartCustomerService-SyncEngine/1.0"
        }
        docs: List[SourceDocumentMetadata] = []
        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"{self.base_url}/repos/{self.repo_slug}/docs"
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                items = resp.json().get("data", [])
                for d in items:
                    # status: 0 表示草稿，1 表示已发布
                    is_draft = (d.get("status") == 0)
                    up_at_str = d.get("updated_at")
                    up_at = datetime.fromisoformat(up_at_str.replace("Z", "+00:00")) if up_at_str else None
                    docs.append(SourceDocumentMetadata(
                        source_doc_id=str(d.get("slug") or d.get("id")),
                        title=d.get("title") or "无标题文档",
                        folder_path=f"/{self.repo_slug}",
                        source_url=f"https://www.yuque.com/{self.repo_slug}/{d.get('slug')}",
                        source_updated_at=up_at,
                        source_version=str(d.get("version", "1")),
                        is_draft=is_draft,
                        is_deleted=False
                    ))
            else:
                raise ValueError(f"拉取语雀文档列表失败: HTTP {resp.status_code} ({resp.text})")

        return docs

    async def fetch_document_content(self, source_doc_id: str) -> RawSourceDocument:
        """根据 slug/id 获取语雀文档完整正文 (Markdown)"""
        if self.is_mock:
            item = MOCK_YUQUE_DOCS.get(source_doc_id)
            if not item:
                raise ValueError(f"Mock 语雀文档未找到: {source_doc_id}")
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

        headers = {
            "X-Auth-Token": self.token,
            "User-Agent": "SmartCustomerService-SyncEngine/1.0"
        }
        async with httpx.AsyncClient(timeout=20.0) as client:
            # raw=1 强制语雀返回原生 Markdown
            url = f"{self.base_url}/repos/{self.repo_slug}/docs/{source_doc_id}?raw=1"
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                body = data.get("body", "") or data.get("body_html", "")
                title = data.get("title") or source_doc_id
                up_at_str = data.get("updated_at")
                up_at = datetime.fromisoformat(up_at_str.replace("Z", "+00:00")) if up_at_str else None
                meta = SourceDocumentMetadata(
                    source_doc_id=source_doc_id,
                    title=title,
                    folder_path=f"/{self.repo_slug}",
                    source_url=f"https://www.yuque.com/{self.repo_slug}/{source_doc_id}",
                    source_updated_at=up_at,
                    source_version=str(data.get("version", "1")),
                    is_draft=(data.get("status") == 0),
                    is_deleted=False
                )
                cleaned = ContentCleaner.clean_markdown(body)
                return RawSourceDocument(
                    metadata=meta,
                    raw_content=cleaned,
                    content_format="MARKDOWN"
                )
            raise ValueError(f"获取语雀文档内容失败: HTTP {resp.status_code} ({resp.text})")

    async def parse_webhook_event(self, headers: Dict[str, str], payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        解析语雀 Webhook 推送
        语雀典型格式:
        {
            "data": {
                "id": 123456,
                "slug": "abc",
                "title": "测试文档",
                "action_type": "update" / "publish" / "delete"
            }
        }
        """
        data = payload.get("data", {})
        slug = str(data.get("slug") or data.get("id") or "")
        action_type = str(data.get("action_type", "")).lower()

        if not slug:
            return None

        action = "UPDATE"
        if action_type in ("delete", "destroy"):
            action = "DELETE"
        elif action_type in ("create", "publish"):
            action = "CREATE"

        return {
            "action": action,
            "source_doc_id": slug,
            "title": data.get("title", ""),
            "action_type": action_type
        }
