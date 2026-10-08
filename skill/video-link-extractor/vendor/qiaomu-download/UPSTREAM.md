# qiaomu-download Channels helper provenance

- Upstream: https://github.com/joeseesun/qiaomu-download
- Pinned commit: `8e92fb0c717537902ba1bad7d06c67411ea56c11`
- License: MIT; original copyright and permission notice retained in LICENSE.
- Adapted portions: URL validation and Tencent media-host validation from
  `scripts/wechat/preflight.py` and `scripts/wechat/download_online.py`.
- Local changes: accept Channels page URLs; reject credentials, custom ports,
  control characters and media fragments; exceptions contain only fixed codes.

The acquisition wrapper in ../../scripts/wechat_acquire.py is locally authored.
No fixed online parser, updater, cookies, browser control, certificate installer,
system proxy mutation, backend installer or media decryption is bundled.
The separately licensed wx_channels_download backend is not copied or installed.

An explicitly configured provider must return a real platform ID and caption/title,
and object/feedInfo identity fields matching a native ID/nonce/token in the original
input query. Exact input URL receipts are checked for contradictions only; echoed
URLs or outer response tokens cannot prove identity. An opaque share URL with only
an echoed receipt and ID/media response remains source_binding_missing.
Signed media URLs and authentication data are never included in provider-result.json.
Downloaded means transferred bytes only; the outer acquisition pipeline must still
verify video streams, full decoding and file hashes before reporting ready.
