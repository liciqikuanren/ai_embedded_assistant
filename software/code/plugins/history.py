"""历史插件：提供 history 服务（发送历史 + 快捷指令，持久化，无依赖）。"""

import json
from pathlib import Path


class HistoryService:
    """维护发送历史（最近 N 条）与快捷指令（命名数据项）。"""

    def __init__(self, max_history: int = 200, path: Path | str | None = None):
        self._max = max_history
        self._path = Path(path) if path else self._default_path()
        self._history: list[str] = []
        self._quick: list[dict] = []
        self.load()

    @staticmethod
    def _default_path() -> Path:
        return Path.home() / ".ai_embedded_assistant" / "history.json"

    def load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                self._history = [str(x) for x in raw.get("history", [])]
                self._quick = [
                    {"name": str(q.get("name", "")),
                     "payload": str(q.get("payload", "")),
                     "hex": bool(q.get("hex", False))}
                    for q in raw.get("quick", [])
                    if isinstance(q, dict)
                ]
        except Exception:
            pass

    def save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps({"history": self._history, "quick": self._quick},
                           ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def add_send(self, text: str) -> None:
        text = text or ""
        if text in self._history:
            self._history.remove(text)
        self._history.insert(0, text)
        del self._history[self._max:]
        self.save()

    def history(self) -> list[str]:
        return list(self._history)

    def clear_history(self) -> None:
        self._history.clear()
        self.save()

    def add_quick(self, name: str, payload: str, as_hex: bool) -> None:
        name = name.strip() or f"指令{len(self._quick) + 1}"
        self._quick.append({"name": name, "payload": payload, "hex": as_hex})
        self.save()

    def remove_quick(self, index: int) -> None:
        if 0 <= index < len(self._quick):
            self._quick.pop(index)
            self.save()

    def quick(self) -> list[dict]:
        return list(self._quick)

    def clear_quick(self) -> None:
        self._quick.clear()
        self.save()


class Plugin:
    name = "history"
    inject = []

    def apply(self, ctx):
        ctx.provide("history", HistoryService())
