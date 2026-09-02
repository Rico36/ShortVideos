"""OAuth token storage and refresh.

Tokens live in one JSON file with 0600 permissions, separate from the config so
the config itself stays safe to commit. Access tokens are refreshed on demand;
YouTube and TikTok both issue refresh tokens, Instagram issues a 60-day
long-lived token that this module refreshes when it is close to expiry.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

import requests

DEFAULT_STORE = Path("~/.config/socialpost/tokens.json").expanduser()
TIMEOUT = 30

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
TIKTOK_TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
GRAPH_VERSION = "v25.0"
GRAPH_URL = f"https://graph.facebook.com/{GRAPH_VERSION}"

# Refresh anything that expires within this window rather than risking a 401
# halfway through a multi-megabyte upload.
REFRESH_MARGIN_S = 300


class AuthError(Exception):
    pass


class TokenStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or DEFAULT_STORE).expanduser()
        self._data: dict[str, Any] = {}
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                self._data = json.loads(self.path.read_text())
            except json.JSONDecodeError as exc:
                raise AuthError(f"token store {self.path} is not valid JSON: {exc}") from exc

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True))
        os.chmod(tmp, 0o600)
        tmp.replace(self.path)

    def get(self, platform: str) -> dict[str, Any]:
        entry = self._data.get(platform)
        if not entry:
            raise AuthError(
                f"no saved credentials for {platform}. "
                f"Run: python3 -m socialpost.authorize {platform}"
            )
        return entry

    def put(self, platform: str, entry: dict[str, Any]) -> None:
        self._data[platform] = entry
        self._save()

    def _expired(self, entry: dict[str, Any]) -> bool:
        expires_at = entry.get("expires_at")
        return expires_at is not None and time.time() > (expires_at - REFRESH_MARGIN_S)

    def access_token(self, platform: str) -> str:
        """Return a valid access token, refreshing it first if needed."""
        entry = self.get(platform)
        if self._expired(entry):
            entry = self._refresh(platform, entry)
        token = entry.get("access_token")
        if not token:
            raise AuthError(f"{platform} credentials have no access_token")
        return str(token)

    def _refresh(self, platform: str, entry: dict[str, Any]) -> dict[str, Any]:
        if platform == "youtube":
            refreshed = _refresh_google(entry)
        elif platform == "tiktok":
            refreshed = _refresh_tiktok(entry)
        elif platform == "instagram":
            refreshed = _refresh_instagram(entry)
        else:
            raise AuthError(f"cannot refresh unknown platform {platform}")
        merged = {**entry, **refreshed}
        self.put(platform, merged)
        return merged


def _expiry(seconds: Any) -> float | None:
    try:
        return time.time() + float(seconds)
    except (TypeError, ValueError):
        return None


def _refresh_google(entry: dict[str, Any]) -> dict[str, Any]:
    refresh_token = entry.get("refresh_token")
    if not refresh_token:
        raise AuthError(
            "YouTube credentials have no refresh_token. Re-authorize with "
            "access_type=offline and prompt=consent."
        )
    response = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "client_id": entry["client_id"],
            "client_secret": entry["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise AuthError(f"Google token refresh failed ({response.status_code}): {response.text}")
    payload = response.json()
    return {"access_token": payload["access_token"], "expires_at": _expiry(payload.get("expires_in"))}


def _refresh_tiktok(entry: dict[str, Any]) -> dict[str, Any]:
    refresh_token = entry.get("refresh_token")
    if not refresh_token:
        raise AuthError("TikTok credentials have no refresh_token; re-authorize.")
    response = requests.post(
        TIKTOK_TOKEN_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": entry["client_key"],
            "client_secret": entry["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=TIMEOUT,
    )
    payload = response.json()
    if response.status_code != 200 or "access_token" not in payload:
        raise AuthError(f"TikTok token refresh failed ({response.status_code}): {response.text}")
    return {
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", refresh_token),
        "expires_at": _expiry(payload.get("expires_in")),
    }


def _refresh_instagram(entry: dict[str, Any]) -> dict[str, Any]:
    """Extend a long-lived Facebook page/user token for another 60 days."""
    response = requests.get(
        f"{GRAPH_URL}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": entry["app_id"],
            "client_secret": entry["app_secret"],
            "fb_exchange_token": entry["access_token"],
        },
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        raise AuthError(
            f"Instagram token refresh failed ({response.status_code}): {response.text}. "
            "Long-lived tokens expire after 60 days of inactivity - re-authorize."
        )
    payload = response.json()
    return {
        "access_token": payload["access_token"],
        "expires_at": _expiry(payload.get("expires_in", 60 * 24 * 3600)),
    }
