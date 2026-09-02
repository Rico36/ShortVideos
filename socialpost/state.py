"""Idempotency ledger.

Cron retries and Home Assistant automations both like to fire twice. Every
successful post is recorded against sha256(video) + platform, so a re-run is a
no-op instead of a duplicate. The same ledger answers "how many have I posted
in the last 24 hours", which is how the Instagram 50/day and YouTube quota
guards are enforced locally.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


class State:
    def __init__(self, path: Path):
        self.path = Path(path).expanduser()
        self.entries: list[dict[str, Any]] = []
        if self.path.is_file():
            try:
                self.entries = json.loads(self.path.read_text()).get("posts", [])
            except (json.JSONDecodeError, AttributeError):
                self.entries = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"posts": self.entries}, indent=2))
        tmp.replace(self.path)

    def already_posted(self, digest: str, platform: str) -> dict[str, Any] | None:
        for entry in self.entries:
            if entry.get("sha256") == digest and entry.get("platform") == platform:
                return entry
        return None

    def record(self, digest: str, platform: str, remote_id: str, url: str = "") -> None:
        self.entries.append({
            "sha256": digest,
            "platform": platform,
            "remote_id": remote_id,
            "url": url,
            "posted_at": time.time(),
        })
        self._save()

    def count_since(self, platform: str, seconds: float) -> int:
        cutoff = time.time() - seconds
        return sum(
            1 for e in self.entries
            if e.get("platform") == platform and float(e.get("posted_at", 0)) >= cutoff
        )
