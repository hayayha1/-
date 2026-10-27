import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.main import app
from app.db.session import init_db, async_session_maker
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_source import KnowledgeSource, DocumentSourceMapping, SyncJob
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.chunk import DocumentChunk
from app.services.sync.cleaner import ContentCleaner, compute_content_hash
from app.services.sync.feishu_connector import FeishuConnector, MOCK_FEISHU_DOCS
from app.services.sync.yuque_connector import YuqueConnector, MOCK_YUQUE_DOCS
from app.services.sync.sync_service import SyncService

@pytest.mark.asyncio
async def test_content_cleaner_and_hash():
    raw_md = """# 标题
<!-- 注释部分 -->
<script>alert(1);</script>
[TOC]

正文内容

"""
    cleaned = ContentCleaner.clean_markdown(raw_md)
    assert "<!-- 注释部分 -->" not in cleaned
    assert "<script>" not in cleaned
    assert "[TOC]" not in cleaned
    assert "# 标题" in cleaned
    assert "正文内容" in cleaned

    h1 = compute_content_hash(cleaned)
    h2 = compute_content_hash(cleaned + "   \n")
    # 清洗后的散列一致性
    assert h1 == h2

@pytest.mark.asyncio
async def test_feishu_connector_sandbox():
    connector = FeishuConnector(credentials={"is_mock": True}, sync_rules={"exclude_draft": True})
    
    # 1. 连通性测试
    conn_res = await connector.test_connection()
    assert conn_res["success"] is True
    assert conn_res["details"]["mode"] == "SANDBOX_MOCK"

    # 2. 列出文档
    docs = await connector.list_documents()
    assert len(docs) == len(MOCK_FEISHU_DOCS)
    doc_ids = [d.source_doc_id for d in docs]
    assert "doc_fs_001" in doc_ids
    assert "doc_fs_draft_001" in doc_ids

    # 3. 读取内容
    raw_doc = await connector.fetch_document_content("doc_fs_001")
    assert "售后政策与7天无理由退换货指南" in raw_doc.raw_content
    assert raw_doc.metadata.title == "飞书-售后政策与7天无理由退换货指南"

    # 4. Webhook 解析
    # 握手
    challenge_ev = await connector.parse_webhook_event({}, {"type": "url_verification", "challenge": "xyz123"})
    assert challenge_ev["action"] == "CHALLENGE"
    assert challenge_ev["challenge"] == "xyz123"

    # 文档变动
    doc_ev = await connector.parse_webhook_event({}, {
        "header": {"event_type": "docx.document.updated_v1"},
        "event": {"document_id": "doc_fs_001"}
    })
    assert doc_ev["action"] == "UPDATE"
    assert doc_ev["source_doc_id"] == "doc_fs_001"

@pytest.mark.asyncio
async def test_yuque_connector_sandbox():
    connector = YuqueConnector(credentials={"is_mock": True, "repo_slug": "test/kb"}, sync_rules={})
    
    # 1. 连通性测试
    conn_res = await connector.test_connection()
    assert conn_res["success"] is True

    # 2. 列出文档
    docs = await connector.list_documents()
    assert len(docs) == len(MOCK_YUQUE_DOCS)

    # 3. 读取内容
    raw_doc = await connector.fetch_document_content("yq_doc_101")
    assert "SaaS系统快速部署与初始化手册" in raw_doc.raw_content

    # 4. Webhook 解析
    yq_ev = await connector.parse_webhook_event({}, {
        "data": {
            "slug": "yq_doc_101",
            "title": "部署手册更新",
            "action_type": "update"
        }
    })
    assert yq_ev["action"] == "UPDATE"
    assert yq_ev["source_doc_id"] == "yq_doc_101"

