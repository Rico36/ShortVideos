"""One-time OAuth setup for each platform.

  python3 -m socialpost.authorize youtube
  python3 -m socialpost.authorize tiktok
  python3 -m socialpost.authorize instagram

Credentials land in ~/.config/socialpost/tokens.json (0600). App secrets are
read from the environment so nothing sensitive is ever typed into a shell that
records history:

  YT_CLIENT_ID / YT_CLIENT_SECRET
  TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET
  IG_APP_ID / IG_APP_SECRET / IG_SHORT_LIVED_TOKEN

Both loopback capture and manual code paste are supported, because a headless
Raspberry Pi usually cannot open a browser and TikTok will not register an
http://localhost redirect.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests

from .tokens import GOOGLE_TOKEN_URL, GRAPH_URL, TIKTOK_TOKEN_URL, TokenStore

TIMEOUT = 30
LOOPBACK_PORT = 8721
REDIRECT_LOOPBACK = f"http://localhost:{LOOPBACK_PORT}/"

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload "
    "https://www.googleapis.com/auth/youtube.readonly"
)

TIKTOK_AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
# video.publish is the direct-post scope and needs an audited app.
# video.upload only drops the file into the creator's inbox.
TIKTOK_SCOPES_DIRECT = "user.info.basic,video.publish"
TIKTOK_SCOPES_DRAFT = "user.info.basic,video.upload"


def _env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is not set. Export it and re-run.")
    return value


class _CodeHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        _CodeHandler.code = (params.get("code") or [None])[0]
        message = "Authorized. You can close this tab." if _CodeHandler.code \
            else f"No code in callback: {query}"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(message.encode())

    def log_message(self, *args):
        pass  # keep the console clean


def _capture_code(auth_url: str, manual: bool) -> str:
    print("\nOpen this URL and approve access:\n")
    print(auth_url)
    print()
    if manual:
        return input("Paste the 'code' value from the redirect URL: ").strip()

    try:
        webbrowser.open(auth_url)
    except Exception:
        pass
    server = HTTPServer(("127.0.0.1", LOOPBACK_PORT), _CodeHandler)
    server.timeout = 300
    print(f"Waiting for the redirect on {REDIRECT_LOOPBACK} (Ctrl-C to switch to --manual)...")
    while _CodeHandler.code is None:
        server.handle_request()
    return _CodeHandler.code


def authorize_youtube(args) -> None:
    client_id = _env("YT_CLIENT_ID")
    client_secret = _env("YT_CLIENT_SECRET")
    redirect = args.redirect_uri or REDIRECT_LOOPBACK

    auth_url = GOOGLE_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": GOOGLE_SCOPES,
        # offline + consent is what makes Google hand back a refresh token.
        "access_type": "offline",
        "prompt": "consent",
    })
    code = _capture_code(auth_url, args.manual)

    response = requests.post(GOOGLE_TOKEN_URL, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    }, timeout=TIMEOUT)
    if response.status_code != 200:
        sys.exit(f"Token exchange failed ({response.status_code}): {response.text}")
    payload = response.json()
    if "refresh_token" not in payload:
        sys.exit(
            "Google did not return a refresh_token. Remove this app at "
            "myaccount.google.com/permissions and authorize again."
        )

    TokenStore(args.token_store).put("youtube", {
        "client_id": client_id,
        "client_secret": client_secret,
        "access_token": payload["access_token"],
        "refresh_token": payload["refresh_token"],
        "expires_at": time.time() + float(payload.get("expires_in", 3600)),
    })
    print("YouTube authorized.")


def authorize_tiktok(args) -> None:
    client_key = _env("TIKTOK_CLIENT_KEY")
    client_secret = _env("TIKTOK_CLIENT_SECRET")
    redirect = args.redirect_uri
    if not redirect:
        sys.exit(
            "TikTok requires an https redirect URI registered in the developer portal. "
            "Pass it with --redirect-uri and use --manual to paste the code back."
        )
    scopes = TIKTOK_SCOPES_DRAFT if args.draft_only else TIKTOK_SCOPES_DIRECT

    auth_url = TIKTOK_AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": client_key,
        "scope": scopes,
        "response_type": "code",
        "redirect_uri": redirect,
        "state": "socialpost",
    })
    code = urllib.parse.unquote(_capture_code(auth_url, True))

    response = requests.post(TIKTOK_TOKEN_URL, headers={
        "Content-Type": "application/x-www-form-urlencoded",
    }, data={
        "client_key": client_key,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect,
    }, timeout=TIMEOUT)
    payload = response.json()
    if response.status_code != 200 or "access_token" not in payload:
        sys.exit(f"Token exchange failed ({response.status_code}): {response.text}")

    TokenStore(args.token_store).put("tiktok", {
        "client_key": client_key,
        "client_secret": client_secret,
        "access_token": payload["access_token"],
        "refresh_token": payload.get("refresh_token", ""),
        "scope": payload.get("scope", scopes),
        "expires_at": time.time() + float(payload.get("expires_in", 86400)),
    })
    print(f"TikTok authorized with scopes: {payload.get('scope', scopes)}")
    if "video.publish" not in str(payload.get("scope", "")):
        print("Note: without video.publish this client can only upload to the inbox "
              "(set platforms.tiktok.mode: draft).")


def authorize_instagram(args) -> None:
    """Exchange a short-lived token for a 60-day one and find the IG user id."""
    app_id = _env("IG_APP_ID")
    app_secret = _env("IG_APP_SECRET")
    short_lived = _env("IG_SHORT_LIVED_TOKEN")

    response = requests.get(f"{GRAPH_URL}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived,
    }, timeout=TIMEOUT)
    if response.status_code != 200:
        sys.exit(f"Token exchange failed ({response.status_code}): {response.text}")
    payload = response.json()
    long_lived = payload["access_token"]

    TokenStore(args.token_store).put("instagram", {
        "app_id": app_id,
        "app_secret": app_secret,
        "access_token": long_lived,
        "expires_at": time.time() + float(payload.get("expires_in", 60 * 24 * 3600)),
    })
    print("Instagram authorized (long-lived token stored).")

    pages = requests.get(f"{GRAPH_URL}/me/accounts", params={
        "fields": "name,instagram_business_account{id,username}",
        "access_token": long_lived,
    }, timeout=TIMEOUT)
    if pages.status_code != 200:
        print(f"Could not list pages ({pages.status_code}): {pages.text}")
        return
    found = False
    for page in pages.json().get("data", []):
        account = page.get("instagram_business_account")
        if account:
            found = True
            print(f"  page '{page['name']}' -> @{account.get('username')} "
                  f"ig_user_id: {account['id']}")
    if found:
        print("\nPut the ig_user_id into platforms.instagram.ig_user_id in your config.")
    else:
        print("\nNo Instagram Business account is linked to any Page on this login. "
              "Link one in the Instagram app under Settings > Account type and tools.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="socialpost.authorize")
    parser.add_argument("platform", choices=("youtube", "tiktok", "instagram"))
    parser.add_argument("--manual", action="store_true",
                        help="print the URL and prompt for the code instead of listening")
    parser.add_argument("--redirect-uri", default=None,
                        help="registered redirect URI (required for TikTok)")
    parser.add_argument("--draft-only", action="store_true",
                        help="TikTok: request video.upload instead of video.publish")
    parser.add_argument("--token-store", default=None)
    args = parser.parse_args(argv)

    {"youtube": authorize_youtube,
     "tiktok": authorize_tiktok,
     "instagram": authorize_instagram}[args.platform](args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
