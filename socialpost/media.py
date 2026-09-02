"""Probe the video file so the policy gate has real numbers to check.

ffprobe ships with ffmpeg and is already present on most Home Assistant hosts.
If it is missing we still return what we can from the filesystem and let the
policy layer downgrade the checks it cannot perform to warnings - a missing
tool should not silently turn into a green light.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path


class MediaError(Exception):
    pass


@dataclass
class MediaInfo:
    path: Path
    size_bytes: int
    sha256: str
    probed: bool = False
    duration_s: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    container: str | None = None

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)

    @property
    def aspect_ratio(self) -> float | None:
        """Width divided by height. 0.5625 is 9:16, 1.0 is square."""
        if not self.width or not self.height:
            return None
        return self.width / self.height

    @property
    def is_vertical(self) -> bool:
        ratio = self.aspect_ratio
        return ratio is not None and ratio <= 1.0

    def describe(self) -> str:
        if not self.probed:
            return f"{self.path.name} ({self.size_mb:.1f} MB, not probed)"
        return (
            f"{self.path.name} ({self.size_mb:.1f} MB, {self.duration_s:.1f}s, "
            f"{self.width}x{self.height}, {self.fps:.2f} fps, "
            f"{self.video_codec}/{self.audio_codec or 'no audio'})"
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_fps(rate: str | None) -> float | None:
    if not rate or rate in ("0/0", "0"):
        return None
    try:
        return float(Fraction(rate))
    except (ValueError, ZeroDivisionError):
        return None


def probe(path: Path) -> MediaInfo:
    if not path.is_file():
        raise MediaError(f"video file not found: {path}")

    info = MediaInfo(
        path=path,
        size_bytes=path.stat().st_size,
        sha256=_sha256(path),
        container=path.suffix.lower().lstrip("."),
    )
    if info.size_bytes == 0:
        raise MediaError(f"video file is empty: {path}")

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return info

    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-print_format", "json",
                "-show_format", "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        payload = json.loads(result.stdout)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise MediaError(f"ffprobe could not read {path}: {exc}") from exc

    streams = payload.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError(f"{path} contains no video stream")

    fmt = payload.get("format", {})
    duration = fmt.get("duration") or video.get("duration")

    info.probed = True
    info.duration_s = float(duration) if duration else None
    info.width = int(video.get("width") or 0) or None
    info.height = int(video.get("height") or 0) or None
    info.fps = _parse_fps(video.get("avg_frame_rate") or video.get("r_frame_rate"))
    info.video_codec = video.get("codec_name")
    info.audio_codec = audio.get("codec_name") if audio else None
    info.audio_channels = int(audio.get("channels")) if audio and audio.get("channels") else None

    # A rotation side-data tag means the stored dimensions are transposed
    # relative to how the video actually plays. Vertical-video checks would
    # otherwise fail on phone footage that plays back perfectly.
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            try:
                rotation = abs(int(side["rotation"])) % 180
            except (TypeError, ValueError):
                rotation = 0
    if rotation == 90 and info.width and info.height:
        info.width, info.height = info.height, info.width

    if info.duration_s is None:
        raise MediaError(f"could not determine duration of {path}")
    return info
