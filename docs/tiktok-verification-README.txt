TikTok URL prefix verification
==============================

Verified prefix:  https://rico36.github.io/ShortVideos
Method:           signature file (DNS is not possible on github.io)
Signature file:   tiktok39tvrURIqVZMGlXhRERvu9c6q2U50zpY.txt

NOTE: the portal's Verify button only enables WITHOUT a trailing slash, even
though the docs describe a prefix as ending in one. Enter it exactly as above.
The prefix still covers every page here, since all of them begin with it:

  /ShortVideos/                     website
  /ShortVideos/terms.html           terms of service
  /ShortVideos/privacy.html         privacy policy
  /ShortVideos/tiktok/callback.html OAuth redirect

DO NOT DELETE the signature file. TikTok re-checks it periodically and the
property can be un-verified if it disappears.

If TikTok ever reissues one, drop the new file in this directory (docs/),
commit, push, wait for the Pages deploy, then click Verify again.

.nojekyll in this directory stops GitHub from running the site through Jekyll,
which guarantees the .txt is served byte-for-byte.
