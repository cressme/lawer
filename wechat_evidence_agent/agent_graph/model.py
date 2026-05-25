"""Small OpenAI-compatible model adapter used by the LangGraph agent."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class ChatModelAdapter:
    """Thin wrapper around the OpenAI SDK.

    We intentionally keep this layer small instead of relying on a LangChain
    chat wrapper because DeepSeek thinking-mode responses include provider
    fields such as ``reasoning_content`` that must be preserved by the caller
    in some modes.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: Optional[str] = None,
        temperature: float = 0.2,
    ) -> None:
        from openai import OpenAI

        kwargs: Dict[str, Any] = {"api_key": api_key or "__missing_api_key__"}
        if base_url:
            kwargs["base_url"] = base_url
        self._client = OpenAI(**kwargs)
        self._model = model
        self._temperature = temperature
        self._history: List[Dict[str, Any]] = []

    def complete(
        self,
        system: str,
        user: str,
        *,
        response_format: Optional[Dict[str, str]] = None,
        max_tokens: int = 2048,
    ) -> str:
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": self._temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        response = self._client.chat.completions.create(**kwargs)
        return response.choices[0].message.content or ""

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int = 2048,
    ) -> Dict[str, Any]:
        content = self.complete(
            system,
            user,
            response_format={"type": "json_object"},
            max_tokens=max_tokens,
        )
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return {"raw": content}
        return data if isinstance(data, dict) else {"value": data}
