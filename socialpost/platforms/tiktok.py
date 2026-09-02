"""TikTok Content Posting API v2.

Two modes:
Before either, /v2/user/info/ confirms which account was authorized - posting
is irreversible, so the operator sees the target account first. That is the
only use of the user.info.basic scope.

  direct  /v2/post/publish/video/init/        scope video.publish - posts live
  draft   /v2/post/publish/inbox/video/init/  scope video.upload  - lands in the
          creator's TikTok inbox for them to finish and post by hand

The creator_info query is not optional decoration. TikTok's Content Sharing
Guidelines require that an integration reads the creator's current settings and
honours them - if they have disabled comments, duet or stitch, the post must
respect that, and the privacy level must be one the API says is available. An
unaudited client only ever gets SELF_ONLY back, which is how this module
detects that an app has not passed audit yet.
"""

from __future__ import annotations

import time

import requests

from ..policy import TIKTOK, TIKTOK_TITLE_LIMIT
from .base import TIMEOUT, PostResult, PublishError, Publisher, raise_for

USER_INFO = "https://open.tiktokapis.com/v2/user/info/"
BASE = "https://open.tiktokapis.com/v2/post/publish"
CREATOR_INFO = f"{BASE}/creator_info/query/"
DIRECT_INIT = f"{BASE}/video/init/"
DRAFT_INIT = f"{BASE}/inbox/video/init/"
STATUS = f"{BASE}/status/fetch/"

MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024
MAX_CHUNKS = 1000

# Each access token is limited to 6 requests per minute.
REQUEST_SPACING_S = 10


