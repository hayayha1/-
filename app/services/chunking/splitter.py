import re
from typing import List, Dict, Any, Optional

class ChunkData:
    def __init__(
        self,
        content: str,
        chunk_index: int,
        heading_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.content = content
        self.chunk_index = chunk_index
        self.heading_path = heading_path or ""
        self.metadata = metadata or {}

class DocumentChunker:
    """多策略智能切片引擎"""

    @staticmethod
    def chunk_faq(faq_items: List[Dict[str, Any]], doc_info: Dict[str, Any]) -> List[ChunkData]:
        """
        FAQ 零切片策略：1条 FAQ = 1个原子 Chunk
        将标准问、相似问与答案整合增强检索特征
        """
        chunks = []
        for idx, item in enumerate(faq_items):
            std_q = item.get("standard_question", "").strip()
            sim_q = item.get("similar_questions", [])
            sim_text = "、".join(sim_q) if isinstance(sim_q, list) else str(sim_q)
            ans = item.get("answer", "").strip()
            
            # 特征增强拼接
            content = f"【标准问题】: {std_q}\n【相似表达】: {sim_text}\n【权威解答】: {ans}"
            
            meta = {
                "doc_id": doc_info.get("id"),
                "doc_name": doc_info.get("name"),
                "doc_type": "FAQ",
                "priority": item.get("priority", doc_info.get("priority", 1)),
                "standard_question": std_q,
                "similar_questions": sim_q,
                "char_count": len(content)
            }
            chunks.append(ChunkData(
                content=content,
                chunk_index=idx,
                heading_path="FAQ问答库",
                metadata=meta
            ))
        return chunks

    @staticmethod
    def chunk_markdown(content: str, doc_info: Dict[str, Any], max_chunk_size: int = 500) -> List[ChunkData]:
        """
        Markdown 语义分块策略：
        基于 Header 层级结构提取面包屑路径，保留结构语义
        """
        lines = content.split("\n")
        chunks = []
        current_headings = {1: "", 2: "", 3: "", 4: ""}
        current_buffer = []
        chunk_idx = 0

        def get_current_path() -> str:
            path_parts = [current_headings[lvl] for lvl in sorted(current_headings.keys()) if current_headings[lvl]]
            return " > ".join(path_parts) if path_parts else "正文"

        def flush_buffer(buf: List[str], path: str):
            nonlocal chunk_idx
            text = "\n".join(buf).strip()
            if not text:
                return
            
            # 若段落仍然过长，执行次级分块
            if len(text) > max_chunk_size:
                sub_chunks = DocumentChunker._split_text_recursively(text, max_chunk_size, 50)
                for sc in sub_chunks:
                    meta = {
                        "doc_id": doc_info.get("id"),
                        "doc_name": doc_info.get("name"),
                        "doc_type": "MD",
                        "priority": doc_info.get("priority", 1),
                        "heading_path": path,
                        "char_count": len(sc)
                    }
                    chunks.append(ChunkData(
                        content=sc,
                        chunk_index=chunk_idx,
                        heading_path=path,
                        metadata=meta
                    ))
                    chunk_idx += 1
            else:
                meta = {
                    "doc_id": doc_info.get("id"),
                    "doc_name": doc_info.get("name"),
                    "doc_type": "MD",
                    "priority": doc_info.get("priority", 1),
                    "heading_path": path,
                    "char_count": len(text)
                }
                chunks.append(ChunkData(
                    content=text,
                    chunk_index=chunk_idx,
                    heading_path=path,
                    metadata=meta
                ))
                chunk_idx += 1

        for line in lines:
            header_match = re.match(r"^(#{1,4})\s+(.+)$", line)
            if header_match:
                # 遇到新标题，先将当前缓冲区沉淀为一个 chunk
                if current_buffer:
                    flush_buffer(current_buffer, get_current_path())
                    current_buffer = []
                
                level = len(header_match.group(1))
                title = header_match.group(2).strip()
                current_headings[level] = title
                # 清除深于该级别的子标题
                for lvl in range(level + 1, 5):
                    current_headings[lvl] = ""
                current_buffer.append(line)
            else:
                current_buffer.append(line)
                # 字符过多且遇到段落结束时主动切分
                if sum(len(l) for l in current_buffer) >= max_chunk_size and (line == "" or line.endswith("。")):
                    flush_buffer(current_buffer, get_current_path())
                    current_buffer = []

        if current_buffer:
            flush_buffer(current_buffer, get_current_path())

        return chunks

    @staticmethod
    def chunk_txt(content: str, doc_info: Dict[str, Any], chunk_size: int = 500, chunk_overlap: int = 50) -> List[ChunkData]:
        """
        TXT 递归字符切片器：
        按换行、段落、标点逐步拆解，并保证 overlap 上下文连续
        """
        text_slices = DocumentChunker._split_text_recursively(content, chunk_size, chunk_overlap)
        chunks = []
        for idx, text in enumerate(text_slices):
            meta = {
                "doc_id": doc_info.get("id"),
                "doc_name": doc_info.get("name"),
                "doc_type": "TXT",
                "priority": doc_info.get("priority", 1),
                "heading_path": "正文",
                "char_count": len(text)
            }
            chunks.append(ChunkData(
                content=text,
                chunk_index=idx,
                heading_path="正文",
                metadata=meta
            ))
        return chunks

    @staticmethod
    def _split_text_recursively(text: str, chunk_size: int, overlap: int) -> List[str]:
        if len(text) <= chunk_size:
            return [text.strip()] if text.strip() else []

        separators = ["\n\n", "\n", "。", "！", "？", "；", " ", ""]
        pieces = []
        
        # 寻找最高优先级分隔符
        sep = separators[-1]
        for s in separators:
            if s and s in text:
                sep = s
                break
        
        raw_splits = text.split(sep) if sep else list(text)
        current_chunk = ""
        
        for part in raw_splits:
            part_str = part + (sep if sep else "")
            if len(current_chunk) + len(part_str) <= chunk_size:
                current_chunk += part_str
            else:
                if current_chunk.strip():
                    pieces.append(current_chunk.strip())
                # 保留 overlap 长度的尾部
                if overlap > 0 and len(current_chunk) > overlap:
                    current_chunk = current_chunk[-overlap:] + part_str
                else:
                    current_chunk = part_str

        if current_chunk.strip():
            pieces.append(current_chunk.strip())

        return pieces
