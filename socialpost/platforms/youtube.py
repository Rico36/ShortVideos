"""YouTube Data API v3 upload (videos.insert, resumable).

Compliance-relevant fields set on every upload:
  status.selfDeclaredMadeForKids   COPPA. Mandatory - omitting it is a legal risk.
  status.containsSyntheticMedia    Altered / synthetic content disclosure.
  status.license                   youtube (standard) or creativeCommon.

A Short is just a normal upload that happens to be vertical and <=3 minutes;
there is no separate Shorts endpoint. #Shorts in the description is belt and
braces - YouTube classifies on the media itself.
"""

from __future__ import annotations

import json

import requests

from ..policy import YOUTUBE, YOUTUBE_TITLE_LIMIT
from .base import TIMEOUT, PostResult, PublishError, Publisher, raise_for

UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
API_URL = "https://www.googleapis.com/youtube/v3/videos"

# The API requires resumable chunks to be a multiple of 256 KB.
CHUNK_SIZE = 8 * 256 * 1024  # 2 MB

# videos.insert costs 1600 units against a default 10,000/day quota.
DAILY_UPLOAD_BUDGET = 6


class YouTubePublisher(Publisher):
    name = "youtube"

    def _body(self) -> dict:
        content = self.config.content
        disclosure = self.config.disclosure
        is_short = bool(self.options.get("short", True))

        description = content.caption_with_hashtags(YOUTUBE.caption_limit)
        if is_short and "#shorts" not in description.lower():
            description = f"{description}\n\n#Shorts".strip()

        return {
            "snippet": {
                "title": content.title[:YOUTUBE_TITLE_LIMIT],
                "description": description[:YOUTUBE.caption_limit],
                "tags": content.hashtags[:15],
                "categoryId": str(self.options.get("category_id", "22")),
                "defaultLanguage": content.language,
                "defaultAudioLanguage": content.language,
            },
            "status": {
                "privacyStatus": str(self.options.get("privacy_status", "private")).lower(),
                "selfDeclaredMadeForKids": disclosure.made_for_kids,
                "containsSyntheticMedia": disclosure.synthetic_media,
                "license": str(self.options.get("license", "youtube")),
                "embeddable": bool(self.options.get("embeddable", True)),
            },
        }

    def preflight(self) -> list[str]:
        token = self.tokens.access_token(self.name)
        response = requests.get(
            "https://www.googleapis.com/youtube/v3/channels",
            headers={"Authorization": f"Bearer {token}"},
            params={"part": "snippet,status", "mine": "true"},
            timeout=TIMEOUT,
        )
        payload = raise_for(response, "YouTube channels.list")
        items = payload.get("items") or []
        if not items:
            raise PublishError(
                "the authorized Google account has no YouTube channel. "
                "Create one at youtube.com before uploading."
            )
        channel = items[0]
        notes = [f"channel: {channel['snippet']['title']} ({channel['id']})"]
        if not channel.get("status", {}).get("isLinked", True):
            notes.append("channel is not linked - uploads may be rejected")
        return notes

    def publish(self) -> PostResult:
        token = self.tokens.access_token(self.name)
        body = self._body()
        size = self.media.size_bytes

        # 1. Open a resumable session.
        session = requests.post(
            UPLOAD_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Length": str(size),
                "X-Upload-Content-Type": "video/*",
            },
            params={"uploadType": "resumable", "part": "snippet,status"},
            data=json.dumps(body),
            timeout=TIMEOUT,
        )
        raise_for(session, "YouTube resumable session")
        location = session.headers.get("Location")
        if not location:
            raise PublishError("YouTube did not return a resumable upload URL")

        # 2. Push the file in 256 KB-aligned chunks, resuming on 308.
        self.log(f"  uploading {self.media.size_mb:.1f} MB in {CHUNK_SIZE // 1024} KB chunks")
        video_id = self._upload_chunks(location, size)

        url = f"https://www.youtube.com/watch?v={video_id}"
        notes = []
        if self.options.get("short", True):
            url = f"https://www.youtube.com/shorts/{video_id}"
        if self.config.cover and self.config.cover.is_file():
            notes.append(self._set_thumbnail(token, video_id))
        return PostResult(self.name, video_id, url, [n for n in notes if n])

    def _upload_chunks(self, location: str, size: int) -> str:
        offset = 0
        with open(self.media.path, "rb") as handle:
            while offset < size:
                handle.seek(offset)
                chunk = handle.read(CHUNK_SIZE)
                end = offset + len(chunk) - 1
                response = requests.put(
                    location,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {offset}-{end}/{size}",
                    },
                    data=chunk,
                    timeout=TIMEOUT * 5,
                )
                if response.status_code in (200, 201):
                    payload = response.json()
                    return str(payload["id"])
                if response.status_code == 308:
                    # Google reports how much it actually stored; trust it over
                    # our own bookkeeping so a partial chunk is re-sent.
                    received = response.headers.get("Range")
                    offset = int(received.split("-")[-1]) + 1 if received else end + 1
                    self.log(f"  {offset * 100 // size}%")
                    continue
                raise PublishError(
                    f"YouTube chunk upload failed ({response.status_code}): {response.text[:500]}"
                )
        raise PublishError("YouTube upload finished without returning a video id")

    def _set_thumbnail(self, token: str, video_id: str) -> str:
        cover = self.config.cover
        if cover.stat().st_size > 2 * 1024 * 1024:
            return "thumbnail skipped: YouTube's limit is 2 MB"
        response = requests.post(
            "https://www.googleapis.com/upload/youtube/v3/thumbnails/set",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
            params={"videoId": video_id},
            data=cover.read_bytes(),
            timeout=TIMEOUT,
        )
        if response.status_code >= 400:
            # Custom thumbnails need a verified channel; not worth failing the post.
            return f"thumbnail rejected ({response.status_code}) - channel may not be verified"
        return "custom thumbnail set"
