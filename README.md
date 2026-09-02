# ShortVideos

Post one short vertical clip to **TikTok, YouTube and Instagram** with a single
command — but only after it passes a policy gate.

Small enough to run unattended on a Raspberry Pi: two dependencies, no vendor
SDKs, every call is plain HTTPS against the official APIs. Designed to be
triggered from cron or a Home Assistant automation.

```
./post_video.py check     # validate the file, caption and disclosures. No network.
./post_video.py post      # validate, check the live accounts, then publish.
```

Nothing uploads until every check passes. `check` never touches the network, so
it is safe to run while you are still writing the caption.

## Why there is a policy gate at all

Automated posting is where accounts get restricted, because the API happily
accepts things the platform's own policies do not. Three failure modes matter:

1. **Undisclosed content.** AI-generated footage, paid partnerships and
   children's content all carry mandatory labels. YouTube and TikTok expose
   these as API fields; Instagram does not, so the tool tells you what to set by
   hand.
2. **Music.** All three run audio fingerprinting. A track you did not license is
   the fastest route to a muted video, a copyright strike, or a removed Reel.
3. **Specs.** A 61-second landscape clip is not a Short. A 2-second clip is
   rejected by Reels. Finding this out after a 200 MB upload wastes quota.

So `disclosure:` in the config has **no defaults**. Every field is required, and
a missing one is a hard error. The tool will not guess whether your video is
made for kids.

### What blocks a post

| Check | Why |
|---|---|
| `privacy_review_confirmed: false` | You have not confirmed the footage is free of other people's faces, neighbours' house numbers, licence plates and audible private conversation. Filming your own garden still catches the street. |
| `music.source: platform_library` | Library audio cannot be attached to a pre-rendered file through any of these APIs. It has to be added in the app. |
| Branded content + TikTok `SELF_ONLY` | TikTok rejects branded content posted privately. |
| Duration, size, codec, container, fps, resolution outside a platform's envelope | The upload would be rejected or silently down-ranked. |
| Landscape video with YouTube `short: true` | It would not be classified as a Short. |
| Title over 100 chars, or containing `<` or `>` | YouTube rejects it. |
| Missing `ig_user_id`, bad `privacy_level` | Misconfiguration. |

`--strict` promotes every warning to a blocker, which is what you want in a
fully unattended cron run.

### What only warns

Silent audio, a non-9:16 vertical crop, contact details in the caption, a
filename that suggests a watermarked re-upload, and every "the API cannot set
this, do it in the app" reminder.

## Setup

```bash
git clone https://github.com/Rico36/ShortVideos.git
cd ShortVideos
pip3 install -r requirements.txt
sudo apt install ffmpeg          # ffprobe; without it the spec checks are skipped
cp config.example.yaml config.yaml
```

`config.yaml` is gitignored. `${VAR}` anywhere in it is replaced with that
environment variable, so ids and secrets never have to be written into the file.

### YouTube

1. Google Cloud console: new project, enable **YouTube Data API v3**.
2. OAuth consent screen: External. Add yourself as a test user.
3. Credentials: OAuth client ID, type **Desktop app**.
4. Authorize:

```bash
export YT_CLIENT_ID=... YT_CLIENT_SECRET=...
python3 -m socialpost.authorize youtube
```

Quota is the thing to watch: `videos.insert` costs 1600 units against a default
10,000/day, so about **6 uploads a day**. The tool refuses a 7th rather than
burning the quota on a call that will fail.

### TikTok

1. developers.tiktok.com: create an app, add the **Content Posting API** product.
2. Register an **https** redirect URI. TikTok will not accept `http://localhost`,
   so authorization uses manual code paste.
3. Pick your scope:
   - `video.upload` (`mode: draft`) - the video lands in your TikTok inbox and
     you finish the post in the app. Works immediately, no audit.
   - `video.publish` (`mode: direct`) - posts live. **Requires TikTok to audit
     your app.** Until then every post is forced to private, and the tool says
     so explicitly instead of letting you think it published.

```bash
export TIKTOK_CLIENT_KEY=... TIKTOK_CLIENT_SECRET=...
python3 -m socialpost.authorize tiktok --redirect-uri https://your.domain/callback
# add --draft-only to request video.upload instead of video.publish
```

Before every post the tool calls `/v2/user/info/` and shows which account was
authorized - posting is irreversible, so you see the target first. That is the
only use of the `user.info.basic` scope. It then calls `creator_info/query`, as
TikTok's Content Sharing Guidelines require, and honours what comes back: if you have disabled
comments, duet or stitch on your account, the post respects that regardless of
what the config says. Rate limit is 6 requests per minute per token, so calls
are spaced 10 seconds apart.

### Instagram

Needs a **Business or Creator** account linked to a Facebook Page.

1. developers.facebook.com: create an app, add **Instagram Graph API**.
2. Grant `instagram_business_content_publish` (Instagram Login) or
   `instagram_content_publish` + `pages_read_engagement` (Facebook Login).
3. Get a short-lived user token from the Graph API Explorer, then:

