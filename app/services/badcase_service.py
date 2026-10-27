import uuid
import re
import numpy as np
from collections import Counter
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, func
from datetime import datetime

from app.models.bad_case import (
    BadCase, 
    BadCaseCluster, 
    BadCaseValidation, 
    BadCaseThresholdConfig, 
    ThresholdAuditLog, 
    ROOT_CAUSE_DICT
)
from app.models.document import Document
from app.models.document_version import DocumentVersion
from app.models.faq import FAQItem
from app.models.knowledge_base import KnowledgeBase
from app.models.chat_session import ChatMessage
from app.services.model_service import model_service
from app.services.retrieval.engine import retrieval_engine

class BadCaseService:
    """BadCase 清洗、动态上下文采集、全链路根因研判、语义聚类与效果回流服务"""

    @staticmethod
    def _cosine_similarity(v1: List[float], v2: List[float]) -> float:
        a = np.array(v1)
        b = np.array(v2)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))

    # ================= 1. 上下文动态滑动窗口提取 =================

    async def get_sliding_context(
        self,
        db: AsyncSession,
        session_id: str,
        until_time: Optional[datetime] = None,
        max_user_rounds: int = 3
    ) -> List[Dict[str, str]]:
        """
        优化上下文记忆：逆向滑动扫描，以用户角色输入为锚点计数，
        包含用户三轮输入之间的全部内容（超过三轮截断，少于三轮也全部记忆）。
        """
        stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
        if until_time:
            stmt = stmt.where(ChatMessage.created_at <= until_time)
        stmt = stmt.order_by(ChatMessage.created_at.desc())
        
        res = await db.execute(stmt)
        msgs = res.scalars().all()
        if not msgs:
            return []
        
        collected = []
        user_count = 0
        for m in msgs:
            collected.append({"role": m.role, "content": m.content})
            if m.role == "user":
                user_count += 1
                if user_count >= max_user_rounds:
                    break
        
        collected.reverse()
        return collected

    # ================= 2. 阈值配置获取 =================

    async def get_threshold_config(
        self,
        db: AsyncSession,
        kb_id: Optional[str] = None,
        product_code: Optional[str] = None
    ) -> BadCaseThresholdConfig:
        """
        获取针对指定知识库/产品线的阈值配置，未配置时自动回退至全局默认配置
        """
        if kb_id:
            stmt = select(BadCaseThresholdConfig).where(BadCaseThresholdConfig.kb_id == kb_id)
            res = await db.execute(stmt)
            cfg = res.scalars().first()
            if cfg:
                return cfg
        if product_code:
            stmt = select(BadCaseThresholdConfig).where(BadCaseThresholdConfig.product_code == product_code)
            res = await db.execute(stmt)
            cfg = res.scalars().first()
            if cfg:
                return cfg
        
        # 返回默认配置
        return BadCaseThresholdConfig(
            id="default",
            kb_id=kb_id or "default",
            product_code=product_code or "SAAS_CLOUD",
            threshold_low=0.30,
            threshold_high=0.70,
            short_query_len=5,
            candidate_rank_start=4,
            candidate_rank_end=10,
            context_window_rounds=3
        )

    # ================= 3. 单条 BadCase 根因定位引擎 =================

    async def diagnose_case_root_cause(
        self,
        db: AsyncSession,
        case: BadCase,
        config: Optional[BadCaseThresholdConfig] = None
    ) -> Tuple[str, str, str]:
        """
        严格按照 7 大规则优先级顺序执行短路判定：
        优先级 1: 无效 case
        优先级 2: 匹配混淆
        优先级 3: 知识错误/过时
        优先级 4: 上下文缺失
        优先级 5: 知识缺失
        优先级 6: 召回不足 (强绑定用户负反馈)
        优先级 7: 用户问题模糊
        兜底: 智能识别或待人工核实
        """
        if not config:
            config = await self.get_threshold_config(db, case.kb_id, case.product_code)
        
        q = (case.user_query or "").strip()
        q_len = len(q)
        recall_sources = case.recall_sources or []
        top_score = float(case.confidence_score or 0.0)
        
        # 1. 优先级 1: 无效 case (敏感词/乱码/纯符号/过短)
        is_sensitive = "敏感词" in (case.user_feedback_comment or "") or "敏感词" in (case.agent_label_notes or "")
        is_gibberish = bool(re.match(r"^(.)\1{3,}$", q)) or bool(re.match(r"^[^\w\s\u4e00-\u9fa5]+$", q))
        if is_sensitive or q_len < 2 or is_gibberish:
            return (
                "RC_INVALID_CASE",
                ROOT_CAUSE_DICT["RC_INVALID_CASE"]["name"],
                "提问触发敏感拦截、字数过短(<2)或由乱码纯符号组成，判定为无效提问。"
            )

        # 2. 优先级 2: 匹配混淆 (跨库污染/核心实体脱靶)
        if recall_sources:
            top_c = recall_sources[0]
            top_kb_id = top_c.get("kb_id")
            top_prod = top_c.get("product_code")
            if (top_kb_id and top_kb_id != case.kb_id) or (top_prod and top_prod != case.product_code):
                return (
                    "RC_MATCH_CONFUSION",
                    ROOT_CAUSE_DICT["RC_MATCH_CONFUSION"]["name"],
                    f"Top-1 召回切片来自跨库产品线({top_prod or top_kb_id})，与当前业务会话不符，造成跨库污染匹配混淆。"
                )
            if case.agent_label_category == "IRRELEVANT":
                return (
                    "RC_MATCH_CONFUSION",
                    ROOT_CAUSE_DICT["RC_MATCH_CONFUSION"]["name"],
                    "坐席质检标错为【答非所问/错召回别的知识】，召回切片偏离提问核心实体。"
                )

        # 3. 优先级 3: 知识错误 / 过时 (命中切片置信分合格，但内容错误或政策过期)
        has_user_neg = case.case_type == "USER_FEEDBACK" or bool(case.user_feedback_comment)
        has_agent_err = case.agent_label_category in ["WRONG_ANSWER", "OUTDATED"]
        if top_score >= config.threshold_high and (has_user_neg or has_agent_err):
            return (
                "RC_KNOWLEDGE_ERROR",
                ROOT_CAUSE_DICT["RC_KNOWLEDGE_ERROR"]["name"],
                f"检索 Top-1 匹配分达到高置信门槛({top_score:.2f} >= {config.threshold_high:.2f})，但用户/坐席反馈知识内容错误或规则已过时。"
            )

        # 4. 优先级 4: 上下文缺失 (代词短句脱离上下文)
        pronouns = ["它", "这个", "这", "那个", "那", "刚才", "还有呢", "怎么退", "前面", "之前说的", "第一种", "第二种"]
        has_pronoun = any(p in q for p in pronouns)
        if has_pronoun and q_len <= config.short_query_len and top_score < config.threshold_low:
            return (
                "RC_CONTEXT_MISSING",
                ROOT_CAUSE_DICT["RC_CONTEXT_MISSING"]["name"],
                f"提问包含指代代词且字数短({q_len} <= {config.short_query_len})，脱离前置多轮对话导致单句检索失真。"
            )

        # 5. 优先级 5: 知识缺失 (全库无切片或得分极低，触发兜底)
        fallback_phrases = ["未找到", "没有找到", "无法解答", "抱歉", "暂无相关"]
        ai_fallback = any(p in (case.ai_raw_response or "") for p in fallback_phrases)
        if (len(recall_sources) == 0 or top_score < config.threshold_low) and (ai_fallback or case.agent_label_category == "MISSING_KNOWLEDGE"):
            return (
                "RC_KNOWLEDGE_MISSING",
                ROOT_CAUSE_DICT["RC_KNOWLEDGE_MISSING"]["name"],
                f"全库检索无有效切片或最高分仅为 {top_score:.2f} (< {config.threshold_low:.2f})，知识库缺失该业务规则。"
            )

        # 6. 优先级 6: 召回不足 (修复漏洞：必须强绑定用户负反馈)
        if has_user_neg or has_agent_err or case.case_type == "LOW_CONFIDENCE":
            if top_score >= config.threshold_low and top_score < config.threshold_high:
                return (
                    "RC_RECALL_INSUFFICIENT",
                    ROOT_CAUSE_DICT["RC_RECALL_INSUFFICIENT"]["name"],
                    f"命中知识切片匹配分处于中间相似区间({top_score:.2f})，且用户/坐席产生负反馈，表明存在知识但排序或相似度不足。"
                )
            if len(recall_sources) >= 4:
                return (
                    "RC_RECALL_INSUFFICIENT",
                    ROOT_CAUSE_DICT["RC_RECALL_INSUFFICIENT"]["name"],
                    f"相关知识切片可能散落在候选区间(Rank {config.candidate_rank_start}~{config.candidate_rank_end})未进 Top-3，且用户未采纳产生差评。"
                )

        # 7. 优先级 7: 用户问题模糊
        vague_queries = ["你好", "在吗", "客服", "有人吗", "问一下", "请问", "hello", "hi", "喂"]
        if q in vague_queries or (q_len <= 3 and not any(k in q for k in ["退", "赔", "换", "款", "货", "买", "送", "发", "单"])):
            return (
                "RC_QUERY_AMBIGUOUS",
                ROOT_CAUSE_DICT["RC_QUERY_AMBIGUOUS"]["name"],
                "用户输入语义不清、缺乏明确业务实体与诉求动作，并非知识库内容缺陷。"
            )

        # 8. 未命中确定性规则的兜底
        return (
            "RC_KNOWLEDGE_MISSING" if top_score < 0.40 else "RC_RECALL_INSUFFICIENT",
            ROOT_CAUSE_DICT["RC_KNOWLEDGE_MISSING"]["name"] if top_score < 0.40 else ROOT_CAUSE_DICT["RC_RECALL_INSUFFICIENT"]["name"],
            f"综合研判(最高检索分 {top_score:.2f})，建议优先归类治理。"
        )

    # ================= 4. 簇整体根因裁定算法 =================

    def determine_cluster_root_cause(
        self, 
        cases: List[BadCase],
        target_knowledge_title: Optional[str] = None
    ) -> Tuple[str, str, str, Dict[str, int], str, str]:
        """
        簇整体根因裁定与治理建议强一致生成：
        1. 多数表决优先 (>=60%)
        2. 严重度优先级覆盖：
           知识缺失 > 知识错误/过时 > 召回不足 > 匹配混淆 > 上下文缺失 > 用户问题模糊 > 无效 case
        3. 动作与诊断依据必须与确诊根因严格一一映射！
        """
        codes = [c.root_cause_code for c in cases if c.root_cause_code]
        if not codes:
            codes = ["RC_KNOWLEDGE_MISSING"]
        counts = Counter(codes)
        total = len(codes)
        
        chosen_code = None
        for code, cnt in counts.items():
            if cnt / total >= 0.60:
                chosen_code = code
                break
        
        severity_order = [
            "RC_KNOWLEDGE_MISSING",
            "RC_KNOWLEDGE_ERROR",
            "RC_RECALL_INSUFFICIENT",
            "RC_MATCH_CONFUSION",
            "RC_CONTEXT_MISSING",
            "RC_QUERY_AMBIGUOUS",
            "RC_INVALID_CASE"
        ]
        if not chosen_code:
            for sev in severity_order:
                if sev in counts:
                    chosen_code = sev
                    break
        if not chosen_code:
            chosen_code = "RC_KNOWLEDGE_MISSING"
        
        meta = ROOT_CAUSE_DICT.get(chosen_code, ROOT_CAUSE_DICT["RC_KNOWLEDGE_MISSING"])
        name = meta["name"]
        desc = meta["description"]
        action = meta["action"]
        action_label = meta["action_label"]
        cnt_chosen = counts.get(chosen_code, 0)
        
        if chosen_code == "RC_KNOWLEDGE_MISSING":
            reason = (
                f"【场景 B: 知识缺失】簇内聚合 {total} 笔进线，{name} 占比达 {cnt_chosen}/{total}。"
                f"知识库内完全缺失对应业务规则，检索没有召回有效切片。系统推荐执行【{action_label}】以补充业务盲区并一键生成标准问答草稿。"
            )
        elif chosen_code == "RC_RECALL_INSUFFICIENT":
            doc_str = f"《{target_knowledge_title}》" if target_knowledge_title else "相关已有文档"
            reason = (
                f"【场景 A: 知识已有但召回不足】系统在{doc_str}中检索到对应知识，但用户口语表达存在长尾偏离（{name} 占比 {cnt_chosen}/{total}）。"
                f"系统推荐执行【{action_label}】将本簇多形态口语表达批量追加至目标文档，以拓宽召回语义覆盖。"
            )
        elif chosen_code == "RC_KNOWLEDGE_ERROR":
            doc_str = f"《{target_knowledge_title}》" if target_knowledge_title else "命中文档"
            reason = (
                f"【场景 C: 知识错误/过时】用户进线命中{doc_str}，但反馈答案内容不准确或业务规则已变更（{name} 占比 {cnt_chosen}/{total}）。"
                f"系统推荐执行【{action_label}】发起版本修订草稿提交审核。"
            )
        elif chosen_code == "RC_MATCH_CONFUSION":
            reason = (
                f"【场景 D: 知识调优与负向排除】检索召回了非归属业务或混淆产品知识（{name} 占比 {cnt_chosen}/{total}）。"
                f"系统推荐执行【{action_label}】配置负向相似问或调整路由过滤策略。"
            )
        elif chosen_code == "RC_CONTEXT_MISSING":
            reason = (
                f"【场景 E: 优化多轮指代策略】多轮对话中脱离上下文导致单句检索失真（{name} 占比 {cnt_chosen}/{total}）。"
                f"系统推荐执行【{action_label}】优化指代消解与改写策略。"
            )
        elif chosen_code == "RC_QUERY_AMBIGUOUS":
            reason = (
                f"【场景 F: 配置反问澄清话术】用户输入语义不清、缺乏明确业务诉求动作（{name} 占比 {cnt_chosen}/{total}）。"
                f"系统推荐执行【{action_label}】配置意图澄清引导话术。"
            )
        elif chosen_code == "RC_INVALID_CASE":
            reason = (
                f"【场景 G: 整簇驳回 / 敏感词加黑】问题包含无意义乱码、敏感词或超出服务范围（{name} 占比 {cnt_chosen}/{total}）。"
                f"系统推荐执行【{action_label}】将该簇移出治理池并加入风控过滤。"
            )
        else:
            reason = (
                f"【簇整体根因：{name}】簇内共聚合 {total} 笔进线，{name} 占比达 {cnt_chosen}/{total}。"
                f"官方定义：{desc}。系统建议执行【{action_label}】。"
            )
            
        return chosen_code, name, desc, dict(counts), action, reason

    # ================= 5. 聚类引擎 (增量防重 + 全量重建) =================

    async def run_clustering(
        self, 
        db: AsyncSession, 
        kb_id: Optional[str] = None, 
        product_code: Optional[str] = None,
        similarity_threshold: float = 0.72,
        mode: str = "incremental"  # "incremental" 或 "rebuild"
    ) -> List[BadCaseCluster]:
        """
        核心算法环节：
        - mode='rebuild': 清空现有 PENDING 待办簇，未采纳 Case 重置状态重新全局聚类
        - mode='incremental': 仅聚类新产生的未分配 Case，并优先尝试与现有未处理老簇中心合并，防重复聚类
        """
        if mode == "rebuild":
            # 1. 查找现有 PENDING 簇并删除，将其关联的 Case 状态重置为 PENDING, cluster_id = None
            c_stmt = select(BadCaseCluster).where(BadCaseCluster.status == "PENDING")
            if kb_id:
                c_stmt = c_stmt.where(BadCaseCluster.kb_id == kb_id)
            if product_code:
                c_stmt = c_stmt.where(BadCaseCluster.product_code == product_code)
            res_c = await db.execute(c_stmt)
            old_clusters = res_c.scalars().all()
            old_ids = [c.id for c in old_clusters]
            
            if old_ids:
                await db.execute(
                    update(BadCase)
                    .where(BadCase.cluster_id.in_(old_ids), BadCase.status.in_(["PENDING", "CLUSTERED"]))
                    .values(cluster_id=None, status="PENDING")
                )
                for oc in old_clusters:
                    await db.delete(oc)
                await db.flush()

        # 2. 捞取未成簇的 PENDING BadCases
        stmt = select(BadCase).where(BadCase.status == "PENDING", BadCase.cluster_id.is_(None))
        if kb_id:
            stmt = stmt.where(BadCase.kb_id == kb_id)
        if product_code:
            stmt = stmt.where(BadCase.product_code == product_code)
            
        res = await db.execute(stmt)
        cases = res.scalars().all()
        if not cases:
            # 即使没有新 case，也把现有 pending 簇返回
            p_stmt = select(BadCaseCluster).where(BadCaseCluster.status == "PENDING")
            if kb_id:
                p_stmt = p_stmt.where(BadCaseCluster.kb_id == kb_id)
            p_res = await db.execute(p_stmt)
            return p_res.scalars().all()

        # 3. 为所有待聚类 Case 自动化完成单条根因诊断
        cfg = await self.get_threshold_config(db, kb_id, product_code)
        for c in cases:
            if not c.root_cause_code:
                code, name, r_reason = await self.diagnose_case_root_cause(db, c, cfg)
                c.root_cause_code = code
                c.root_cause_name = name
                c.root_cause_reason = r_reason

        # 4. 过滤极短或无意义的脏数据进入聚类
        valid_cases = [c for c in cases if c.user_query and len(c.user_query.strip()) >= 2]
        if not valid_cases:
            await db.commit()
            return []

        # 5. 提取并计算向量 (使用高性能批量嵌入)
        queries = [c.user_query.strip() for c in valid_cases]
        embeddings = await model_service.get_embeddings_batch(queries)

        # 6. 如果是增量模式，尝试优先并入现有 PENDING 老簇
        unassigned_indices = list(range(len(valid_cases)))
        existing_stmt = select(BadCaseCluster).where(BadCaseCluster.status == "PENDING")
        if kb_id:
            existing_stmt = existing_stmt.where(BadCaseCluster.kb_id == kb_id)
        if product_code:
            existing_stmt = existing_stmt.where(BadCaseCluster.product_code == product_code)
        
        res_exist = await db.execute(existing_stmt)
        existing_clusters = res_exist.scalars().all()
        
        if existing_clusters and mode == "incremental":
            c_names = [ec.cluster_name for ec in existing_clusters]
            c_embs_list = await model_service.get_embeddings_batch(c_names)
            cluster_embs = {ec.id: (ec, c_embs_list[i]) for i, ec in enumerate(existing_clusters)}
            
            merged_indices = set()
            for idx in unassigned_indices:
                emb = embeddings[idx]
                best_sim = -1.0
                best_cluster = None
                for cid, (ec, c_emb) in cluster_embs.items():
                    sim = self._cosine_similarity(emb, c_emb)
                    if sim > best_sim:
                        best_sim = sim
                        best_cluster = ec
                
                # 若与现有老簇相似度 >= 阈值，直接增量并入！
                if best_sim >= similarity_threshold and best_cluster:
                    case_obj = valid_cases[idx]
                    case_obj.cluster_id = best_cluster.id
                    case_obj.status = "CLUSTERED"
                    best_cluster.case_count += 1
                    curr_samples = list(best_cluster.sample_queries or [])
                    if case_obj.user_query.strip() not in curr_samples:
                        curr_samples.append(case_obj.user_query.strip())
                        best_cluster.sample_queries = curr_samples
                    merged_indices.add(idx)

            unassigned_indices = [i for i in unassigned_indices if i not in merged_indices]

        # 7. 针对剩余仍未入簇的 Case 执行两两凝聚聚类
        n = len(unassigned_indices)
        cluster_groups: List[List[int]] = []
        visited = set()

        for i in range(n):
            real_i = unassigned_indices[i]
            if real_i in visited:
                continue
            group = [real_i]
            visited.add(real_i)
            for j in range(i + 1, n):
                real_j = unassigned_indices[j]
                if real_j in visited:
                    continue
                sim = self._cosine_similarity(embeddings[real_i], embeddings[real_j])
                if sim >= similarity_threshold:
                    visited.add(real_j)
                    group.append(real_j)
            
            # 只有当样本量 >= 2 时才生成核心簇；单条孤立 Case 保持未成簇留在全量池
            if len(group) >= 2:
                cluster_groups.append(group)
            else:
                # 孤立 Case 保留在全量池供运营手动合并或单独操作
                valid_cases[real_i].cluster_id = None
                valid_cases[real_i].status = "PENDING"

        # 8. 为新生成的聚类簇提取代表问、判定簇整体根因并推荐操作
        created_clusters = []
        for group in cluster_groups:
            group_cases = [valid_cases[idx] for idx in group]
            group_queries = [queries[idx] for idx in group]
            distinct_queries = list(dict.fromkeys(group_queries))
            
            # 选择最具代表性的问句作为簇标题
            centroid = np.mean([embeddings[idx] for idx in group], axis=0).tolist()
            best_idx = group[0]
            best_sim = -1.0
            for idx in group:
                sim = self._cosine_similarity(embeddings[idx], centroid)
                if sim > best_sim:
                    best_sim = sim
                    best_idx = idx
            rep_query = queries[best_idx]
            
            target_kb_id = group_cases[0].kb_id
            target_prod = group_cases[0].product_code

            # 辅助检索比对以提取推荐关联知识
            chunks, top_score = await retrieval_engine.retrieve_and_rerank(
                db=db,
                kb_id=target_kb_id,
                raw_query=rep_query,
                rewritten_query=rep_query,
                bm25_top_k=10,
                vector_top_k=10,
                rerank_top_k=3
            )
            target_knowledge_id = chunks[0].get("doc_id") if chunks else None
            target_knowledge_title = chunks[0].get("doc_name") if chunks else None

            # 裁定簇整体根因（动作与依据由确诊根因严格派生，保证强一致）
            rc_code, rc_name, rc_desc, rc_dist, rec_action, rec_reason = self.determine_cluster_root_cause(
                group_cases,
                target_knowledge_title=target_knowledge_title
            )

            cluster_obj = BadCaseCluster(
                id=str(uuid.uuid4()),
                product_code=target_prod,
                kb_id=target_kb_id,
                cluster_name=rep_query,
                case_count=len(group_cases),
                sample_queries=distinct_queries,
                root_cause_code=rc_code,
                root_cause_name=rc_name,
                root_cause_description=rc_desc,
                root_cause_distribution=rc_dist,
                recommended_action=rec_action,
                recommended_reason=rec_reason,
                target_knowledge_id=target_knowledge_id,
                target_knowledge_title=target_knowledge_title,
                status="PENDING"
            )
            db.add(cluster_obj)
            await db.flush()

            # 绑定本组 case，状态置为 CLUSTERED
            for c in group_cases:
                c.cluster_id = cluster_obj.id
                c.status = "CLUSTERED"
                c.recommended_action = rec_action
                c.target_knowledge_id = target_knowledge_id

            created_clusters.append(cluster_obj)

        # 9. 确保所有老簇均聚合补齐簇整体根因与分布，并强制动作与依据强一致对齐
        for ec in existing_clusters:
            c_stmt = select(BadCase).where(BadCase.cluster_id == ec.id)
            c_res = await db.execute(c_stmt)
            cluster_cases = c_res.scalars().all()
            if cluster_cases:
                rc_code, rc_name, rc_desc, rc_dist, rec_action, rec_reason = self.determine_cluster_root_cause(
                    cluster_cases,
                    target_knowledge_title=ec.target_knowledge_title
                )
                ec.root_cause_code = rc_code
                ec.root_cause_name = rc_name
                ec.root_cause_description = rc_desc
                ec.root_cause_distribution = rc_dist
                ec.recommended_action = rec_action
                ec.recommended_reason = rec_reason

        await db.commit()

        # 返回全部活跃的 PENDING 簇列表
        final_stmt = select(BadCaseCluster).where(BadCaseCluster.status == "PENDING")
        if kb_id:
            final_stmt = final_stmt.where(BadCaseCluster.kb_id == kb_id)
        if product_code:
            final_stmt = final_stmt.where(BadCaseCluster.product_code == product_code)
        final_stmt = final_stmt.order_by(BadCaseCluster.case_count.desc(), BadCaseCluster.created_at.desc())
        final_res = await db.execute(final_stmt)
        return final_res.scalars().all()

    # ================= 6. 手动合并建簇 =================

    async def manual_create_cluster(
        self,
        db: AsyncSession,
        case_ids: List[str],
        cluster_name: str,
        standard_question: Optional[str] = None,
        operator_username: str = "operator"
    ) -> BadCaseCluster:
        """
        全量 BadCase 池中勾选未成簇 Case 执行手动合并建簇
        """
        stmt = select(BadCase).where(BadCase.id.in_(case_ids))
        res = await db.execute(stmt)
        cases = res.scalars().all()
        if not cases:
            raise ValueError("未选择任何有效的 BadCase")

        target_kb_id = cases[0].kb_id
        target_prod = cases[0].product_code
        distinct_queries = list(dict.fromkeys([c.user_query.strip() for c in cases if c.user_query]))

        # 辅助检索目标知识
        chunks, top_score = await retrieval_engine.retrieve_and_rerank(
            db=db,
            kb_id=target_kb_id,
            raw_query=cluster_name,
            rewritten_query=cluster_name,
            bm25_top_k=10,
            vector_top_k=10,
            rerank_top_k=3
        )
        target_knowledge_id = chunks[0].get("doc_id") if chunks else None
        target_knowledge_title = chunks[0].get("doc_name") if chunks else None

        rc_code, rc_name, rc_desc, rc_dist, rec_action, rec_reason = self.determine_cluster_root_cause(
            cases,
            target_knowledge_title=target_knowledge_title
        )

        cluster_obj = BadCaseCluster(
            id=str(uuid.uuid4()),
            product_code=target_prod,
            kb_id=target_kb_id,
            cluster_name=standard_question or cluster_name,
            case_count=len(cases),
            sample_queries=distinct_queries,
            root_cause_code=rc_code,
            root_cause_name=rc_name,
            root_cause_description=rc_desc,
            root_cause_distribution=rc_dist,
            recommended_action=rec_action,
            recommended_reason=rec_reason,
            target_knowledge_id=target_knowledge_id,
            target_knowledge_title=target_knowledge_title,
            status="PENDING",
            handled_by=operator_username
        )
        db.add(cluster_obj)
        await db.flush()

        for c in cases:
            c.cluster_id = cluster_obj.id
            c.status = "CLUSTERED"
            c.recommended_action = rec_action

        await db.commit()
        return cluster_obj

    # ================= 7. 修订知识文档版本闭环 (场景 C) =================

    async def revise_document(
        self,
        db: AsyncSession,
        doc_id: str,
        new_content: str,
        change_summary: str,
        case_id: Optional[str] = None,
        cluster_id: Optional[str] = None,
        operator_username: str = "operator"
    ) -> DocumentVersion:
        """
        场景 C 修订长文本知识库：创建新版本草稿，置文档为 PENDING_REVIEW 提交审核
        """
        doc = await db.get(Document, doc_id)
        if not doc:
            raise ValueError("目标文档不存在")

        # 升级版本号
        next_version = (doc.version or 1) + 1
        doc.version = next_version
        doc.content = new_content
        doc.status = "PENDING_REVIEW"
        doc.updated_by = operator_username

        ver = DocumentVersion(
            doc_id=doc.id,
            version=next_version,
            content=new_content,
            metadata_snapshot={
                "name": doc.name,
                "change_summary": change_summary,
                "case_id": case_id,
                "cluster_id": cluster_id,
                "status": "PENDING_REVIEW"
            },
            created_by=operator_username
        )
        db.add(ver)

        # 级联更新关联的 BadCase 或 Cluster 状态
        now = datetime.utcnow()
        if case_id:
            await db.execute(
                update(BadCase)
                .where(BadCase.id == case_id)
                .values(
                    status="ADOPTED",
                    resolved_doc_id=doc.id,
                    resolved_at=now,
                    handled_by=operator_username,
                    handling_notes=f"已针对文档《{doc.name}》发起修订草稿 (v{next_version})"
                )
            )

        if cluster_id:
            cluster = await db.get(BadCaseCluster, cluster_id)
            if cluster:
                cluster.status = "ADOPTED"
                cluster.resolved_doc_id = doc.id
                cluster.handled_by = operator_username
                cluster.handled_at = now
            await db.execute(
                update(BadCase)
                .where(BadCase.cluster_id == cluster_id)
                .values(
                    status="ADOPTED",
                    resolved_doc_id=doc.id,
                    resolved_at=now,
                    handled_by=operator_username,
                    handling_notes=f"已针对文档《{doc.name}》发起修订草稿 (v{next_version})"
                )
            )

        await db.commit()
        return ver

    # ================= 8. 回流效果验证 =================

    async def validate_cluster(self, db: AsyncSession, cluster_id: str) -> Dict[str, Any]:
        """
        步骤 6：效果回流验证（闭环验证新知识是否真正解决了历史 BadCase）
        """
        cluster = await db.get(BadCaseCluster, cluster_id)
        if not cluster:
            raise ValueError("聚类簇不存在")

        queries = cluster.sample_queries or [cluster.cluster_name]
        total = len(queries)
        passed = 0
        details = []

        target_doc_id = cluster.resolved_doc_id or cluster.target_knowledge_id

        for q in queries:
            chunks, top_score = await retrieval_engine.retrieve_and_rerank(
                db=db,
                kb_id=cluster.kb_id,
                raw_query=q,
                rewritten_query=q,
                bm25_top_k=10,
                vector_top_k=10,
                rerank_top_k=3
            )
            
            hit_target = any(c.get("doc_id") == target_doc_id for c in chunks) if target_doc_id else False
            is_passed = hit_target or (top_score >= 0.70)
            
            if is_passed:
                passed += 1

            details.append({
                "query": q,
                "passed": is_passed,
                "top_score": round(float(top_score), 4),
                "top_doc": chunks[0].get("doc_name") if chunks else "未召回",
                "top_heading": chunks[0].get("heading_path", "")[:40] if chunks else "",
                "hit_target_doc": hit_target
            })

        pass_rate = round(passed / max(total, 1), 4)

        val_rec = BadCaseValidation(
            id=str(uuid.uuid4()),
            cluster_id=cluster.id,
            kb_id=cluster.kb_id,
            knowledge_id=target_doc_id,
            total_cases=total,
            passed_cases=passed,
            pass_rate=pass_rate,
            details=details
        )
        db.add(val_rec)
        await db.commit()

        return {
            "cluster_id": cluster.id,
            "cluster_name": cluster.cluster_name,
            "total_cases": total,
            "passed_cases": passed,
            "pass_rate": pass_rate,
            "details": details
        }

badcase_service = BadCaseService()
