"""
证据导出器

支持多种格式的证据导出：
- DOCX: 调用 EvidenceListGenerator 生成 Word 文档
- HTML: 生成独立 HTML 报告（内嵌 CSS，图片 base64 编码）
- PDF: 通过 LibreOffice CLI 将 DOCX 转为 PDF
- ZIP: 将证据清单与所有媒体文件打包
"""

from __future__ import annotations

import base64
import html as html_lib
import mimetypes
import os
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .generator import EvidenceItem, EvidenceListGenerator

_HTML_REPORT_CSS = """\
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
    font-family: "SimSun", "宋体", serif;
    font-size: 14px; color: #222; background: #f5f5f5;
    padding: 40px 20px;
}
.report { max-width: 800px; margin: 0 auto; background: #fff;
    padding: 50px 60px; box-shadow: 0 1px 6px rgba(0,0,0,.1); }
h1 { text-align: center; font-family: "SimHei","黑体",sans-serif;
    font-size: 24px; margin-bottom: 6px; letter-spacing: 6px; }
.subtitle { text-align: center; color: #666; margin-bottom: 24px; font-size: 13px; }
.info-block { margin-bottom: 20px; }
.info-block p { margin: 4px 0; }
.info-block .label { font-weight: bold; display: inline-block; width: 80px; }
table { width: 100%; border-collapse: collapse; margin-top: 16px; font-size: 13px; }
th { background: #D9E2F3; font-family: "SimHei","黑体",sans-serif; }
th, td { border: 1px solid #999; padding: 6px 8px; text-align: left; vertical-align: top; }
td:first-child, th:first-child { text-align: center; width: 40px; }
.footer { margin-top: 30px; text-align: right; font-size: 13px; }
.evidence-detail { margin-top: 40px; page-break-before: always; }
.evidence-detail h2 { font-size: 16px; border-bottom: 2px solid #333; padding-bottom: 4px;
    margin-bottom: 12px; }
.evidence-detail .content { margin: 10px 0; line-height: 1.8; }
.evidence-detail img { max-width: 100%; margin: 8px 0; border: 1px solid #ddd; }
"""

