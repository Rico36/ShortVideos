"""Shared publisher interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..media import MediaInfo
from ..tokens import TokenStore

TIMEOUT = 60


class PublishError(Exception):
    pass


@dataclass
class PostResult:
    platform: str
    remote_id: str
    url: str = ""
    notes: list[str] = field(default_factory=list)


class Publisher:
    name = ""

    def __init__(self, config: Config, media: MediaInfo, tokens: TokenStore, log):
        self.config = config
        self.media = media
        self.tokens = tokens
        self.log = log
        self.options = config.platforms[self.name]

    def preflight(self) -> list[str]:
        """Live account-side checks that need a network call. Returns notes."""
        return []

    def publish(self) -> PostResult:
        raise NotImplementedError


def raise_for(response: Any, what: str) -> dict:
    if response.status_code >= 400:
        raise PublishError(f"{what} failed ({response.status_code}): {response.text[:800]}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {}
