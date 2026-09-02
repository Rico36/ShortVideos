TikTok URL prefix verification
==============================

Verified prefix:  https://rico36.github.io/ShortVideos/
Method:           signature file (DNS is not possible on github.io)

TikTok gives you a file named something like
tiktokXXXXXXXXXXXXXXXX.txt or tiktok-developers-site-verification.txt.

Put it in THIS directory (docs/), commit, and push. It is then served at
https://rico36.github.io/ShortVideos/<filename>, which is directly under the
verified prefix, and the portal's Verify button will find it.

Open that URL in a browser and confirm you see the file's contents BEFORE
clicking Verify. GitHub Pages takes a minute or two to redeploy.

Do not delete the file afterwards - TikTok re-checks it periodically.

.nojekyll in this directory stops GitHub from running the site through
Jekyll, which guarantees the .txt is served byte-for-byte.
