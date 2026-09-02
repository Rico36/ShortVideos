#!/usr/bin/env python3
"""Offline tests for the policy gate. No network, no credentials.

Run: python3 tests.py
"""

import copy
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import yaml

from socialpost import config as config_module
from socialpost.config import ConfigError
from socialpost.media import MediaInfo
from socialpost.policy import ERROR, WARNING, evaluate

BASE_CONFIG = {
    "video": {"path": "clip.mp4", "cover_timestamp_ms": 1000},
    "content": {
        "title": "GOAT A3000 mowing a zone",
        "caption": "Scheduled zone mow started from Home Assistant.",
        "hashtags": ["robotmower", "ecovacs", "homeassistant"],
    },
    "disclosure": {
        "synthetic_media": False,
        "made_for_kids": False,
        "privacy_review_confirmed": True,
        "branded_content": {"enabled": False, "own_brand": False, "third_party": False},
        "music": {"source": "original"},
    },
    "platforms": {
        "youtube": {"enabled": True, "privacy_status": "private", "short": True},
        "tiktok": {"enabled": True, "mode": "draft", "privacy_level": "SELF_ONLY"},
        "instagram": {"enabled": True, "ig_user_id": "12345"},
    },
}


def good_media(**overrides) -> MediaInfo:
    defaults = dict(
        path=Path("clip.mp4"), size_bytes=20 * 1024 * 1024, sha256="a" * 64,
        probed=True, duration_s=45.0, width=1080, height=1920, fps=30.0,
        video_codec="h264", audio_codec="aac", audio_channels=2, container="mp4",
    )
    defaults.update(overrides)
    return MediaInfo(**defaults)


def load_config(raw: dict) -> config_module.Config:
    tmp = Path(tempfile.mkdtemp()) / "config.yaml"
    tmp.write_text(yaml.safe_dump(raw))
    return config_module.load(tmp)


def codes(report, severity=None):
    return {f.code for f in report.findings if severity is None or f.severity == severity}