class TikTokPublisher(Publisher):
    name = "tiktok"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.creator_info: dict = {}
        self.user_info: dict = {}
        self._last_call = 0.0

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.tokens.access_token(self.name)}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if self._last_call and elapsed < REQUEST_SPACING_S:
            time.sleep(REQUEST_SPACING_S - elapsed)
        self._last_call = time.time()

    def _get(self, url: str, params: dict, what: str) -> dict:
        self._throttle()
        response = requests.get(url, headers=self._headers(), params=params, timeout=TIMEOUT)
        return self._unwrap(raise_for(response, what), what)

    def _post(self, url: str, payload: dict, what: str) -> dict:
        self._throttle()
        response = requests.post(url, headers=self._headers(), json=payload, timeout=TIMEOUT)
        return self._unwrap(raise_for(response, what), what)

    @staticmethod
    def _unwrap(body: dict, what: str) -> dict:
        """TikTok reports failures inside a 200 response, so check error.code."""
        error = (body.get("error") or {})
        if error.get("code") not in (None, "ok"):
            raise PublishError(
                f"{what} failed: {error.get('code')} - {error.get('message')} "
                f"(log_id {error.get('log_id')})"
            )
        return body.get("data") or {}

    def preflight(self) -> list[str]:
        """Confirm the account, then read its settings and check the request."""
        # Posting is irreversible, so confirm which account was authorized
        # before anything is uploaded. This is what user.info.basic is for.
        user = self._get(
            USER_INFO, {"fields": "open_id,display_name,avatar_url"}, "TikTok user info"
        ).get("user") or {}
        self.user_info = user
        notes = [f"authorized account: {user.get('display_name', 'unknown')}"]

        self.creator_info = self._post(CREATOR_INFO, {}, "TikTok creator_info")
        info = self.creator_info
        notes.append(f"creator: {info.get('creator_nickname')} (@{info.get('creator_username')})")

        options = info.get("privacy_level_options") or []
        wanted = str(self.options.get("privacy_level", "SELF_ONLY")).upper()
        if options and wanted not in options:
            if options == ["SELF_ONLY"]:
                raise PublishError(
                    "TikTok only offers SELF_ONLY for this client, which means the app has not "
                    "passed TikTok's audit yet. Unaudited clients can only post privately. "
                    "Either set platforms.tiktok.privacy_level: SELF_ONLY, or submit the app "
                    "for audit in the TikTok developer portal."
                )
            raise PublishError(
                f"privacy_level {wanted} is not available for this creator "
                f"(available: {', '.join(options)})"
            )

        # Honour the creator's interaction settings rather than our config.
        for field, label in (
            ("comment_disabled", "comments"),
            ("duet_disabled", "duet"),
            ("stitch_disabled", "stitch"),
        ):
            if info.get(field):
                notes.append(f"{label} disabled in the creator's account settings - honoured")

        max_duration = info.get("max_video_post_duration_sec")
        duration = self.media.duration_s
        if max_duration and duration and duration > float(max_duration):
            raise PublishError(
                f"video is {duration:.1f}s but this creator's TikTok limit is {max_duration}s"
            )

        remaining = info.get("creator_can_post")
        if remaining is False:
            raise PublishError(
                "TikTok reports this creator cannot post right now "
                "(daily post limit reached, or the account is restricted)"
            )
        return notes

    def _post_info(self) -> dict:
        content = self.config.content
        disclosure = self.config.disclosure
        info = self.creator_info
        branded = disclosure.branded_content

        post_info = {
            "title": content.caption_with_hashtags(TIKTOK.caption_limit)[:TIKTOK_TITLE_LIMIT],
            "privacy_level": str(self.options.get("privacy_level", "SELF_ONLY")).upper(),
            # Creator settings win over our config - a disabled interaction stays disabled.
            "disable_comment": bool(info.get("comment_disabled"))
                               or not bool(self.options.get("allow_comment", True)),
            "disable_duet": bool(info.get("duet_disabled"))
                            or not bool(self.options.get("allow_duet", True)),
            "disable_stitch": bool(info.get("stitch_disabled"))
                              or not bool(self.options.get("allow_stitch", True)),
            "video_cover_timestamp_ms": int(self.config.cover_timestamp_ms),
            # AI-generated content label.
            "is_aigc": disclosure.synthetic_media,
        }
        if branded.enabled:
            post_info["brand_organic_toggle"] = branded.own_brand
            post_info["brand_content_toggle"] = branded.third_party
        return post_info

    def _chunking(self) -> tuple[int, int]:
        """Pick a chunk size inside TikTok's 5-64 MB window."""
        size = self.media.size_bytes
        if size <= MIN_CHUNK:
            return size, 1
        chunk = MIN_CHUNK
        while size // chunk > MAX_CHUNKS and chunk < MAX_CHUNK:
            chunk = min(chunk * 2, MAX_CHUNK)
        return chunk, max(1, size // chunk)

    def publish(self) -> PostResult:
        if not self.creator_info:
            self.preflight()

        mode = str(self.options.get("mode", "direct")).lower()
        if mode not in ("direct", "draft"):
            raise PublishError(f"platforms.tiktok.mode must be 'direct' or 'draft', got {mode!r}")

        chunk_size, chunk_count = self._chunking()
        size = self.media.size_bytes
        payload = {
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": size,
                "chunk_size": chunk_size,
                "total_chunk_count": chunk_count,
            }
        }
        if mode == "direct":
            payload["post_info"] = self._post_info()
            data = self._post(DIRECT_INIT, payload, "TikTok video/init")
        else:
            data = self._post(DRAFT_INIT, payload, "TikTok inbox/video/init")

        publish_id = data.get("publish_id")
        upload_url = data.get("upload_url")
        if not publish_id or not upload_url:
            raise PublishError(f"TikTok init returned no upload target: {data}")

        self.log(f"  uploading {self.media.size_mb:.1f} MB in {chunk_count} chunk(s)")
        self._upload(upload_url, chunk_size, chunk_count, size)

        notes = []
        if mode == "draft":
            notes.append("uploaded to the TikTok inbox - open the app to finish and post it")
        status = self._await_status(publish_id, expect_publish=(mode == "direct"))
        notes.append(f"final status: {status}")
        return PostResult(self.name, str(publish_id), "", notes)

    def _upload(self, url: str, chunk_size: int, chunk_count: int, size: int) -> None:
        with open(self.media.path, "rb") as handle:
            for index in range(chunk_count):
                start = index * chunk_size
                # The last chunk carries the remainder, so it can exceed chunk_size.
                end = size - 1 if index == chunk_count - 1 else start + chunk_size - 1
                handle.seek(start)
                body = handle.read(end - start + 1)
                response = requests.put(
                    url,
                    headers={
                        "Content-Range": f"bytes {start}-{end}/{size}",
                        "Content-Length": str(len(body)),
                        "Content-Type": f"video/{self.media.container or 'mp4'}",
                    },
                    data=body,
                    timeout=TIMEOUT * 5,
                )
                if response.status_code not in (200, 201, 206):
                    raise PublishError(
                        f"TikTok chunk {index + 1}/{chunk_count} failed "
                        f"({response.status_code}): {response.text[:400]}"
                    )
                self.log(f"  chunk {index + 1}/{chunk_count} sent")

    def _await_status(self, publish_id: str, expect_publish: bool, tries: int = 20) -> str:
        """Poll until TikTok finishes processing, so failures surface here."""
        terminal_ok = "PUBLISH_COMPLETE" if expect_publish else "SEND_TO_USER_INBOX"
        status = "UNKNOWN"
        for _ in range(tries):
            data = self._post(STATUS, {"publish_id": publish_id}, "TikTok status/fetch")
            status = str(data.get("status", "UNKNOWN"))
            if status in (terminal_ok, "PUBLISH_COMPLETE", "SEND_TO_USER_INBOX"):
                return status
            if status == "FAILED":
                reason = data.get("fail_reason") or data.get("error_code") or "no reason given"
                raise PublishError(f"TikTok rejected the post: {reason}")
        return f"{status} (still processing after {tries} polls - check the app)"
