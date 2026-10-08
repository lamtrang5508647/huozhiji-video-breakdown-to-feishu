# Curated upstream source

Source: https://github.com/ljb1020/video-batch-download

Pinned commit: `be5e41cf7e95b3c3388790bcce91b8becb942ef1`

License: MIT, copyright (c) 2026 Lvjianbing. The original LICENSE is retained.
Only the explicit parser/media utility dependency closure is copied. Upstream
Skill instructions, setup installers, transcription, agent review, plugin
discovery, examples and live outputs are excluded. `package-lock.json` pins
Playwright 1.61.1; package scripts are narrowed to the local synthetic tests.

Local changes require exact native identity, reject recommendation/runtime
fallbacks, refuse verification challenges, support explicitly supplied browser
executables and storage state, and never install browsers or read browser
cookies automatically. Bilibili media is requested with the bound video's CID.
There is no WeChat Channels provider in this upstream subset.

Xiaohongshu candidates are restricted to the exact note's explicit video streams,
originVideoKey and direct video URL fields. Recursive searches through cover,
BGM or nested recommendation metadata are removed. Media URLs require HTTPS
and the actual xhscdn.com hostname boundary; images, HLS and query-only video
labels cannot establish a playable target. These guards have synthetic regressions;
their post-change authenticated live check remains separate from earlier downloads.

Douyin alone distinguishes ordinary comment/SMS-login and optional HD-login UI
from an actual content-access challenge. Such UI is ignored only when an exact
target detail provides its own playable streams. Visible verification widgets,
explicit slider/human verification, verification URLs, risk headers and actual
video login walls still stop acquisition. This does not close login overlays,
read cookies, log in, or request restricted HD streams. Other platform access
policies are unchanged. The ordinary-UI exemption removes only the observed
login-option/footer phrases, rather than dropping every body-text access check.
The current URL is rechecked after waiting; unreadable body or challenge-widget
state stops as unverified. Detail candidates are normalized before both stream
alternatives and the complete parser/core contract validation.

Provider output is acquisition evidence only. The caller must independently
hash, ffprobe and completely decode a downloaded file before accepting it.

Static review performed before copying; synthetic regression tests accompany
the subset. This file does not claim live-platform verification or an automatic
security scanner verdict.
# Dependency distribution

The lock retains the pinned versions and integrity hashes, with tarball URLs changed
from the upstream mirror to the official npm registry. `npm ci --ignore-scripts`
verified these integrity values locally; browser/runtime binaries are not distributed.
