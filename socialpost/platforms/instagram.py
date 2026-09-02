"""Instagram Reels publishing via the Instagram Graph API.

Three steps: create a container, push the bytes to rupload.facebook.com, then
publish the container once Meta reports FINISHED.

The account must be a Business or Creator account. Publishing needs
instagram_business_content_publish (Instagram Login) or instagram_content_publish
plus pages_read_engagement (Facebook Login).

Note the gap that the policy layer warns about: the publishing API exposes no
field for the AI-content label or the paid-partnership label. Where a post needs
either, it has to be set in the app afterwards.
"""

from __future__ import annotations

import time

import requests

from ..policy import INSTAGRAM
from .base import TIMEOUT, PostResult, PublishError, Publisher, raise_for

GRAPH_VERSION = "v25.0"
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"

# Meta caps published posts at 50 per rolling 24 hours.
DAILY_LIMIT = 50

# Reels transcode asynchronously; publishing before FINISHED returns an error.
POLL_INTERVAL_S = 5
POLL_TRIES = 60


class InstagramPublisher(Publisher):
    name = "instagram"

    @property
    def ig_user_id(self) -> str:
        value = self.options.get("ig_user_id")
        if not value:
            raise PublishError("platforms.instagram.ig_user_id is not set")
        return str(value)

    def preflight(self) -> list[str]:
        token = self.tokens.access_token(self.name)
        notes = []

        account = raise_for(
            requests.get(
                f"{GRAPH}/{self.ig_user_id}",
                params={"fields": "username,account_type", "access_token": token},
                timeout=TIMEOUT,
            ),
            "Instagram account lookup",
        )
        username = account.get("username", "unknown")
        account_type = account.get("account_type", "")
        notes.append(f"account: @{username} ({account_type or 'type unknown'})")
        if account_type and account_type not in ("BUSINESS", "MEDIA_CREATOR", "CREATOR"):
            raise PublishError(
                f"@{username} is a {account_type} account. The Content Publishing API only "
                "works with Business or Creator accounts - convert it in the app."
            )

        usage = raise_for(
            requests.get(
                f"{GRAPH}/{self.ig_user_id}/content_publishing_limit",
                params={"fields": "quota_usage,config", "access_token": token},
                timeout=TIMEOUT,
            ),
            "Instagram publishing limit",
        )
        entries = usage.get("data") or [{}]
        used = int(entries[0].get("quota_usage", 0) or 0)
        quota = int((entries[0].get("config") or {}).get("quota_total", DAILY_LIMIT) or DAILY_LIMIT)
        if used >= quota:
            raise PublishError(
                f"Instagram publishing quota exhausted ({used}/{quota} in the last 24h)"
            )
        notes.append(f"publishing quota: {used}/{quota} used in the last 24h")
        return notes

    def publish(self) -> PostResult:
        token = self.tokens.access_token(self.name)
        caption = self.config.content.caption_with_hashtags(
            INSTAGRAM.caption_limit, INSTAGRAM.max_hashtags
        )

        params = {
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption,
            "share_to_feed": "true" if self.options.get("share_to_feed", True) else "false",
            "access_token": token,
        }
        if self.config.cover_timestamp_ms:
            params["thumb_offset"] = str(self.config.cover_timestamp_ms)
        if self.options.get("location_id"):
            params["location_id"] = str(self.options.get("location_id"))
        if self.options.get("collaborators"):
            params["collaborators"] = ",".join(self.options.get("collaborators"))

        container = raise_for(
            requests.post(f"{GRAPH}/{self.ig_user_id}/media", data=params, timeout=TIMEOUT),
            "Instagram container creation",
        )
        container_id = container.get("id")
        if not container_id:
            raise PublishError(f"Instagram returned no container id: {container}")

        # 2. Push the whole file in one shot; Meta's endpoint takes the full body
        # and reports partial progress through offset if it ever needs a resume.
        self.log(f"  uploading {self.media.size_mb:.1f} MB to rupload")
        upload = requests.post(
            f"{RUPLOAD}/{container_id}",
            headers={
                "Authorization": f"OAuth {token}",
                "offset": "0",
                "file_size": str(self.media.size_bytes),
                "Content-Type": "application/octet-stream",
            },
            data=self.media.path.read_bytes(),
            timeout=TIMEOUT * 10,
        )
        result = raise_for(upload, "Instagram binary upload")
        if not result.get("success", True):
            raise PublishError(f"Instagram upload reported failure: {result}")

        # 3. Wait for the transcode.
        self._await_finished(container_id, token)

        published = raise_for(
            requests.post(
                f"{GRAPH}/{self.ig_user_id}/media_publish",
                data={"creation_id": container_id, "access_token": token},
                timeout=TIMEOUT,
            ),
            "Instagram media_publish",
        )
        media_id = published.get("id")
        if not media_id:
            raise PublishError(f"Instagram publish returned no media id: {published}")

        permalink = ""
        try:
            detail = raise_for(
                requests.get(
                    f"{GRAPH}/{media_id}",
                    params={"fields": "permalink", "access_token": token},
                    timeout=TIMEOUT,
                ),
                "Instagram permalink lookup",
            )
            permalink = detail.get("permalink", "")
        except PublishError:
            pass  # The post is live; a missing permalink is cosmetic.

        notes = []
        if self.config.disclosure.synthetic_media:
            notes.append("set the 'AI info' label on this Reel in the app - the API cannot")
        if self.config.disclosure.branded_content.enabled:
            notes.append("add the paid-partnership label in the app - the API cannot")
        return PostResult(self.name, str(media_id), permalink, notes)

    def _await_finished(self, container_id: str, token: str) -> None:
        for attempt in range(POLL_TRIES):
            status = raise_for(
                requests.get(
                    f"{GRAPH}/{container_id}",
                    params={"fields": "status_code,status", "access_token": token},
                    timeout=TIMEOUT,
                ),
                "Instagram container status",
            )
            code = status.get("status_code")
            if code == "FINISHED":
                return
            if code == "ERROR":
                raise PublishError(
                    f"Instagram failed to process the video: {status.get('status', 'no detail')}"
                )
            if code == "EXPIRED":
                raise PublishError("Instagram container expired before it could be published")
            if attempt % 6 == 0:
                self.log(f"  transcoding ({code})...")
            time.sleep(POLL_INTERVAL_S)
        raise PublishError(
            f"Instagram was still processing after {POLL_TRIES * POLL_INTERVAL_S}s; "
            "the container may still finish - check the app before retrying"
        )
