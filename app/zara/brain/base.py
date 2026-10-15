from __future__ import annotations
from typing import Protocol


class Brain(Protocol):
    name: str

    def available(self) -> bool: ...

    def ask(self, prompt: str, context: str = "") -> str: ...