@pytest.mark.asyncio
async def test_sync_engine_full_lifecycle():
    """测试完整同步生命周期：初始化 -> 增量未变 -> 源端更新生成新版本 -> 软删除"""
    await init_db()

    async with async_session_maker() as db:
        # 1. 准备测试知识库
        kb = KnowledgeBase(
            product_code="SYNC_TEST_PROD",
            name="同步测试专用知识库",
            description="用于验证飞书/语雀同步引擎",
            created_by="tester",
            updated_by="tester"
        )
        db.add(kb)
        await db.flush()

        # 2. 创建飞书测试数据源（沙盒模式）
        source = KnowledgeSource(
            kb_id=kb.id,
            name="飞书售后文档同步源",
            source_type="FEISHU",
            credentials={"is_mock": True},
            sync_mode="POLL_INCREMENTAL",
            sync_rules={"exclude_draft": True, "conflict_strategy": "SOURCE_OVERWRITE"},
            created_by="tester",
            updated_by="tester"
        )
        db.add(source)
        await db.commit()
        await db.refresh(source)

        # 3. 执行首次全量同步
        job1 = await SyncService.execute_sync(source.id, trigger_type="MANUAL", db=db)
        assert job1.status == "COMPLETED"
        # 2 篇正式文档新增，1 篇草稿被跳过
        assert job1.added_count == 2
        assert job1.unchanged_count == 1
        assert job1.failed_count == 0

        # 验证数据库中生成的文档
        docs_stmt = select(Document).where(Document.kb_id == kb.id)
        docs = (await db.execute(docs_stmt)).scalars().all()
        assert len(docs) == 2
        for d in docs:
            assert d.source_origin == "SYNC_ENGINE"
            assert d.source_id == source.id
            assert d.version == 1
            assert d.status == "ACTIVE"

            # 验证切片是否已生成
            chunks_stmt = select(DocumentChunk).where(DocumentChunk.doc_id == d.id)
            chunks = (await db.execute(chunks_stmt)).scalars().all()
            assert len(chunks) > 0

        # 4. 再次执行同步（内容无任何变化）
        job2 = await SyncService.execute_sync(source.id, trigger_type="MANUAL", db=db)
        assert job2.status == "COMPLETED"
        assert job2.added_count == 0
        assert job2.updated_count == 0
        assert job2.unchanged_count == 3  # 2篇内容未变 + 1篇草稿

        # 5. 模拟源端内容修改 -> 触发版本递增
        # 修改 mock 字典里的内容
        original_content = MOCK_FEISHU_DOCS["doc_fs_001"]["content"]
        try:
            MOCK_FEISHU_DOCS["doc_fs_001"]["content"] += "\n\n## 4. 补充特别说明\n大促期间退换货时效自动延长至15天。"
            
            job3 = await SyncService.execute_sync(source.id, trigger_type="MANUAL", db=db)
            assert job3.status == "COMPLETED"
            assert job3.updated_count == 1

            # 验证文档版本自增为 2，并且产生了两条版本快照
            m_stmt = select(DocumentSourceMapping).where(
                DocumentSourceMapping.source_id == source.id,
                DocumentSourceMapping.source_doc_id == "doc_fs_001"
            )
            mapping = (await db.execute(m_stmt)).scalars().first()
            updated_doc = await db.get(Document, mapping.doc_id)
            assert updated_doc.version == 2
            assert "大促期间退换货时效自动延长至15天" in updated_doc.content

            v_stmt = select(DocumentVersion).where(DocumentVersion.doc_id == updated_doc.id).order_by(DocumentVersion.version.asc())
            versions = (await db.execute(v_stmt)).scalars().all()
            assert len(versions) == 2
            assert versions[0].version == 1
            assert versions[1].version == 2
        finally:
            # 还原 mock 数据
            MOCK_FEISHU_DOCS["doc_fs_001"]["content"] = original_content

@pytest.mark.asyncio
async def test_sync_api_endpoints():
    """测试通过 FastAPI 接口管理数据源与触发同步"""
    await init_db()

    transport = ASGITransport(app=app)
    admin_headers = {"X-User-Role": "admin", "X-User-Id": "admin_001"}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. 获取一个已存在的知识库
        res_kbs = await ac.get("/api/v1/kbs", headers=admin_headers)
        assert res_kbs.status_code == 200
        kbs = res_kbs.json()
        assert len(kbs) > 0
        target_kb_id = kbs[0]["id"]

        # 2. 临时测试连通性 (保存前预检)
        res_trans_test = await ac.post("/api/v1/sync/sources/test-transient", json={
            "kb_id": target_kb_id,
            "name": "临时测试源",
            "source_type": "YUQUE",
            "credentials": {"is_mock": True, "repo_slug": "demo/kb"}
        }, headers=admin_headers)
        assert res_trans_test.status_code == 200
        assert res_trans_test.json()["success"] is True

        # 3. 创建语雀数据源
        res_create = await ac.post("/api/v1/sync/sources", json={
            "kb_id": target_kb_id,
            "name": "语雀SaaS快速接入库",
            "source_type": "YUQUE",
            "credentials": {"is_mock": True, "repo_slug": "demo/kb"},
            "sync_mode": "POLL_INCREMENTAL",
            "sync_rules": {"exclude_draft": True}
        }, headers=admin_headers)
        assert res_create.status_code == 200
        source_data = res_create.json()
        source_id = source_data["id"]
        assert source_data["name"] == "语雀SaaS快速接入库"

        # 4. 获取数据源列表
        res_list = await ac.get("/api/v1/sync/sources", headers=admin_headers)
        assert res_list.status_code == 200
        sources = res_list.json()
        assert any(s["id"] == source_id for s in sources)

        # 5. 针对已有数据源测试连通性
        res_test = await ac.post(f"/api/v1/sync/sources/{source_id}/test", headers=admin_headers)
        assert res_test.status_code == 200
        assert res_test.json()["success"] is True

        # 6. 触发一次同步
        res_trigger = await ac.post(f"/api/v1/sync/sources/{source_id}/trigger", headers=admin_headers)
        assert res_trigger.status_code == 200
        job_data = res_trigger.json()
        assert job_data["status"] == "COMPLETED"
        assert job_data["added_count"] == 2
        job_id = job_data["id"]

        # 7. 查看任务详情及明细
        res_job_detail = await ac.get(f"/api/v1/sync/jobs/{job_id}", headers=admin_headers)
        assert res_job_detail.status_code == 200
        detail = res_job_detail.json()
        assert len(detail["items"]) >= 2
        action_types = [item["action"] for item in detail["items"]]
        assert "CREATE" in action_types

        # 8. Webhook 回调接收
        res_webhook = await ac.post(
            f"/api/v1/sync/webhook/{source_id}",
            json={
                "data": {
                    "slug": "yq_doc_101",
                    "title": "部署手册变动",
                    "action_type": "update"
                }
            }
        )
        assert res_webhook.status_code == 200
        assert res_webhook.json()["status"] == "success"

        # 9. 清理测试数据源
        res_del = await ac.delete(f"/api/v1/sync/sources/{source_id}", headers=admin_headers)
        assert res_del.status_code == 200
