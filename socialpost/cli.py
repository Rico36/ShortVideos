"""Command line entry point.

  check   validate the video, caption and disclosures - no network, no posting
  post    validate, run the live account checks, then publish

`check` is what you run while writing the caption. `post` re-runs every check
before it touches a network, so an automated run cannot skip the policy gate.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import requests

from . import __version__, config as config_module
from .config import ConfigError
from .media import MediaError, probe
from .policy import ERROR, WARNING, evaluate
from .platforms import PUBLISHERS
from .platforms.youtube import DAILY_UPLOAD_BUDGET
from .platforms.base import PublishError
from .state import State
from .tokens import AuthError, TokenStore

EXIT_OK = 0
EXIT_POLICY = 1
EXIT_CONFIG = 2
EXIT_PUBLISH = 3

DAY_S = 24 * 3600


def log(message: str = "") -> None:
    print(message, flush=True)


def _print_report(report, platforms: list[str]) -> None:
    if not report.findings:
        log("  no findings")
        return
    for severity in (ERROR, WARNING):
        for finding in report.findings:
            if finding.severity == severity and finding.platform in platforms + ["all"]:
                log("  " + finding.format())


def _resolve_platforms(cfg, requested: list[str] | None) -> list[str]:
    enabled = cfg.enabled_platforms()
    if not requested:
        return enabled
    unknown = set(requested) - set(config_module.PLATFORMS)
    if unknown:
        raise ConfigError(f"unknown platform(s): {', '.join(sorted(unknown))}")
    return [p for p in requested if p in enabled]


def _load(args) -> tuple:
    cfg = config_module.load(args.config)
    media = probe(cfg.video)
    return cfg, media


def cmd_check(args) -> int:
    cfg, media = _load(args)
    platforms = _resolve_platforms(cfg, args.platforms)
    if not platforms:
        log("No platforms enabled. Set platforms.<name>.enabled: true in the config.")
        return EXIT_CONFIG

    log(f"Video:     {media.describe()}")
    log(f"sha256:    {media.sha256[:16]}...")
    log(f"Platforms: {', '.join(platforms)}")
    log("")
    log("Policy check")
    report = evaluate(cfg, media)
    _print_report(report, platforms)
    log("")

    errors = [f for f in report.findings if f.severity == ERROR]
    if errors:
        log(f"BLOCKED: {len(errors)} error(s). Nothing would be posted.")
        return EXIT_POLICY
    warnings = len([f for f in report.findings if f.severity == WARNING])
    log(f"PASS: ready to post to {', '.join(platforms)}"
        + (f" ({warnings} warning(s) above)" if warnings else ""))
    return EXIT_OK


def cmd_post(args) -> int:
    cfg, media = _load(args)
    platforms = _resolve_platforms(cfg, args.platforms)
    if not platforms:
        log("No platforms enabled.")
        return EXIT_CONFIG

    log(f"Video:     {media.describe()}")
    log(f"Platforms: {', '.join(platforms)}")
    log("")

    log("Policy check")
    report = evaluate(cfg, media)
    _print_report(report, platforms)
    log("")

    if report.errors("all"):
        log("BLOCKED: cross-platform policy errors. Nothing posted.")
        return EXIT_POLICY
    if args.strict and report.warnings():
        log("BLOCKED: --strict and there are warnings. Nothing posted.")
        return EXIT_POLICY

    runnable = [p for p in platforms if not report.blocked(p)]
    for skipped in [p for p in platforms if p not in runnable]:
        log(f"SKIP {skipped}: policy errors above")
    if not runnable:
        return EXIT_POLICY

    state = State(cfg.state_file)
    tokens = TokenStore(args.token_store)
    failures, posted = [], []

    for platform in runnable:
        log("")
        log(f"--- {platform} ---")

        previous = state.already_posted(media.sha256, platform)
        if previous and not args.allow_duplicate:
            log(f"  already posted as {previous['remote_id']} - skipping "
                "(pass --allow-duplicate to post it again)")
            continue

        if platform == "youtube":
            recent = state.count_since("youtube", DAY_S)
            if recent >= DAILY_UPLOAD_BUDGET:
                log(f"  SKIP: {recent} uploads in the last 24h; the default API quota "
                    f"only funds about {DAILY_UPLOAD_BUDGET}")
                failures.append(platform)
                continue

        publisher = PUBLISHERS[platform](cfg, media, tokens, log)
        try:
            for note in publisher.preflight():
                log(f"  {note}")
        except (AuthError, PublishError) as exc:
            log(f"  FAILED preflight: {exc}")
            failures.append(platform)
            continue

        if args.dry_run:
            log("  dry run: everything checks out, nothing uploaded")
            continue

        try:
            result = publisher.publish()
        except (AuthError, PublishError) as exc:
            log(f"  FAILED: {exc}")
            failures.append(platform)
            continue
        except requests.RequestException as exc:
            log(f"  FAILED: network error: {exc}")
            failures.append(platform)
            continue

        state.record(media.sha256, platform, result.remote_id, result.url)
        posted.append(platform)
        log(f"  POSTED {result.remote_id}" + (f" -> {result.url}" if result.url else ""))
        for note in result.notes:
            log(f"  NOTE: {note}")

    log("")
    if args.dry_run:
        log("Dry run complete - no posts were made.")
        if failures:
            log(f"Would have failed: {', '.join(failures)}")
            return EXIT_PUBLISH
        return EXIT_OK
    log(f"Posted to: {', '.join(posted) if posted else 'nothing'}")
    if failures:
        log(f"Failed:    {', '.join(failures)}")
        return EXIT_PUBLISH
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    default_config = Path(__file__).resolve().parent.parent / "config.yaml"

    # Shared options are attached to both the top level and every subcommand so
    # `post_video.py -c x check` and `post_video.py check -c x` both work.
    # SUPPRESS keeps the subparser from overwriting a value given before the
    # subcommand with its own default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default=argparse.SUPPRESS,
        help=f"path to the post config (default: {default_config})",
    )
    common.add_argument(
        "-p", "--platforms", nargs="+", metavar="NAME", default=argparse.SUPPRESS,
        help="only act on these platforms (must also be enabled in the config)",
    )
    common.add_argument(
        "--token-store", default=argparse.SUPPRESS,
        help="path to the OAuth token file (default: ~/.config/socialpost/tokens.json)",
    )

    parser = argparse.ArgumentParser(
        prog="post_video",
        parents=[common],
        description="Post one short video to TikTok, YouTube and Instagram, "
                    "with a policy gate in front of every upload.",
    )
    parser.add_argument("--version", action="version", version=f"socialpost {__version__}")
    parser.set_defaults(config=str(default_config), platforms=None, token_store=None)

    sub = parser.add_subparsers(dest="command", required=True)

    check = sub.add_parser(
        "check", parents=[common],
        help="validate without posting or touching the network",
    )
    check.set_defaults(func=cmd_check)

    post = sub.add_parser("post", parents=[common], help="validate, then publish")
    post.add_argument("--dry-run", action="store_true",
                      help="run every check including the live account checks, but do not upload")
    post.add_argument("--strict", action="store_true",
                      help="treat warnings as blocking")
    post.add_argument("--allow-duplicate", action="store_true",
                      help="post again even if this exact file was already posted")
    post.set_defaults(func=cmd_post)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        log(f"Config error: {exc}")
        return EXIT_CONFIG
    except MediaError as exc:
        log(f"Media error: {exc}")
        return EXIT_CONFIG
    except AuthError as exc:
        log(f"Auth error: {exc}")
        return EXIT_CONFIG
    except KeyboardInterrupt:
        log("Interrupted.")
        return EXIT_PUBLISH


if __name__ == "__main__":
    sys.exit(main())
