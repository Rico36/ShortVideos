# CommonHours site

Static pages published with GitHub Pages. They exist to satisfy the TikTok
developer portal, which requires a reachable Terms of Service URL, Privacy Policy
URL and https redirect URI, all under a verified URL prefix.

| File | Purpose |
|---|---|
| `index.html` | What CommonHours is. The page a reviewer lands on. |
| `privacy.html` | Privacy policy. Describes local-only storage. |
| `terms.html` | Terms of service. |
| `tiktok/callback.html` | OAuth redirect target. Displays the `code` parameter for manual paste; no server, nothing is transmitted. |

## Publishing

Repository Settings > Pages > Source: Deploy from a branch, branch `main`,
folder `/docs`.

## TikTok URL prefix verification

TikTok issues a signature file named `tiktok-developers-site-verification.txt`
(or similar). Drop it in **this directory** so it is served at the root of the
verified prefix, commit, and then verify in the developer portal.

Verify the prefix, not the domain - `github.io` is a shared domain and DNS
verification is not possible on it.
