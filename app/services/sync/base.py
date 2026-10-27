from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class SourceDocumentMetadata:
    """源端文档元数据结构"""
    source_doc_id: str
    title: str
    folder_path: str = ""
    source_url: str = ""
    source_updated_at: Optional[datetime] = None
    source_version: Optional[str] = None
    is_draft: bool = False
    is_deleted: bool = False

@dataclass
class RawSourceDocument:
    """源端拉取出的原始正文载荷"""
    metadata: SourceDocumentMetadata
    raw_content: str
    content_format: str = "MARKDOWN"  # MARKDOWN, FEISHU_BLOCKS, HTML
    extra_attributes: Dict[str, Any] = field(default_factory=dict)

class BaseKnowledgeConnector(ABC):
    """知识源通用抽象连接器接口"""

    def __init__(self, credentials: Dict[str, Any], sync_rules: Dict[str, Any]):
        self.credentials = credentials or {}
        self.sync_rules = sync_rules or {}

    @abstractmethod
    async def test_connection(self) -> Dict[str, Any]:
        """连通性与权限测试：返回成功标识或详细失败原因"""
        pass

    @abstractmethod
    async def list_documents(self, last_sync_time: Optional[datetime] = None) -> List[SourceDocumentMetadata]:
        """扫描源端文档目录，返回元数据列表"""
        pass

    @abstractmethod
    async def fetch_document_content(self, source_doc_id: str) -> RawSourceDocument:
        """根据源文档 ID 获取正文与完整属性"""
        pass

    @abstractmethod
    async def parse_webhook_event(self, headers: Dict[str, str], payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """解析 Webhook 回调事件，提取变动的 source_doc_id 与事件类型"""
        pass