```bash
export IG_APP_ID=... IG_APP_SECRET=... IG_SHORT_LIVED_TOKEN=...
python3 -m socialpost.authorize instagram
```

That prints your `ig_user_id`. The stored token is long-lived (60 days) and is
auto-renewed on use, so a monthly post keeps it alive on its own; go quiet for
two months and you re-authorize.

Limit: 50 published posts per rolling 24 hours, checked live before each post.

## Running it

```bash
./post_video.py check                      # validate only
./post_video.py post --dry-run             # + live account checks, no upload
./post_video.py post                       # publish
./post_video.py post -p youtube instagram  # subset
./post_video.py post --strict              # warnings block too
```

Exit codes: `0` ok, `1` policy block, `2` config/media error, `3` publish failure.

Every successful post is recorded against the video's sha256 in
`~/.local/state/socialpost/posted.json`, so a cron retry or a double-fired HA
automation cannot post the same file twice. `--allow-duplicate` overrides it.

### From Home Assistant

The example below posts a robot-mower clip when the mower docks on a Sunday;
the pattern works for any entity. `configuration.yaml`:

```yaml
shell_command:
  post_mowing_clip: >-
    /home/pi/ShortVideos/post_video.py post --strict
    -c /home/pi/ShortVideos/config.yaml
```

```yaml
automation:
  - alias: Post the weekly mowing clip
    trigger:
      - platform: state
        entity_id: lawn_mower.goat_a3000
        to: docked
    condition:
      - condition: time
        weekday: [sun]
    action:
      - service: shell_command.post_mowing_clip
        response_variable: result
      - if: "{{ result.returncode != 0 }}"
        then:
          - service: notify.mobile_app
            data:
              title: Mowing clip not posted
              message: "{{ result.stdout }}"
```

`--strict` matters here: unattended, you want a warning to stop the post and
tell you, not sail through.

### From cron

```cron
30 18 * * 0 cd /home/pi/ShortVideos && ./post_video.py post --strict >> /var/log/socialpost.log 2>&1
```

## Recommended master format

One export that satisfies all three:

```bash
ffmpeg -i input.mov \
  -vf "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30" \
  -c:v libx264 -profile:v high -pix_fmt yuv420p -b:v 8M \
  -c:a aac -b:a 192k -ar 48000 \
  -movflags +faststart output.mp4
```

MP4 / H.264 / AAC, 1080x1920, 30 fps, under 3 minutes, under 1 GB. That clears
every envelope below with room to spare.

## Platform limits enforced

Current as of September 2026. Platforms move these; `socialpost/policy.py` is
where to update them.

| | YouTube Shorts | TikTok | Instagram Reels |
|---|---|---|---|
| Duration | 1s - 3 min | 3s - per-creator (from `creator_info`) | 3s - 15 min |
| Max size | 256 GB | 4 GB | 1 GB |
| Containers | mp4, mov, webm, mkv, avi | mp4, mov, webm | mp4, mov |
| Video codecs | h264, hevc, vp9, av1 | h264, hevc | h264, hevc |
| Audio codecs | aac, mp3, opus, flac | aac, mp3 | aac |
| Frame rate | 20 - 60 | 23 - 60 | 23 - 60 |
| Min width | 600 | 360 | 540 |
| Caption | 5000 (title 100) | 2200 | 2200, 30 hashtags |
| Posting cap | ~6/day (API quota) | 6 req/min | 50 per 24h |

## Disclosure fields, and where they land

| Config | YouTube | TikTok | Instagram |
|---|---|---|---|
| `synthetic_media` | `status.containsSyntheticMedia` | `post_info.is_aigc` | **no API field** - label in the app |
| `made_for_kids` | `status.selfDeclaredMadeForKids` | n/a | n/a |
| `branded_content.own_brand` | **no API field** - tick "paid promotion" in Studio | `brand_organic_toggle` | **no API field** - label in the app |
| `branded_content.third_party` | as above | `brand_content_toggle` | as above |
| `music.source` | advisory | advisory | advisory |
| `privacy_review_confirmed` | local gate | local gate | local gate |

The gaps are real, not oversights: Instagram's publishing API genuinely has no
AI-disclosure or paid-partnership parameter. Where a label cannot be set through
the API, the tool prints exactly what to set by hand and where, after the post
goes live.

## Layout

```
post_video.py              entry point
config.example.yaml        annotated template
tests.py                   45 offline tests, no network or credentials
socialpost/
  config.py                config parsing; disclosure fields with no defaults
  media.py                 ffprobe wrapper, rotation-aware
  policy.py                specs + policy checks - the gate
  state.py                 sha256 ledger: dedupe and daily counts
  tokens.py                token store (0600) with per-platform refresh
  authorize.py             one-time OAuth setup
  platforms/
    youtube.py             videos.insert, resumable, 256 KB-aligned chunks
    tiktok.py              creator_info -> init -> chunked upload -> status poll
    instagram.py           container -> rupload -> poll -> media_publish
```

```bash
python3 tests.py
```
