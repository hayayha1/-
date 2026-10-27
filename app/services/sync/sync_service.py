import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, func

from app.models.knowledge_source import KnowledgeSource, DocumentSourceMapping, SyncJob, SyncJobItem
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.chunk import DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.services.sync.base import BaseKnowledgeConnector, SourceDocumentMetadata
from app.services.sync.cleaner import ContentCleaner, compute_content_hash
from app.services.sync.feishu_connector import FeishuConnector
from app.services.sync.yuque_connector import YuqueConnector

logger = logging.getLogger(__name__)

class SyncService:
    """知识源同步核心引擎"""

    @classmethod
    def get_connector(cls, source: KnowledgeSource) -> BaseKnowledgeConnector:
        """根据数据源类型实例化对应的适配器"""
        stype = (source.source_type or "").upper()
        if stype == "FEISHU":
            return FeishuConnector(source.credentials, source.sync_rules)
        elif stype == "YUQUE":
            return YuqueConnector(source.credentials, source.sync_rules)
        else:
            raise ValueError(f"暂不支持的数据源类型: {source.source_type}")

    @classmethod
    async def test_connection(cls, source: KnowledgeSource) -> Dict[str, Any]:
        """执行连通性与权限测试"""
        from app.api.v1.documents import index_document_chunks
        connector = cls.get_connector(source)
        return await connector.test_connection()

    @classmethod
    async def execute_sync(
        cls, 
        source_id: str, 
        trigger_type: str = "MANUAL", 
        db: AsyncSession = None
    ) -> SyncJob:
        """
        执行完整或增量同步流程
        """
        source = await db.get(KnowledgeSource, source_id)
        if not source:
            raise ValueError(f"数据源不存在: {source_id}")

        kb = await db.get(KnowledgeBase, source.kb_id)
        if not kb:
            raise ValueError(f"绑定的知识库不存在: {source.kb_id}")

        # 1. 创建批次任务流水记录
        job = SyncJob(
            source_id=source.id,
            trigger_type=trigger_type,
            status="RUNNING"
        )
        db.add(job)
        await db.flush()

        from app.api.v1.documents import index_document_chunks
        connector = cls.get_connector(source)
        rules = source.sync_rules or {}
        exclude_draft = rules.get("exclude_draft", True)

        added_count = 0
        updated_count = 0
        deleted_count = 0
        unchanged_count = 0
        failed_count = 0

        try:
            # 2. 扫描源端元数据列表
            scanned_docs = await connector.list_documents(source.last_sync_at)
            job.total_scanned = len(scanned_docs)

            # 3. 加载本地现有的映射关系
            stmt_mappings = select(DocumentSourceMapping).where(DocumentSourceMapping.source_id == source.id)
            res_mappings = await db.execute(stmt_mappings)
            existing_mappings = {m.source_doc_id: m for m in res_mappings.scalars().all()}

            scanned_doc_ids = set()

            # 4. 遍历处理每篇文档
            for doc_meta in scanned_docs:
                scanned_doc_ids.add(doc_meta.source_doc_id)

                # 4.1 规则过滤：草稿跳过
                if doc_meta.is_draft and exclude_draft:
                    item = SyncJobItem(
                        job_id=job.id,
                        source_doc_id=doc_meta.source_doc_id,
                        doc_title=doc_meta.title,
                        action="SKIP",
                        status="SUCCESS",
                        error_message="已按规则跳过未发布草稿文档"
                    )
                    db.add(item)
                    unchanged_count += 1
                    continue

                try:
                    mapping = existing_mappings.get(doc_meta.source_doc_id)

                    # 4.2 本地尚未收录该文档 -> 【新增分支】
                    if not mapping:
                        if doc_meta.is_deleted:
                            continue

                        raw_doc = await connector.fetch_document_content(doc_meta.source_doc_id)
                        cleaned_content = ContentCleaner.clean_markdown(raw_doc.raw_content)
                        chash = compute_content_hash(cleaned_content)

                        new_doc = Document(
                            kb_id=source.kb_id,
                            name=doc_meta.title or "外部同步文档",
                            doc_type="MD",
                            content=cleaned_content,
                            file_size=len(cleaned_content.encode("utf-8")),
                            priority=1,
                            version=1,
                            status="ACTIVE",
                            source_origin="SYNC_ENGINE",
                            source_id=source.id,
                            is_external_controlled=True,
                            created_by="sync_engine",
                            updated_by="sync_engine"
                        )
                        db.add(new_doc)
                        await db.flush()

                        # 归档版本 1
                        init_v = DocumentVersion(
                            doc_id=new_doc.id,
                            version=1,
                            content=cleaned_content,
                            metadata_snapshot={
                                "source_type": source.source_type,
                                "source_doc_id": doc_meta.source_doc_id,
                                "source_url": doc_meta.source_url
                            },
                            created_by="sync_engine"
                        )
                        db.add(init_v)

                        # 切片向量化入库
                        await index_document_chunks(new_doc, db)

                        # 创建映射关系
                        new_mapping = DocumentSourceMapping(
                            source_id=source.id,
                            doc_id=new_doc.id,
                            source_doc_id=doc_meta.source_doc_id,
                            source_url=doc_meta.source_url,
                            source_title=doc_meta.title,
                            source_folder_path=doc_meta.folder_path,
                            content_hash=chash,
                            source_updated_at=doc_meta.source_updated_at,
                            source_version=doc_meta.source_version,
                            sync_status="SYNCED"
                        )
                        db.add(new_mapping)

                        db.add(SyncJobItem(
                            job_id=job.id,
                            source_doc_id=doc_meta.source_doc_id,
                            doc_title=new_doc.name,
                            action="CREATE",
                            status="SUCCESS"
                        ))
                        added_count += 1

                    # 4.3 本地已收录该文档 -> 【比对与变更分支】
                    else:
                        target_doc = await db.get(Document, mapping.doc_id)
                        if not target_doc:
                            # 异常孤立映射，修复重建文档
                            mapping.sync_status = "ORPHANED"
                            continue

                        # 4.3.1 源端已标记删除/回收站 -> 【软删除分支】
                        if doc_meta.is_deleted:
                            target_doc.status = "INACTIVE"
                            # 注销切片向量
                            await db.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == target_doc.id))
                            mapping.sync_status = "ORPHANED"

                            db.add(SyncJobItem(
                                job_id=job.id,
                                source_doc_id=doc_meta.source_doc_id,
                                doc_title=target_doc.name,
                                action="SOFT_DELETE",
                                status="SUCCESS",
                                error_message="源端文档已删除，本地已置为失效并下线检索"
                            ))
                            deleted_count += 1
                            continue

                        # 4.3.2 拉取新内容计算 Hash
                        raw_doc = await connector.fetch_document_content(doc_meta.source_doc_id)
                        cleaned_content = ContentCleaner.clean_markdown(raw_doc.raw_content)
                        new_hash = compute_content_hash(cleaned_content)

                        # 内容无变化 -> 【跳过分支】
                        if new_hash == mapping.content_hash:
                            # 仅检查是否有移动目录或更名
                            if doc_meta.title and doc_meta.title != target_doc.name:
                                target_doc.name = doc_meta.title
                                mapping.source_title = doc_meta.title
                            if doc_meta.folder_path:
                                mapping.source_folder_path = doc_meta.folder_path

                            db.add(SyncJobItem(
                                job_id=job.id,
                                source_doc_id=doc_meta.source_doc_id,
                                doc_title=target_doc.name,
                                action="SKIP",
                                status="SUCCESS",
                                error_message="文档内容散列一致，无实质变动"
                            ))
                            unchanged_count += 1

                        # 内容发生了实质变化 -> 【更新版本分支】
                        else:
                            # 冲突策略检查：本地是否已人工加锁
                            if mapping.is_locked_manually:
                                db.add(SyncJobItem(
                                    job_id=job.id,
                                    source_doc_id=doc_meta.source_doc_id,
                                    doc_title=target_doc.name,
                                    action="SKIP",
                                    status="SUCCESS",
                                    error_message="文档已在知识库本地加锁保护，已跳过外部覆盖"
                                ))
                                unchanged_count += 1
                            else:
                                next_v = (target_doc.version or 1) + 1
                                target_doc.version = next_v
                                target_doc.content = cleaned_content
                                target_doc.name = doc_meta.title or target_doc.name
                                target_doc.file_size = len(cleaned_content.encode("utf-8"))
                                target_doc.status = "ACTIVE"
                                target_doc.updated_by = "sync_engine"

                                # 归档新版本
                                new_v_snapshot = DocumentVersion(
                                    doc_id=target_doc.id,
                                    version=next_v,
                                    content=cleaned_content,
                                    metadata_snapshot={
                                        "source_type": source.source_type,
                                        "source_doc_id": doc_meta.source_doc_id,
                                        "source_url": doc_meta.source_url
                                    },
                                    created_by="sync_engine"
                                )
                                db.add(new_v_snapshot)

                                # 重新切片与向量入库
                                await index_document_chunks(target_doc, db)

                                # 更新映射信息
                                mapping.content_hash = new_hash
                                mapping.source_updated_at = doc_meta.source_updated_at
                                mapping.source_title = doc_meta.title
                                mapping.source_folder_path = doc_meta.folder_path
                                mapping.sync_status = "SYNCED"

                                db.add(SyncJobItem(
                                    job_id=job.id,
                                    source_doc_id=doc_meta.source_doc_id,
                                    doc_title=target_doc.name,
                                    action="UPDATE",
                                    status="SUCCESS"
                                ))
                                updated_count += 1

                except Exception as doc_err:
                    logger.error(f"Error syncing document {doc_meta.source_doc_id}: {doc_err}", exc_info=True)
                    db.add(SyncJobItem(
                        job_id=job.id,
                        source_doc_id=doc_meta.source_doc_id,
                        doc_title=doc_meta.title,
                        action="ERROR",
                        status="FAILED",
                        error_message=str(doc_err)
                    ))
                    failed_count += 1

            # 5. 汇总任务结果
            job.added_count = added_count
            job.updated_count = updated_count
            job.deleted_count = deleted_count
            job.unchanged_count = unchanged_count
            job.failed_count = failed_count
            job.finished_at = datetime.now()

            if failed_count == 0:
                job.status = "COMPLETED"
            elif added_count + updated_count > 0:
                job.status = "PARTIAL_SUCCESS"
            else:
                job.status = "FAILED"

            source.last_sync_at = datetime.now()
            source.last_sync_status = job.status

            await db.commit()
            await db.refresh(job)
            return job

        except Exception as e:
            logger.error(f"Sync execution failed for source {source_id}: {e}", exc_info=True)
            job.status = "FAILED"
            job.finished_at = datetime.now()
            job.error_summary = str(e)
            source.last_sync_status = "ERROR"
            await db.commit()
            await db.refresh(job)
            return job

    @classmethod
    async def handle_webhook(
        cls, 
        source_id: str, 
        headers: Dict[str, str], 
        payload: Dict[str, Any], 
        db: AsyncSession
    ) -> Dict[str, Any]:
        """处理外部系统 Webhook 回调推送"""
        source = await db.get(KnowledgeSource, source_id)
        if not source:
            raise ValueError(f"数据源不存在: {source_id}")

        from app.api.v1.documents import index_document_chunks
        connector = cls.get_connector(source)
        event_info = await connector.parse_webhook_event(headers, payload)

        if not event_info:
            return {"status": "ignored", "message": "非目标文档变动事件"}

        # 飞书 URL 验证握手直接透传 challenge
        if event_info.get("action") == "CHALLENGE":
            return {"challenge": event_info.get("challenge")}

        # 触发一次定向增量同步
        job = await cls.execute_sync(source_id=source.id, trigger_type="WEBHOOK", db=db)
        return {
            "status": "success",
            "message": "Webhook 事件接收并触发增量同步成功",
            "job_id": job.id,
            "job_status": job.status
        }
