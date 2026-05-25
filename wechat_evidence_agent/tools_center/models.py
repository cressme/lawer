"""Shared models for the lawyer workbench tools center."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolDefinition:
    """Metadata used by the UI and future agent orchestration."""

    id: str
    name: str
    category: str
    description: str
    output_type: str
    handler: Callable[..., dict[str, Any]]

    def to_public_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "output_type": self.output_type,
        }