_HTML_REPORT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>证据清单 - {case_type}</title>
<style>{css}</style>
</head>
<body>
<div class="report">
<h1>证 据 清 单</h1>
<div class="subtitle">{court_name} {case_number}</div>
<div class="info-block">
<p><span class="label">案　　由：</span>{case_type}</p>
<p><span class="label">原　　告：</span>{plaintiff}</p>
<p><span class="label">被　　告：</span>{defendant}</p>
</div>
{table_html}
{details_html}
<div class="footer">
<p>提交人：{plaintiff}</p>
<p>日　期：{date}</p>
</div>
</div>
</body>
</html>
"""


class EvidenceExporter:
    """多格式证据导出器。"""

    def __init__(self, template_config: Optional[dict] = None) -> None:
        """初始化导出器。

        Args:
            template_config: 传递给 EvidenceListGenerator 的模板配置。
        """
        self._generator = EvidenceListGenerator(template_config)

    # ------------------------------------------------------------------ #
    #  DOCX 导出
    # ------------------------------------------------------------------ #

    def export_docx(
        self,
        evidence_data: Dict[str, Any],
        output_path: str,
    ) -> Path:
        """生成证据清单 Word 文档。

        Args:
            evidence_data: 包含 case_info 和 evidence_items 的字典。
            output_path: 输出文件路径。

        Returns:
            生成的文件路径。
        """
        case_info: dict = evidence_data.get("case_info", {})
        items: List[EvidenceItem] = evidence_data.get("evidence_items", [])
        return self._generator.generate(case_info, items, output_path)

    # ------------------------------------------------------------------ #
    #  HTML 导出
    # ------------------------------------------------------------------ #

    def export_html(
        self,
        evidence_data: Dict[str, Any],
        output_path: str,
    ) -> Path:
        """生成独立 HTML 证据报告，图片以 base64 内嵌。

        Args:
            evidence_data: 包含 case_info 和 evidence_items 的字典。
            output_path: 输出路径（.html）。

        Returns:
            生成的文件路径。
        """
        from .generator import auto_number_evidence

        case_info: dict = evidence_data.get("case_info", {})
        raw_items: List[EvidenceItem] = evidence_data.get("evidence_items", [])
        items = auto_number_evidence(raw_items)

        # 构建表格 HTML
        table_rows: List[str] = []
        for item in items:
            table_rows.append(
                "<tr>"
                f"<td>{item.id}</td>"
                f"<td>{html_lib.escape(item.title)}</td>"
                f"<td>{html_lib.escape(item.source)}</td>"
                f"<td>{html_lib.escape(item.description)}</td>"
                f"<td>{html_lib.escape(item.proof_purpose)}</td>"
                f"<td>{html_lib.escape(item.page_range)}</td>"
                f"<td>{html_lib.escape(item.remarks)}</td>"
                "</tr>"
            )

        table_html = (
            "<table>"
            "<tr><th>序号</th><th>证据名称</th><th>证据来源</th>"
            "<th>证据内容摘要</th><th>证明目的</th><th>页码</th><th>备注</th></tr>"
            + "\n".join(table_rows)
            + "</table>"
        )

        # 构建证据详情
        details_parts: List[str] = []
        for item in items:
            content_html = self._render_evidence_content_html(item)
            details_parts.append(
                f'<div class="evidence-detail">'
                f"<h2>证据{item.id}：{html_lib.escape(item.title)}</h2>"
                f'<div class="content">{content_html}</div>'
                f"</div>"
            )

        html_str = _HTML_REPORT_TEMPLATE.format(
            css=_HTML_REPORT_CSS,
            case_type=html_lib.escape(case_info.get("case_type", "")),
            court_name=html_lib.escape(case_info.get("court_name", "")),
            case_number=html_lib.escape(case_info.get("case_number", "")),
            plaintiff=html_lib.escape(case_info.get("plaintiff", "")),
            defendant=html_lib.escape(case_info.get("defendant", "")),
            table_html=table_html,
            details_html="\n".join(details_parts),
            date=datetime.now().strftime("%Y年%m月%d日"),
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(html_str, encoding="utf-8")
        return out

    @staticmethod
    def _render_evidence_content_html(item: EvidenceItem) -> str:
        """将单条证据的 content 渲染为 HTML 片段。"""
        content = item.content
        if not content:
            return f"<p>{html_lib.escape(item.description)}</p>"

        path = Path(content)
        # 图片类型 → base64 嵌入
        if item.category == "图片" and path.is_file():
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            data = base64.b64encode(path.read_bytes()).decode()
            return f'<img src="data:{mime};base64,{data}" alt="{html_lib.escape(item.title)}">'

        # 其他文件类型仅标注路径
        if path.is_file():
            return f"<p>[附件] {html_lib.escape(path.name)}</p>"

        # 纯文本
        return f"<p>{html_lib.escape(content)}</p>"

    # ------------------------------------------------------------------ #
    #  PDF 导出
    # ------------------------------------------------------------------ #

    def export_pdf(
        self,
        docx_path: str,
        output_path: str,
    ) -> Path:
        """将 DOCX 转换为 PDF。

        优先使用 LibreOffice CLI (soffice)；若不可用则抛出异常并
        提示安装方式。

        Args:
            docx_path: 源 DOCX 文件路径。
            output_path: 目标 PDF 文件路径。

        Returns:
            生成的 PDF 文件路径。

        Raises:
            RuntimeError: LibreOffice 不可用或转换失败时抛出。
        """
        docx_file = Path(docx_path)
        if not docx_file.is_file():
            raise FileNotFoundError(f"DOCX 文件不存在：{docx_path}")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        # 查找 LibreOffice
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        if not soffice:
            raise RuntimeError(
                "未找到 LibreOffice。请安装后重试：\n"
                "  Ubuntu/Debian: sudo apt install libreoffice\n"
                "  macOS: brew install --cask libreoffice\n"
                "  Windows: 从 https://www.libreoffice.org 下载安装"
            )

        result = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", str(out.parent),
                str(docx_file),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"LibreOffice 转换失败（返回码 {result.returncode}）：\n"
                f"{result.stderr}"
            )

        # LibreOffice 输出文件名为原文件名 .pdf
        generated = out.parent / f"{docx_file.stem}.pdf"
        if generated != out and generated.is_file():
            generated.rename(out)

        if not out.is_file():
            raise RuntimeError("PDF 转换完成但未找到输出文件")

        return out

    # ------------------------------------------------------------------ #
    #  ZIP 打包
    # ------------------------------------------------------------------ #

    def package_evidence(
        self,
        evidence_dir: str,
        output_path: str,
    ) -> Path:
        """将证据目录打包为 ZIP 文件。

        目录结构示例::

            evidence_package.zip
            ├── 证据清单.docx
            ├── 聊天记录/
            ├── 转账记录/
            ├── 图片/
            ├── 语音/
            ├── 视频/
            └── 文件/

        Args:
            evidence_dir: 证据文件所在目录。
            output_path: 输出 ZIP 路径。

        Returns:
            生成的 ZIP 文件路径。
        """
        src = Path(evidence_dir)
        if not src.is_dir():
            raise NotADirectoryError(f"证据目录不存在：{evidence_dir}")

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(str(out), "w", zipfile.ZIP_DEFLATED) as zf:
            for file in sorted(src.rglob("*")):
                if file.is_file():
                    arcname = file.relative_to(src)
                    zf.write(file, arcname)

        return out
