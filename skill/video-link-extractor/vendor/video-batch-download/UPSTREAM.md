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

Provider output is acquisition evidence only. The caller must independently
hash, ffprobe and completely decode a downloaded file before accepting it.

Static review performed before copying; synthetic regression tests accompany
the subset. This file does not claim live-platform verification or an automatic
security scanner verdict.
# Dependency distribution

The lock retains the pinned versions and integrity hashes, with tarball URLs changed
from the upstream mirror to the official npm registry. `npm ci --ignore-scripts`
verified these integrity values locally; browser/runtime binaries are not distributed.
