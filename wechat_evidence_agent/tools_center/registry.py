"""Lightweight registry for deterministic lawyer workbench tools."""

from __future__ import annotations

from .image_evidence_docx import generate_image_evidence_docx
from .models import ToolDefinition


def get_tool_definitions() -> list[ToolDefinition]:
    """Return all currently available workbench tools."""

    return [
        ToolDefinition(
            id="image_evidence_docx",
            name="图片证据排版",
            category="证据文档",
            description="导入多张图片，按一页四张排版生成 Word 文件。",
            output_type="docx",
            handler=generate_image_evidence_docx,
        )
    ]
