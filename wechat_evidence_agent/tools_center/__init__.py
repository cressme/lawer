"""Deterministic lawyer workbench tools."""

from .image_evidence_docx import generate_image_evidence_docx
from .registry import get_tool_definitions

__all__ = ["generate_image_evidence_docx", "get_tool_definitions"]
