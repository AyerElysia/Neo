from __future__ import annotations

from dataclasses import dataclass

from ..roles import ROLE
from .content import Content
from .tooling import LLMUsable

def _normalize_content(content: Content | LLMUsable | list[Content | LLMUsable]) -> list[Content | LLMUsable]:
    if isinstance(content, list):
        return content
    return [content]


@dataclass(slots=True)
class LLMPayload:
    role: ROLE
    content: list[Content | LLMUsable]

    def __init__(self, role: ROLE, content: Content | LLMUsable | list[Content | LLMUsable]):
        self.role = role
        self.content = _normalize_content(content)
