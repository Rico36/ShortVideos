# TikTok demo video — shot list

Record in the **sandbox** environment (required: the app has not been approved).
Output mp4 or mov, under 50 MB, max 5 videos. One continuous take is best.

## Before you hit record

- [ ] Sandbox created in the developer portal, and your TikTok account added to it
- [ ] Terminal font size raised — a reviewer must read it on a small player
- [ ] `config.yaml` open in an editor, ready to show
- [ ] A short test clip ready that passes `./post_video.py check`
- [ ] Any previous token cleared, so authorization is shown from scratch:
      `rm ~/.config/socialpost/tokens.json`
- [ ] Browser window ready with the address bar visible

## Shots

**1 — The website (10s)**
Open `https://rico36.github.io/ShortVideos/` in the browser. Let the address bar
be readable. This is the URL on the app details form; showing it first ties the
submission together.
> "This is CommonHours. It's a self-hosted tool I run on my own machine to post
> my own videos to my own TikTok account."

**2 — The app and its disclosures (15s)**
Show `config.yaml`, scroll to the `disclosure:` block.
> "Before anything uploads, I have to declare whether the video is AI-generated,
> whether it's branded content, and how the music is licensed. These are required
> fields with no defaults — the upload is blocked if any is missing."

**3 — Login Kit / user.info.basic (25s)**
Run `python3 -m socialpost.authorize tiktok`.
Show, in order:
  a. TikTok's consent screen, with the requested scopes visible
  b. approving it
  c. the redirect landing on
     `https://rico36.github.io/ShortVideos/tiktok/callback.html`
     — PAUSE HERE so the address bar reads clearly. This is the shot that
     satisfies "the domain shown must match the website URL you provide."
  d. copying the code and pasting it into the terminal
> "Authorization uses Login Kit. The code comes back to my registered redirect
> URI on my own site, and I paste it into the tool. The token is stored locally
> on this machine."

**4 — user.info.basic actually being used (15s)**
Run `./post_video.py post -p tiktok`.
The first line printed is `authorized account: <name>` — LET IT SIT ON SCREEN.
> "Before uploading, CommonHours calls /v2/user/info/ and shows which account
> was authorized. Posting is irreversible, so I confirm the target first. That
> is the only thing user.info.basic is used for."

**5 — creator_info being honoured (15s)**
The next lines show the creator and any honoured account settings.
> "It then reads creator_info. If I've disabled comments, duet or stitch on my
> account, the post request disables them too — my account settings override
> anything in the config file."

**6 — Content Posting API / video.upload (20s)**
Show the chunk progress and the final status line.
> "The video uploads in chunks to the Content Posting API and CommonHours polls
> the status endpoint until TikTok confirms it arrived."

**7 — The result in TikTok (15s)**
Switch to the TikTok app. Open the inbox. Show the uploaded video sitting there.
> "It lands in my TikTok inbox as a draft. I review and publish it myself in the
> app — CommonHours never posts publicly on my behalf."

Total: roughly 2 minutes.

## Self-check before submitting

| Requirement | Shot |
|---|---|
| Complete end-to-end flow | 3 → 7 |
| Login Kit demonstrated | 3 |
| `user.info.basic` demonstrated | 4 |
| Content Posting API demonstrated | 6 |
| `video.upload` demonstrated | 6, 7 |
| Recorded in sandbox | all |
| Registered domain visible | 1 and 3c |
| UI and interactions visible | throughout |
| mp4/mov, under 50 MB | export settings |

Every product and scope on the form must appear above. If one does not, either
add a shot for it or remove it from the form before submitting — an unused
scope is a documented rejection reason.
