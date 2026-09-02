"""Config loading and the disclosure declarations the policy gate depends on.

The disclosure block has no defaults on purpose. Every field in it maps to a
legal or platform-policy obligation (COPPA, AI labelling, paid-promotion
disclosure, music licensing). Guessing on the user's behalf is exactly the
wrong thing to do, so a missing field is a hard error, not a default.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PLATFORMS = ("youtube", "tiktok", "instagram")

MUSIC_SOURCES = ("original", "licensed", "none", "platform_library")


class ConfigError(Exception):
    """Raised for anything malformed or missing in the config file."""


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise ConfigError(f"{where}.{key} is required and has no default")
    return mapping[key]


def _require_bool(mapping: dict[str, Any], key: str, where: str) -> bool:
    value = _require(mapping, key, where)
    if not isinstance(value, bool):
        raise ConfigError(f"{where}.{key} must be true or false, got {value!r}")
    return value


@dataclass
class BrandedContent:
    """TikTok splits branded content into two mutually-exclusive toggles."""

    enabled: bool
    own_brand: bool
    third_party: bool
    partner: str = ""

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "BrandedContent":
        where = "disclosure.branded_content"
        enabled = _require_bool(raw, "enabled", where)
        own = _require_bool(raw, "own_brand", where) if enabled else False
        third = _require_bool(raw, "third_party", where) if enabled else False
        if enabled and not (own or third):
            raise ConfigError(
                f"{where}.enabled is true so one of own_brand / third_party must be true"
            )
        return cls(enabled, own, third, str(raw.get("partner", "") or ""))


@dataclass
class Music:
    source: str
    license_ref: str = ""

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Music":
        where = "disclosure.music"
        source = str(_require(raw, "source", where))
        if source not in MUSIC_SOURCES:
            raise ConfigError(
                f"{where}.source must be one of {', '.join(MUSIC_SOURCES)}, got {source!r}"
            )
        ref = str(raw.get("license_ref", "") or "")
        if source == "licensed" and not ref:
            raise ConfigError(f"{where}.license_ref is required when source is 'licensed'")
        return cls(source, ref)


@dataclass
class Disclosure:
    synthetic_media: bool
    made_for_kids: bool
    privacy_review_confirmed: bool
    branded_content: BrandedContent
    music: Music

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Disclosure":
        if not isinstance(raw, dict):
            raise ConfigError("disclosure block is required")
        where = "disclosure"
        return cls(
            synthetic_media=_require_bool(raw, "synthetic_media", where),
            made_for_kids=_require_bool(raw, "made_for_kids", where),
            privacy_review_confirmed=_require_bool(raw, "privacy_review_confirmed", where),
            branded_content=BrandedContent.parse(_require(raw, "branded_content", where)),
            music=Music.parse(_require(raw, "music", where)),
        )


@dataclass
class Content:
    title: str
    description: str
    caption: str
    hashtags: list[str] = field(default_factory=list)
    language: str = "en"

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> "Content":
        if not isinstance(raw, dict):
            raise ConfigError("content block is required")
        title = str(_require(raw, "title", "content")).strip()
        caption = str(raw.get("caption") or title).strip()
        tags = [str(t).lstrip("#").strip() for t in (raw.get("hashtags") or [])]
        return cls(
            title=title,
            description=str(raw.get("description") or caption).strip(),
            caption=caption,
            hashtags=[t for t in tags if t],
            language=str(raw.get("language") or "en"),
        )

    def caption_with_hashtags(self, limit: int, max_tags: int | None = None) -> str:
        """Caption plus as many hashtags as fit inside `limit` characters."""
        tags = self.hashtags[:max_tags] if max_tags is not None else list(self.hashtags)
        body = self.caption
        for tag in tags:
            candidate = f"{body}\n#{tag}" if "\n#" not in body else f"{body} #{tag}"
            if len(candidate) > limit:
                break
            body = candidate
        return body[:limit]


@dataclass
class PlatformConfig:
    enabled: bool
    options: dict[str, Any]

    def get(self, key: str, default: Any = None) -> Any:
        value = self.options.get(key, default)
        return default if value is None else value


@dataclass
class Config:
    path: Path
    video: Path
    cover: Path | None
    cover_timestamp_ms: int
    content: Content
    disclosure: Disclosure
    platforms: dict[str, PlatformConfig]
    state_file: Path

    def enabled_platforms(self) -> list[str]:
        return [name for name in PLATFORMS if self.platforms[name].enabled]


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: Any) -> Any:
    """Expand ${VAR} references so secrets can live outside the config file."""
    if isinstance(value, str):
        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            if name not in os.environ:
                raise ConfigError(f"config references ${{{name}}} but it is not set")
            return os.environ[name]

        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


def load(path: str | Path) -> Config:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text()) or {}
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a mapping")
    raw = _expand_env(raw)

    video_block = raw.get("video") or {}
    if not isinstance(video_block, dict):
        raise ConfigError("video block must be a mapping")
    video = Path(str(_require(video_block, "path", "video"))).expanduser()
    if not video.is_absolute():
        video = (config_path.parent / video).resolve()

    cover_raw = video_block.get("cover")
    cover: Path | None = None
    if cover_raw:
        cover = Path(str(cover_raw)).expanduser()
        if not cover.is_absolute():
            cover = (config_path.parent / cover).resolve()

    platform_block = raw.get("platforms") or {}
    if not isinstance(platform_block, dict):
        raise ConfigError("platforms block must be a mapping")
    unknown = set(platform_block) - set(PLATFORMS)
    if unknown:
        raise ConfigError(f"unknown platform(s): {', '.join(sorted(unknown))}")

    platforms = {}
    for name in PLATFORMS:
        options = platform_block.get(name) or {}
        if not isinstance(options, dict):
            raise ConfigError(f"platforms.{name} must be a mapping")
        platforms[name] = PlatformConfig(bool(options.get("enabled", False)), options)

    state_raw = raw.get("state_file") or "~/.local/state/socialpost/posted.json"
    state_file = Path(str(state_raw)).expanduser()

    return Config(
        path=config_path,
        video=video,
        cover=cover,
        cover_timestamp_ms=int(video_block.get("cover_timestamp_ms") or 0),
        content=Content.parse(raw.get("content")),
        disclosure=Disclosure.parse(raw.get("disclosure")),
        platforms=platforms,
        state_file=state_file,
    )
