"""Per-platform technical specs and policy checks, run before anything uploads.

Two severities:

  ERROR    the upload would be rejected, shadow-restricted, or would breach a
           platform policy or a legal obligation. Blocks the post. --force does
           not override these.
  WARNING  allowed, but likely to hurt reach or needs a manual step the API
           cannot perform (Instagram has no AI-label field, for example).

Specs are current as of September 2026; every limit is cited in social/README.md
so they can be re-checked when the platforms move them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from .config import Config
from .media import MediaInfo

ERROR = "ERROR"
WARNING = "WARNING"


@dataclass
class Finding:
    severity: str
    platform: str
    code: str
    message: str
    remedy: str = ""

    def format(self) -> str:
        head = f"[{self.severity:<7}] {self.platform}: {self.message}"
        return f"{head}\n            -> {self.remedy}" if self.remedy else head


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: str, platform: str, code: str, message: str, remedy: str = "") -> None:
        self.findings.append(Finding(severity, platform, code, message, remedy))

    def errors(self, platform: str | None = None) -> list[Finding]:
        return [
            f for f in self.findings
            if f.severity == ERROR and (platform is None or f.platform in (platform, "all"))
        ]

    def warnings(self, platform: str | None = None) -> list[Finding]:
        return [
            f for f in self.findings
            if f.severity == WARNING and (platform is None or f.platform in (platform, "all"))
        ]

    def blocked(self, platform: str) -> bool:
        return bool(self.errors(platform))


@dataclass(frozen=True)
class Spec:
    """Technical envelope a platform accepts for a short vertical video."""

    min_duration_s: float
    max_duration_s: float
    max_size_bytes: int
    containers: tuple[str, ...]
    video_codecs: tuple[str, ...]
    audio_codecs: tuple[str, ...]
    min_fps: float
    max_fps: float
    min_width: int
    caption_limit: int
    max_hashtags: int | None = None


# YouTube Shorts: <=3 min, square or vertical, 1080x1920 recommended.
YOUTUBE = Spec(
    min_duration_s=1.0, max_duration_s=180.0,
    max_size_bytes=256 * 1024**3,
    containers=("mp4", "mov", "webm", "mkv", "avi"),
    video_codecs=("h264", "hevc", "vp9", "av1"),
    audio_codecs=("aac", "mp3", "opus", "flac"),
    min_fps=20.0, max_fps=60.0, min_width=600,
    caption_limit=5000,
)

# TikTok: 3s floor; the ceiling is per-creator and replaced at runtime by
# max_video_post_duration_sec from the creator_info endpoint.
TIKTOK = Spec(
    min_duration_s=3.0, max_duration_s=600.0,
    max_size_bytes=4 * 1024**3,
    containers=("mp4", "mov", "webm"),
    video_codecs=("h264", "hevc"),
    audio_codecs=("aac", "mp3"),
    min_fps=23.0, max_fps=60.0, min_width=360,
    caption_limit=2200,
)

# Instagram Reels: 3s-15min, <=1GB, H.264/HEVC + AAC, <=30 hashtags.
INSTAGRAM = Spec(
    min_duration_s=3.0, max_duration_s=900.0,
    max_size_bytes=1 * 1024**3,
    containers=("mp4", "mov"),
    video_codecs=("h264", "hevc"),
    audio_codecs=("aac",),
    min_fps=23.0, max_fps=60.0, min_width=540,
    caption_limit=2200, max_hashtags=30,
)

SPECS = {"youtube": YOUTUBE, "tiktok": TIKTOK, "instagram": INSTAGRAM}

YOUTUBE_TITLE_LIMIT = 100
TIKTOK_TITLE_LIMIT = 2200

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
_WATERMARK_HINT = re.compile(r"(tiktok|douyin|snapchat|capcut|watermark|ssstik|savefrom)", re.I)


def _check_specs(
    report: Report, platform: str, spec: Spec, media: MediaInfo, require_vertical: bool
) -> None:
    """Technical envelope. Skipped with a warning when ffprobe was unavailable."""
    if media.size_bytes > spec.max_size_bytes:
        report.add(
            ERROR, platform, "size",
            f"file is {media.size_mb:.0f} MB, limit is {spec.max_size_bytes / 1024**3:.0f} GB",
            "re-encode at a lower bitrate",
        )

    if not media.probed:
        report.add(
            WARNING, platform, "unprobed",
            "ffprobe not installed, so duration/resolution/codec checks were skipped",
            "install ffmpeg (apt install ffmpeg) so the upload is validated before it is sent",
        )
        return

    duration = media.duration_s or 0.0
    if duration < spec.min_duration_s:
        report.add(
            ERROR, platform, "too_short",
            f"video is {duration:.1f}s, minimum is {spec.min_duration_s:.0f}s",
        )
    if duration > spec.max_duration_s:
        report.add(
            ERROR, platform, "too_long",
            f"video is {duration:.1f}s, maximum is {spec.max_duration_s:.0f}s",
            "trim the clip or disable this platform for this post",
        )

    if media.container not in spec.containers:
        report.add(
            ERROR, platform, "container",
            f"container '.{media.container}' is not accepted "
            f"(accepted: {', '.join(spec.containers)})",
            "remux to MP4: ffmpeg -i in -c copy out.mp4",
        )
    if media.video_codec and media.video_codec not in spec.video_codecs:
        report.add(
            ERROR, platform, "video_codec",
            f"video codec '{media.video_codec}' is not accepted "
            f"(accepted: {', '.join(spec.video_codecs)})",
            "re-encode: ffmpeg -i in -c:v libx264 -c:a aac out.mp4",
        )
    if media.audio_codec is None:
        report.add(
            WARNING, platform, "no_audio",
            "video has no audio track; silent clips are down-ranked on every short-form feed",
        )
    elif media.audio_codec not in spec.audio_codecs:
        report.add(
            ERROR, platform, "audio_codec",
            f"audio codec '{media.audio_codec}' is not accepted "
            f"(accepted: {', '.join(spec.audio_codecs)})",
            "re-encode audio: ffmpeg -i in -c:v copy -c:a aac out.mp4",
        )

    if media.fps is not None and not (spec.min_fps <= media.fps <= spec.max_fps):
        report.add(
            ERROR, platform, "fps",
            f"{media.fps:.2f} fps is outside the accepted {spec.min_fps:.0f}-{spec.max_fps:.0f} fps",
            "re-encode with -r 30",
        )

    if media.width and media.width < spec.min_width:
        report.add(
            ERROR, platform, "resolution",
            f"width {media.width}px is below the {spec.min_width}px minimum",
        )

    if not media.is_vertical:
        message = f"video is landscape ({media.width}x{media.height})"
        if not require_vertical:
            pass
        elif platform == "youtube":
            report.add(
                ERROR, platform, "aspect",
                message + " so it will not be treated as a Short",
                "crop to 9:16, or set platforms.youtube.short: false to upload it as a normal video",
            )
        else:
            report.add(
                WARNING, platform, "aspect",
                message + "; the feed is vertical, so it will be letterboxed and lose reach",
                "crop to 1080x1920",
            )
    elif require_vertical and media.width and media.height and abs(media.aspect_ratio - 9 / 16) > 0.02:
        report.add(
            WARNING, platform, "aspect",
            f"{media.width}x{media.height} is vertical but not 9:16",
            "1080x1920 fills the frame on all three platforms",
        )


def _check_disclosures(report: Report, config: Config) -> None:
    """Legal and platform-policy declarations that apply across all platforms."""
    disclosure = config.disclosure
    enabled = config.enabled_platforms()

    if not disclosure.privacy_review_confirmed:
        report.add(
            ERROR, "all", "privacy_review",
            "disclosure.privacy_review_confirmed is false",
            "review the footage for other people's faces, house numbers, licence plates and "
            "audible conversation, then set it to true to record that you have",
        )

    # Music. Platform-library audio cannot be attached to a pre-rendered file
    # uploaded through any of these APIs - it has to be added in-app.
    music = disclosure.music
    if music.source == "platform_library":
        report.add(
            ERROR, "all", "music_library",
            "disclosure.music.source is 'platform_library', which API uploads cannot use",
            "commercial-library tracks are only attachable in the app; use original or "
            "separately licensed audio for automated posts",
        )
    elif music.source == "licensed":
        report.add(
            WARNING, "all", "music_licensed",
            f"third-party music declared under licence '{music.license_ref}'",
            "confirm the licence covers social distribution on all three platforms; "
            "Content ID / TikTok's audio matcher will flag it otherwise",
        )

    # AI / synthetic media labelling.
    if disclosure.synthetic_media:
        if "instagram" in enabled:
            report.add(
                WARNING, "instagram", "ai_label",
                "content is declared as AI-generated but the Instagram publishing API has no "
                "AI-disclosure field",
                "open the Reel in the app straight after posting and set the "
                "'AI info' label manually, or Meta may apply it for you",
            )
    else:
        report.add(
            WARNING, "all", "ai_declared_false",
            "content is declared as NOT AI-generated or altered",
            "if any part is AI-generated, voice-cloned or materially altered, set "
            "disclosure.synthetic_media: true - all three platforms require the label",
        )

    # Branded content.
    branded = disclosure.branded_content
    if branded.enabled:
        if branded.own_brand and branded.third_party:
            report.add(
                ERROR, "all", "branded_both",
                "own_brand and third_party are both true",
                "TikTok treats these as mutually exclusive; pick the one that applies",
            )
        if branded.third_party and not branded.partner:
            report.add(
                WARNING, "all", "branded_partner",
                "third-party branded content declared without naming the partner",
            )
        if "youtube" in enabled:
            report.add(
                WARNING, "youtube", "paid_promotion",
                "branded content declared but the YouTube API cannot set the "
                "'contains paid promotion' checkbox",
                "tick it in YouTube Studio right after upload - it is an FTC requirement, "
                "not just a YouTube one",
            )
        if "instagram" in enabled:
            report.add(
                WARNING, "instagram", "paid_partnership",
                "branded content declared but the Instagram API cannot set the "
                "'paid partnership' label",
                "add the paid-partnership label in the app after posting",
            )

    # COPPA. Only YouTube exposes this as an API field, and it is mandatory there.
    if disclosure.made_for_kids and "youtube" in enabled:
        report.add(
            WARNING, "youtube", "made_for_kids",
            "marked as made for kids, so comments, personalised ads and several other "
            "features will be disabled on the video",
        )


def _check_text(report: Report, config: Config, platform: str, spec: Spec) -> None:
    content = config.content
    caption = content.caption_with_hashtags(spec.caption_limit, spec.max_hashtags)

    if not content.title:
        report.add(ERROR, platform, "no_title", "content.title is empty")

    if platform == "youtube":
        if len(content.title) > YOUTUBE_TITLE_LIMIT:
            report.add(
                ERROR, platform, "title_length",
                f"title is {len(content.title)} chars, YouTube's limit is {YOUTUBE_TITLE_LIMIT}",
            )
        if "<" in content.title or ">" in content.title:
            report.add(
                ERROR, platform, "title_chars",
                "YouTube rejects '<' and '>' in titles and descriptions",
            )
        tag_chars = sum(len(t) + 1 for t in content.hashtags)
        if tag_chars > 500:
            report.add(
                ERROR, platform, "tags_length",
                f"hashtags total {tag_chars} chars, YouTube's tag budget is 500",
            )
    elif platform == "tiktok" and len(content.title) > TIKTOK_TITLE_LIMIT:
        report.add(
            ERROR, platform, "title_length",
            f"title is {len(content.title)} chars, TikTok's limit is {TIKTOK_TITLE_LIMIT}",
        )

    if spec.max_hashtags is not None and len(content.hashtags) > spec.max_hashtags:
        report.add(
            WARNING, platform, "hashtag_count",
            f"{len(content.hashtags)} hashtags given, only the first "
            f"{spec.max_hashtags} will be posted",
        )

    if _EMAIL.search(caption) or _PHONE.search(caption):
        report.add(
            WARNING, platform, "personal_data",
            "caption looks like it contains an email address or phone number",
            "public short-form video is a bad place for direct contact details",
        )

    if _WATERMARK_HINT.search(config.video.name):
        report.add(
            WARNING, platform, "watermark",
            f"filename '{config.video.name}' suggests the clip was downloaded from another "
            "platform and may carry a watermark",
            "YouTube treats watermarked re-uploads as reused content and Instagram "
            "down-ranks them; export a clean master instead",
        )


def _check_platform_options(report: Report, config: Config, platform: str) -> None:
    options = config.platforms[platform]

    if platform == "youtube":
        privacy = str(options.get("privacy_status", "private")).lower()
        if privacy not in ("public", "unlisted", "private"):
            report.add(
                ERROR, platform, "privacy",
                f"privacy_status '{privacy}' must be public, unlisted or private",
            )

    if platform == "tiktok":
        privacy = str(options.get("privacy_level", "SELF_ONLY")).upper()
        valid = (
            "PUBLIC_TO_EVERYONE", "MUTUAL_FOLLOW_FRIENDS",
            "FOLLOWER_OF_CREATOR", "SELF_ONLY",
        )
        if privacy not in valid:
            report.add(
                ERROR, platform, "privacy",
                f"privacy_level '{privacy}' must be one of {', '.join(valid)}",
            )
        # TikTok rejects branded content posted privately.
        if config.disclosure.branded_content.enabled and privacy == "SELF_ONLY":
            report.add(
                ERROR, platform, "branded_private",
                "TikTok does not allow branded content with privacy_level SELF_ONLY",
                "either publish it publicly or drop the branded-content declaration",
            )

    if platform == "instagram":
        if not config.platforms["instagram"].get("ig_user_id"):
            report.add(
                ERROR, platform, "ig_user_id",
                "platforms.instagram.ig_user_id is not set",
                "GET /me/accounts then /{page-id}?fields=instagram_business_account",
            )


def evaluate(config: Config, media: MediaInfo) -> Report:
    """Run every check for every enabled platform."""
    report = Report()
    _check_disclosures(report, config)

    for platform in config.enabled_platforms():
        spec = SPECS[platform]
        require_vertical = True
        # A YouTube upload with short: false is an ordinary video - the 3 minute
        # Shorts ceiling and the vertical requirement both stop applying.
        if platform == "youtube" and not config.platforms["youtube"].get("short", True):
            spec = replace(spec, max_duration_s=12 * 3600.0)
            require_vertical = False
        _check_specs(report, platform, spec, media, require_vertical)
        _check_text(report, config, platform, spec)
        _check_platform_options(report, config, platform)

    return report
