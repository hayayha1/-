import re
import hashlib
from typing import Any, Dict, List

def compute_content_hash(text: str) -> str:
    """计算文档标准SHA-256散列值"""
    normalized = text.strip().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()

class ContentCleaner:
    """文档正文标准化与清洗引擎"""

    @classmethod
    def clean_markdown(cls, raw_text: str) -> str:
        """清洗 Markdown 文本中的杂质与无效噪声"""
        if not raw_text:
            return ""

        text = raw_text

        # 1. 过滤 HTML 注释
        text = re.sub(r"<!--[\s\S]*?-->", "", text)

        # 2. 过滤脚本与内嵌样式
        text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)

        # 3. 过滤 TOC 自动生成目录标记
        text = re.sub(r"\[(TOC|toc)\]", "", text)

        # 4. 规范化 Windows 回车换行符
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 5. 过滤多余的连续空白行（保留最多2个换行）
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    @classmethod
    def convert_feishu_blocks_to_markdown(cls, blocks: List[Dict[str, Any]]) -> str:
        """
        将飞书开放平台 Docx Block JSON 树形结构解析为标准的 Markdown
        飞书 block_type 说明:
        - 1: Page
        - 2: Text (文本)
        - 3: Heading 1
        - 4: Heading 2
        - 5: Heading 3
        - 6: Heading 4
        - 7: Heading 5
        - 8: Heading 6
        - 9: Bullet (无序列表)
        - 10: Ordered (有序列表)
        - 11: Code (代码块)
        - 12: Quote (引用)
        - 14: Callout (高亮块)
        - 27: Table (表格)
        - 31: Image (图片)
        """
        lines = []

        for b in blocks:
            b_type = b.get("block_type")
            
            # 提取 text elements
            def get_text_from_elements(elements):
                if not elements:
                    return ""
                parts = []
                for el in elements:
                    text_run = el.get("text_run", {})
                    content = text_run.get("content", "")
                    style = text_run.get("text_element_style", {})
                    if style.get("bold"):
                        content = f"**{content}**"
                    if style.get("italic"):
                        content = f"*{content}*"
                    if style.get("inline_code"):
                        content = f"`{content}`"
                    parts.append(content)
                return "".join(parts)

            if b_type == 2:  # Text
                text_obj = b.get("text", {})
                t = get_text_from_elements(text_obj.get("elements", []))
                if t:
                    lines.append(t)
            elif b_type == 3:  # Heading 1
                h = get_text_from_elements(b.get("heading1", {}).get("elements", []))
                lines.append(f"# {h}")
            elif b_type == 4:  # Heading 2
                h = get_text_from_elements(b.get("heading2", {}).get("elements", []))
                lines.append(f"## {h}")
            elif b_type == 5:  # Heading 3
                h = get_text_from_elements(b.get("heading3", {}).get("elements", []))
                lines.append(f"### {h}")
            elif b_type in (6, 7, 8):  # Heading 4-6
                level = b_type - 2
                h = get_text_from_elements(b.get(f"heading{level}", {}).get("elements", []))
                lines.append(f"{'#' * level} {h}")
            elif b_type == 9:  # Bullet
                t = get_text_from_elements(b.get("bullet", {}).get("elements", []))
                lines.append(f"- {t}")
            elif b_type == 10:  # Ordered
                t = get_text_from_elements(b.get("ordered", {}).get("elements", []))
                lines.append(f"1. {t}")
            elif b_type == 11:  # Code
                code_obj = b.get("code", {})
                t = get_text_from_elements(code_obj.get("elements", []))
                lang = code_obj.get("style", {}).get("language", "")
                lines.append(f"```{lang}\n{t}\n```")
            elif b_type == 12:  # Quote
                t = get_text_from_elements(b.get("quote", {}).get("elements", []))
                lines.append(f"> {t}")
            elif b_type == 14:  # Callout
                t = get_text_from_elements(b.get("callout", {}).get("elements", []))
                lines.append(f"> [!NOTE]\n> {t}")
            elif b_type == 31:  # Image
                img = b.get("image", {})
                token = img.get("token", "")
                lines.append(f"![image](https://open.feishu.cn/image/{token})")

        return cls.clean_markdown("\n\n".join(lines))
