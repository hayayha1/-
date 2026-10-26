import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.core.config import settings
from app.db.session import init_db, async_session_maker
from app.api.v1 import api_router
from app.models.knowledge_base import KnowledgeBase
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.chunk import DocumentChunk
from app.models.faq import FAQItem
from app.services.chunking.splitter import DocumentChunker
from app.services.model_service import model_service
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

async def seed_demo_data():
    """预填充演示知识库与知识数据"""
    async with async_session_maker() as db:
        res = await db.execute(select(KnowledgeBase).limit(1))
        existing_kb = res.scalars().first()
        if existing_kb:
            # 即使知识库已存在，也确保会话被初始化
            from app.models.chat_session import ChatSession, ChatMessage
            import datetime
            sess_res = await db.execute(select(ChatSession).limit(1))
            if not sess_res.scalars().first():
                s1 = ChatSession(
                    customer_id="user_001",
                    customer_name="张先生 · 钻石VIP客户",
                    product_code="SAAS_CLOUD",
                    status="AI_SERVICE",
                    last_message="怎么退款",
                    last_message_at=datetime.datetime.now() - datetime.timedelta(minutes=5)
                )
                db.add(s1)
                await db.flush()
                db.add(ChatMessage(
                    session_id=s1.id,
                    role="user",
                    content="怎么退款",
                    raw_query="怎么退款"
                ))
                db.add(ChatMessage(
                    session_id=s1.id,
                    role="assistant",
                    content="购买本平台 SaaS 订阅服务或云资源实例后，自订单支付之日起 7 个自然日内，若未产生超出基础配额 5% 的计算资源消耗，用户均享有无理由全额退款权益。",
                    reply_type="DIRECT_ANSWER",
                    confidence_score=0.892,
                    raw_query="怎么退款"
                ))
                await db.commit()
            return

        logger.info("Initializing Seed Demo Knowledge Base and Documents...")
        
        # 1. 创建默认演示知识库
        demo_kb = KnowledgeBase(
            product_code="SAAS_CLOUD",
            name="智能云平台服务与售后知识库",
            description="覆盖SaaS平台账号管理、退款售后规范、计费规则与发票开具指引。",
            status="ACTIVE",
            doc_count=0,
            chunk_count=0,
            config={
                "bm25_top_k": 15,
                "vector_top_k": 15,
                "rerank_top_k": 3,
                "threshold_direct": 0.80,
                "threshold_clarify": 0.50
            },
            created_by="system",
            updated_by="system"
        )
        db.add(demo_kb)
        await db.flush()

        # 2. 导入演示 Markdown 文档
        md_content = """# 智能云平台售后退款与服务保障规范

## 一、退款申请核心条件
购买本平台 SaaS 订阅服务或云资源实例后，自订单支付之日起 7 个自然日内，若未产生超出基础配额 5% 的计算资源消耗，用户均享有无理由全额退款权益。

## 二、退款申请标准流程
1. 登录用户控制台，点击右上角【费用中心】；
2. 进入【订单管理 - 退订与退款】页面；
3. 选择需要退款的订单编号，点击【申请退订】；
4. 填写退款原因后提交，系统将在 1~3 个工作日内原路返回支付账户。

## 三、发票开具与退款冲突说明
若用户在退款前已开具增值税电子普通发票或专用发票，退款审批前需先在财务中心作废发票或由我方开具红字发票冲抵。"""

        doc_md = Document(
            kb_id=demo_kb.id,
            name="退款政策与售后服务保障指南.md",
            doc_type="MD",
            content=md_content,
            file_size=len(md_content.encode("utf-8")),
            priority=3,
            version=1,
            status="ACTIVE",
            created_by="admin",
            updated_by="admin"
        )
        db.add(doc_md)
        await db.flush()

        # 版本归档
        db.add(DocumentVersion(
            doc_id=doc_md.id,
            version=1,
            content=md_content,
            created_by="admin"
        ))

        # 切片与向量化
        md_chunks = DocumentChunker.chunk_markdown(md_content, {"id": doc_md.id, "name": doc_md.name, "priority": 3})
        for c in md_chunks:
            emb = await model_service.get_embedding(c.content)
            db.add(DocumentChunk(
                kb_id=demo_kb.id,
                doc_id=doc_md.id,
                chunk_index=c.chunk_index,
                content=c.content,
                embedding=emb,
                heading_path=c.heading_path,
                priority=3,
                metadata_info=c.metadata
            ))

        # 3. 导入演示 FAQ 文档
        faq_items_data = [
            {
                "standard_question": "怎么办理产品退款？",
                "similar_questions": ["我要退钱", "如何申请退款流程", "购买后可以退款吗", "退订服务操作步骤"],
                "answer": "购买 7 天内且未消耗核心配额的用户，请进入【控制台 > 费用中心 > 退订与退款】提交申请，款项将在 1~3 个工作日原路退回。",
                "priority": 5
            },
            {
                "standard_question": "如何重置登录密码？",
                "similar_questions": ["忘记密码怎么办", "修改登录密码", "密码找回流程"],
                "answer": "在登录页点击【忘记密码】，通过绑定的企业邮箱或手机号码接收验证码即可完成密码重置。",
                "priority": 2
            },
            {
                "standard_question": "电子发票多长时间能开具出来？",
                "similar_questions": ["开票周期是多久", "什么时候能拿到发票", "申请发票需要几天"],
                "answer": "增值税电子普通发票提交后 1 小时内自动推送到您的预留邮箱；专用发票需人工审核，预计 1~2 个工作日开具并寄出。",
                "priority": 2
            }
        ]

        faq_text = "\n\n".join([
            f"【标准问题】: {item['standard_question']}\n" +
            (f"【相似问法】: {'；'.join(item['similar_questions'])}\n" if item.get('similar_questions') else "") +
            f"【权威解答】: {item['answer']}"
            for item in faq_items_data
        ])

        doc_faq = Document(
            kb_id=demo_kb.id,
            name="核心业务高频FAQ问答集",
            doc_type="FAQ",
            content=faq_text,
            file_size=len(faq_text.encode("utf-8")),
            priority=4,
            version=1,
            status="ACTIVE",
            created_by="admin",
            updated_by="admin"
        )
        db.add(doc_faq)
        await db.flush()

        db.add(DocumentVersion(
            doc_id=doc_faq.id,
            version=1,
            content=faq_text,
            created_by="admin"
        ))

        for item in faq_items_data:
            db.add(FAQItem(
                doc_id=doc_faq.id,
                kb_id=demo_kb.id,
                standard_question=item["standard_question"],
                similar_questions=item["similar_questions"],
                answer=item["answer"],
                priority=item["priority"]
            ))

        faq_chunks = DocumentChunker.chunk_faq(faq_items_data, {"id": doc_faq.id, "name": doc_faq.name, "priority": 4})
        for c in faq_chunks:
            emb = await model_service.get_embedding(c.content)
            db.add(DocumentChunk(
                kb_id=demo_kb.id,
                doc_id=doc_faq.id,
                chunk_index=c.chunk_index,
                content=c.content,
                embedding=emb,
                heading_path=c.heading_path,
                priority=c.metadata.get("priority", 4),
                metadata_info=c.metadata
            ))

        demo_kb.doc_count = 2
        demo_kb.chunk_count = len(md_chunks) + len(faq_chunks)

        # 4. 初始化三位核心客户的真实客服会话与业务历史记录
        from app.models.chat_session import ChatSession, ChatMessage
        import datetime

        s1 = ChatSession(
            customer_id="user_001",
            customer_name="张先生 · 钻石VIP客户",
            product_code="SAAS_CLOUD",
            status="AI_SERVICE",
            last_message="怎么退款",
            last_message_at=datetime.datetime.now() - datetime.timedelta(minutes=5)
        )
        db.add(s1)
        await db.flush()

        db.add(ChatMessage(
            session_id=s1.id,
            role="user",
            content="怎么退款",
            raw_query="怎么退款"
        ))
        db.add(ChatMessage(
            session_id=s1.id,
            role="assistant",
            content="购买本平台 SaaS 订阅服务或云资源实例后，自订单支付之日起 7 个自然日内，若未产生超出基础配额 5% 的计算资源消耗，用户均享有无理由全额退款权益。请登录费用中心在【退订与退款】中申请。",
            reply_type="DIRECT_ANSWER",
            confidence_score=0.892,
            raw_query="怎么退款",
            rewritten_query="SAAS_CLOUD产品关于退款申请流程与售后退款政策条件",
            sources=[
                {
                    "doc_id": doc_md.id,
                    "doc_name": doc_md.name,
                    "heading": "售后服务 > 退款规范",
                    "chunk_id": "chunk_demo_01",
                    "snippet": "购买本平台 SaaS 订阅服务或云资源实例后，自订单支付之日起 7 个自然日内，若未产生超出基础配额 5% 的计算资源消耗，用户均享有无理由全额退款权益。",
                    "priority": 3
                }
            ],
            suggested_questions=[]
        ))

        s2 = ChatSession(
            customer_id="user_002",
            customer_name="李总监 · 算力业务部",
            product_code="SAAS_CLOUD",
            status="AI_SERVICE",
            last_message="查我的退款进度",
            last_message_at=datetime.datetime.now() - datetime.timedelta(minutes=15)
        )
        db.add(s2)
        await db.flush()

        db.add(ChatMessage(
            session_id=s2.id,
            role="user",
            content="查我的退款进度",
            raw_query="查我的退款进度"
        ))
        db.add(ChatMessage(
            session_id=s2.id,
            role="assistant",
            content="已为您调取您名下的最新售后退款单。财务团队正在进行最终复核，详情见下方退款进度卡片：",
            reply_type="TRANSACTION",
            confidence_score=1.0,
            raw_query="查我的退款进度",
            business_data={
                "business_type": "REFUND_STATUS",
                "title": "售后退款审批单详情",
                "summary": "您名下最新订单的退款审核状态如下：",
                "order": {
                    "order_id": "ORD-20261020-3320",
                    "product_name": "高并发GPU算力云主机 (A100*8)",
                    "amount": "¥24,000.00",
                    "status_desc": "退款审核中",
                    "refund_info": {
                        "refund_id": "REF-998231",
                        "refund_amount": "¥24,000.00",
                        "stage": "财务复核",
                        "eta": "预计1个工作日内原路退回银行卡"
                    }
                }
            }
        ))

        s3 = ChatSession(
            customer_id="user_003",
            customer_name="王女士 · 开发者个人账户",
            product_code="SAAS_CLOUD",
            status="AI_SERVICE",
            last_message="发票几天能出来",
            last_message_at=datetime.datetime.now() - datetime.timedelta(minutes=30)
        )
        db.add(s3)
        await db.flush()

        await db.commit()
        logger.info(f"Seed Demo completed: 1 KnowledgeBase, 2 Documents, 3 Sessions initialized.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库与样例数据
    await init_db()
    await seed_demo_data()
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册 API
app.include_router(api_router, prefix=settings.API_V1_STR)

# 静态资源与前端页面挂载
static_dir = os.path.join(os.path.dirname(__file__), "static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/")
async def root():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return {"message": "智能客服知识库系统运行中", "docs": "/docs"}