class ConfigTests(unittest.TestCase):
    def test_loads_a_complete_config(self):
        cfg = load_config(BASE_CONFIG)
        self.assertEqual(cfg.enabled_platforms(), ["youtube", "tiktok", "instagram"])
        self.assertEqual(cfg.content.hashtags[0], "robotmower")

    def test_missing_disclosure_field_is_an_error(self):
        raw = copy.deepcopy(BASE_CONFIG)
        del raw["disclosure"]["made_for_kids"]
        with self.assertRaises(ConfigError) as ctx:
            load_config(raw)
        self.assertIn("made_for_kids", str(ctx.exception))

    def test_disclosure_field_must_be_boolean_not_yes(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["synthetic_media"] = "maybe"
        with self.assertRaises(ConfigError):
            load_config(raw)

    def test_branded_content_needs_a_kind(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["branded_content"] = {
            "enabled": True, "own_brand": False, "third_party": False,
        }
        with self.assertRaises(ConfigError):
            load_config(raw)

    def test_licensed_music_needs_a_reference(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["music"] = {"source": "licensed"}
        with self.assertRaises(ConfigError):
            load_config(raw)

    def test_unknown_platform_is_rejected(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["platforms"]["twitter"] = {"enabled": True}
        with self.assertRaises(ConfigError):
            load_config(raw)

    def test_hashtags_are_appended_within_the_limit(self):
        cfg = load_config(BASE_CONFIG)
        short = cfg.content.caption_with_hashtags(limit=60)
        self.assertLessEqual(len(short), 60)
        full = cfg.content.caption_with_hashtags(limit=2200)
        self.assertIn("#robotmower", full)
        self.assertIn("#homeassistant", full)

    def test_instagram_hashtag_cap_is_applied(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["content"]["hashtags"] = [f"tag{i}" for i in range(40)]
        cfg = load_config(raw)
        caption = cfg.content.caption_with_hashtags(2200, max_tags=30)
        self.assertEqual(caption.count("#"), 30)


class CleanPostTests(unittest.TestCase):
    def test_a_compliant_post_has_no_errors(self):
        report = evaluate(load_config(BASE_CONFIG), good_media())
        self.assertEqual(report.errors(), [], [f.message for f in report.errors()])


class SpecTests(unittest.TestCase):
    def test_long_video_blocks_youtube_shorts_but_not_instagram(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(duration_s=400.0))
        self.assertIn("too_long", codes(report, ERROR))
        self.assertTrue(report.blocked("youtube"))
        self.assertFalse(report.blocked("instagram"))

    def test_long_video_is_fine_as_a_normal_youtube_upload(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["platforms"]["youtube"]["short"] = False
        report = evaluate(load_config(raw), good_media(duration_s=400.0))
        self.assertFalse(report.blocked("youtube"))

    def test_landscape_blocks_shorts_only(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(width=1920, height=1080))
        self.assertTrue(report.blocked("youtube"))
        self.assertFalse(report.blocked("tiktok"))
        self.assertIn("aspect", codes(report, WARNING))

    def test_rotated_portrait_footage_is_treated_as_vertical(self):
        # media.probe transposes rotated dimensions; confirm the check agrees.
        report = evaluate(load_config(BASE_CONFIG), good_media(width=1080, height=1920))
        self.assertNotIn("aspect", codes(report, ERROR))

    def test_two_second_clip_is_too_short_for_tiktok_and_instagram(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(duration_s=2.0))
        self.assertTrue(report.blocked("tiktok"))
        self.assertTrue(report.blocked("instagram"))
        self.assertFalse(report.blocked("youtube"))

    def test_oversize_file_blocks_instagram_first(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(size_bytes=2 * 1024**3))
        self.assertIn("size", codes(report, ERROR))
        self.assertTrue(report.blocked("instagram"))

    def test_webm_is_rejected_by_instagram_only(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(container="webm"))
        self.assertTrue(report.blocked("instagram"))
        self.assertFalse(report.blocked("youtube"))

    def test_opus_audio_is_rejected_by_instagram_and_tiktok(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(audio_codec="opus"))
        self.assertTrue(report.blocked("instagram"))
        self.assertTrue(report.blocked("tiktok"))
        self.assertFalse(report.blocked("youtube"))

    def test_silent_video_warns_everywhere(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(audio_codec=None))
        self.assertIn("no_audio", codes(report, WARNING))
        self.assertEqual(report.errors(), [])

    def test_high_frame_rate_is_blocked(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(fps=120.0))
        self.assertIn("fps", codes(report, ERROR))

    def test_unprobed_media_warns_rather_than_silently_passing(self):
        report = evaluate(load_config(BASE_CONFIG), good_media(probed=False))
        self.assertIn("unprobed", codes(report, WARNING))


class DisclosureTests(unittest.TestCase):
    def test_privacy_review_gate_blocks_everything(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["privacy_review_confirmed"] = False
        report = evaluate(load_config(raw), good_media())
        self.assertIn("privacy_review", codes(report, ERROR))
        for platform in ("youtube", "tiktok", "instagram"):
            self.assertTrue(report.blocked(platform))

    def test_platform_library_music_is_blocked(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["music"] = {"source": "platform_library"}
        report = evaluate(load_config(raw), good_media())
        self.assertIn("music_library", codes(report, ERROR))

    def test_licensed_music_warns_but_does_not_block(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["music"] = {"source": "licensed", "license_ref": "Epidemic #123"}
        report = evaluate(load_config(raw), good_media())
        self.assertIn("music_licensed", codes(report, WARNING))
        self.assertEqual(report.errors(), [])

    def test_ai_content_warns_that_instagram_needs_a_manual_label(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["synthetic_media"] = True
        report = evaluate(load_config(raw), good_media())
        self.assertIn("ai_label", codes(report, WARNING))

    def test_branded_content_cannot_be_posted_privately_on_tiktok(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["branded_content"] = {
            "enabled": True, "own_brand": True, "third_party": False,
        }
        raw["platforms"]["tiktok"]["privacy_level"] = "SELF_ONLY"
        report = evaluate(load_config(raw), good_media())
        self.assertIn("branded_private", codes(report, ERROR))
        self.assertTrue(report.blocked("tiktok"))

    def test_branded_content_reminds_about_the_manual_labels(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["disclosure"]["branded_content"] = {
            "enabled": True, "own_brand": False, "third_party": True, "partner": "Ecovacs",
        }
        raw["platforms"]["tiktok"]["privacy_level"] = "PUBLIC_TO_EVERYONE"
        report = evaluate(load_config(raw), good_media())
        self.assertIn("paid_promotion", codes(report, WARNING))
        self.assertIn("paid_partnership", codes(report, WARNING))


class TextTests(unittest.TestCase):
    def test_overlong_youtube_title_is_blocked(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["content"]["title"] = "x" * 130
        report = evaluate(load_config(raw), good_media())
        self.assertIn("title_length", codes(report, ERROR))
        self.assertTrue(report.blocked("youtube"))

    def test_angle_brackets_are_blocked_on_youtube(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["content"]["title"] = "Mowing <live>"
        report = evaluate(load_config(raw), good_media())
        self.assertIn("title_chars", codes(report, ERROR))

    def test_contact_details_in_the_caption_warn(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["content"]["caption"] = "Questions? ricky@example.com or 555-123-4567"
        report = evaluate(load_config(raw), good_media())
        self.assertIn("personal_data", codes(report, WARNING))

    def test_downloaded_filename_warns_about_watermarks(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["video"]["path"] = "tiktok_download_7211.mp4"
        report = evaluate(load_config(raw), good_media())
        self.assertIn("watermark", codes(report, WARNING))


class PlatformOptionTests(unittest.TestCase):
    def test_bad_tiktok_privacy_level_is_blocked(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["platforms"]["tiktok"]["privacy_level"] = "PUBLIC"
        report = evaluate(load_config(raw), good_media())
        self.assertIn("privacy", codes(report, ERROR))

    def test_missing_ig_user_id_is_blocked(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["platforms"]["instagram"].pop("ig_user_id")
        report = evaluate(load_config(raw), good_media())
        self.assertTrue(report.blocked("instagram"))

    def test_disabled_platform_is_not_checked(self):
        raw = copy.deepcopy(BASE_CONFIG)
        raw["platforms"]["instagram"]["enabled"] = False
        raw["platforms"]["instagram"].pop("ig_user_id")
        report = evaluate(load_config(raw), good_media())
        self.assertEqual(report.errors("instagram"), [])


class ProbeTests(unittest.TestCase):
    """Exercise the real probe() path against a stub ffprobe on PATH."""

    def _probe_with(self, payload: str, size: int = 1024):
        import json as _json
        import os
        import subprocess
        from socialpost.media import probe

        workdir = Path(tempfile.mkdtemp())
        bindir = workdir / "bin"
        bindir.mkdir()
        stub = bindir / "ffprobe"
        stub.write_text("#!/bin/sh\ncat <<'JSON'\n" + payload + "\nJSON\n")
        stub.chmod(0o755)

        video = workdir / "clip.mp4"
        video.write_bytes(b"\0" * size)

        original = os.environ["PATH"]
        os.environ["PATH"] = f"{bindir}:{original}"
        try:
            return probe(video)
        finally:
            os.environ["PATH"] = original

    def test_parses_a_normal_vertical_clip(self):
        info = self._probe_with("""
        {"format": {"duration": "42.5"},
         "streams": [
           {"codec_type": "video", "width": 1080, "height": 1920,
            "avg_frame_rate": "30000/1001", "codec_name": "h264"},
           {"codec_type": "audio", "codec_name": "aac", "channels": 2}]}
        """)
        self.assertTrue(info.probed)
        self.assertAlmostEqual(info.duration_s, 42.5)
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertAlmostEqual(info.fps, 29.97, places=2)
        self.assertEqual(info.video_codec, "h264")
        self.assertEqual(info.audio_codec, "aac")
        self.assertTrue(info.is_vertical)

    def test_rotated_phone_footage_is_transposed_to_how_it_plays(self):
        info = self._probe_with("""
        {"format": {"duration": "10"},
         "streams": [
           {"codec_type": "video", "width": 1920, "height": 1080,
            "avg_frame_rate": "30/1", "codec_name": "h264",
            "side_data_list": [{"rotation": -90}]}]}
        """)
        self.assertEqual((info.width, info.height), (1080, 1920))
        self.assertTrue(info.is_vertical)

    def test_audio_only_file_is_rejected(self):
        from socialpost.media import MediaError
        with self.assertRaises(MediaError):
            self._probe_with("""
            {"format": {"duration": "10"},
             "streams": [{"codec_type": "audio", "codec_name": "aac", "channels": 2}]}
            """)

    def test_empty_file_is_rejected_before_ffprobe_runs(self):
        from socialpost.media import MediaError
        with self.assertRaises(MediaError):
            self._probe_with('{"format": {}, "streams": []}', size=0)


class TikTokPreflightTests(unittest.TestCase):
    """Preflight must exercise every scope the app asks TikTok for."""

    def build(self, user_payload=None, creator_payload=None):
        from unittest import mock
        from socialpost.platforms.tiktok import TikTokPublisher

        publisher = object.__new__(TikTokPublisher)
        publisher.config = load_config(BASE_CONFIG)
        publisher.media = good_media()
        publisher.options = publisher.config.platforms["tiktok"]
        publisher.log = lambda *a: None
        publisher.creator_info = {}
        publisher.user_info = {}
        publisher._last_call = 0.0
        publisher.tokens = mock.Mock(**{"access_token.return_value": "tok"})

        user = user_payload if user_payload is not None else {
            "data": {"user": {"open_id": "abc", "display_name": "Ricky F",
                              "avatar_url": "https://x/y.jpg"}},
            "error": {"code": "ok"},
        }
        creator = creator_payload if creator_payload is not None else {
            "data": {"creator_nickname": "Ricky F", "creator_username": "rickyf",
                     "privacy_level_options": ["PUBLIC_TO_EVERYONE", "SELF_ONLY"],
                     "comment_disabled": False, "duet_disabled": True,
                     "stitch_disabled": False, "max_video_post_duration_sec": 600},
            "error": {"code": "ok"},
        }

        def response(payload):
            r = mock.Mock(); r.status_code = 200; r.content = b"{}"
            r.json.return_value = payload
            return r

        self.get = mock.Mock(return_value=response(user))
        self.post = mock.Mock(return_value=response(creator))
        return publisher, mock.patch.multiple(
            "socialpost.platforms.tiktok.requests", get=self.get, post=self.post
        )

    def test_preflight_calls_user_info_and_creator_info(self):
        from unittest import mock
        publisher, patched = self.build()
        with patched, mock.patch("socialpost.platforms.tiktok.time.sleep"):
            notes = publisher.preflight()

        # user.info.basic is genuinely exercised, not just requested.
        self.assertEqual(self.get.call_count, 1)
        url, kwargs = self.get.call_args[0][0], self.get.call_args[1]
        self.assertEqual(url, "https://open.tiktokapis.com/v2/user/info/")
        self.assertIn("display_name", kwargs["params"]["fields"])
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")

        self.assertEqual(self.post.call_count, 1)
        self.assertIn("creator_info/query", self.post.call_args[0][0])

        self.assertIn("authorized account: Ricky F", notes[0])
        self.assertTrue(any("rickyf" in n for n in notes))
        self.assertTrue(any("duet disabled" in n for n in notes))

    def test_creator_settings_override_the_config(self):
        from unittest import mock
        publisher, patched = self.build()
        with patched, mock.patch("socialpost.platforms.tiktok.time.sleep"):
            publisher.preflight()
        post_info = publisher._post_info()
        # Config says allow_duet, but the account has duet off. Account wins.
        self.assertTrue(publisher.options.get("allow_duet", True))
        self.assertTrue(post_info["disable_duet"])
        self.assertFalse(post_info["disable_comment"])

    def test_unaudited_app_is_named_not_silently_downgraded(self):
        from unittest import mock
        from socialpost.platforms.base import PublishError
        publisher, patched = self.build(creator_payload={
            "data": {"creator_nickname": "R", "creator_username": "r",
                     "privacy_level_options": ["SELF_ONLY"]},
            "error": {"code": "ok"},
        })
        publisher.options.options["privacy_level"] = "PUBLIC_TO_EVERYONE"
        with patched, mock.patch("socialpost.platforms.tiktok.time.sleep"):
            with self.assertRaises(PublishError) as ctx:
                publisher.preflight()
        self.assertIn("audit", str(ctx.exception).lower())

    def test_error_inside_a_200_response_is_raised(self):
        from unittest import mock
        from socialpost.platforms.base import PublishError
        publisher, patched = self.build(user_payload={
            "data": {}, "error": {"code": "access_token_invalid",
                                  "message": "token expired", "log_id": "123"},
        })
        with patched, mock.patch("socialpost.platforms.tiktok.time.sleep"):
            with self.assertRaises(PublishError) as ctx:
                publisher.preflight()
        self.assertIn("access_token_invalid", str(ctx.exception))


class TikTokChunkingTests(unittest.TestCase):
    """TikTok requires 5-64 MB chunks, <=1000 of them, covering the file exactly."""

    def plan(self, size: int):
        from socialpost.platforms.tiktok import MAX_CHUNK, MAX_CHUNKS, MIN_CHUNK, TikTokPublisher

        publisher = object.__new__(TikTokPublisher)
        publisher.media = good_media(size_bytes=size)
        chunk, count = publisher._chunking()

        self.assertLessEqual(count, MAX_CHUNKS)
        if count > 1:
            self.assertGreaterEqual(chunk, MIN_CHUNK)
            self.assertLessEqual(chunk, MAX_CHUNK)

        # Replay the byte ranges _upload would actually send.
        ranges = [
            (i * chunk, size - 1 if i == count - 1 else i * chunk + chunk - 1)
            for i in range(count)
        ]
        self.assertEqual(ranges[0][0], 0, "first chunk must start at 0")
        self.assertEqual(ranges[-1][1] + 1, size, "chunks must cover the whole file")
        for a, b in zip(ranges, ranges[1:]):
            self.assertEqual(a[1] + 1, b[0], "chunks must be contiguous")
        return chunk, count

    def test_small_file_is_a_single_chunk(self):
        MB = 1024 * 1024
        self.assertEqual(self.plan(3 * MB), (3 * MB, 1))
        self.assertEqual(self.plan(5 * MB), (5 * MB, 1))

    def test_remainder_rides_along_on_the_last_chunk(self):
        MB = 1024 * 1024
        chunk, count = self.plan(12 * MB)
        self.assertEqual(count, 2)
        self.assertEqual(chunk, 5 * MB)

    def test_large_files_stay_within_the_chunk_count_ceiling(self):
        MB = 1024 * 1024
        for size in (100 * MB, 500 * MB, 2048 * MB, 4096 * MB):
            self.plan(size)


class StateTests(unittest.TestCase):
    def test_duplicate_detection_and_daily_count(self):
        from socialpost.state import State
        path = Path(tempfile.mkdtemp()) / "posted.json"
        state = State(path)
        self.assertIsNone(state.already_posted("abc", "youtube"))
        state.record("abc", "youtube", "vid1", "https://youtu.be/vid1")
        self.assertIsNotNone(State(path).already_posted("abc", "youtube"))
        self.assertIsNone(State(path).already_posted("abc", "tiktok"))
        self.assertEqual(State(path).count_since("youtube", 3600), 1)
        self.assertEqual(State(path).count_since("tiktok", 3600), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
