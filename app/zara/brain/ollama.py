from __future__ import annotations
import json
import urllib.request
import urllib.error
from ..errors import BrainError

_HOST = "http://localhost:11434"


class OllamaBrain:
    """Cerebro local vía Ollama (HTTP en localhost:11434). Privado y sin coste."""

    def __init__(self, model: str, system: str = "", host: str = _HOST,
                 timeout: int = 300):
        self.name = model
        self.model = model
        self.system = system
        self.host = host
        self.timeout = timeout

    def available(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.host}/api/tags", timeout=3) as r:
                data = json.loads(r.read())
            tags = {m["name"] for m in data.get("models", [])}
            return any(t == self.model or t.split(":")[0] == self.model
                       for t in tags)
        except Exception:
            return False

    def ask(self, prompt: str, context: str = "") -> str:
        from .framing import frame_user
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": frame_user(prompt, context)})
        payload = json.dumps({
            "model": self.model, "messages": messages, "stream": False,
        }).encode()
        req = urllib.request.Request(
            f"{self.host}/api/chat", data=payload,
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
        except urllib.error.URLError as exc:
            raise BrainError(f"Ollama no respondió ({self.model}): {exc}") from exc
        return data.get("message", {}).get("content", "").strip()
