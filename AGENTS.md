# Repository instructions

This is the distributable source, not a live Feishu project. Runtime configuration,
registries, source media, derived clips, review evidence and credentials belong in a
separate workspace created with `scripts/project.py init`.

- Read `README.md` and the relevant reference under the Skill before changing a workflow.
- Keep source video, exact transcript, product assignment and observed brand distinct.
- Preserve source hashes, time intervals, phrase offsets, contextual review receipts,
  H1/H2 rendering and exact saved-output verification. A structural pass does not
  establish correct semantic judgment or audio correction.
- Default delivery contains semantic script segments and numbered shot clips.
  A document, thumbnail or uploaded file alone is not proof of gallery delivery.
- Keep the source analysis; default presentation omits `不可直接照搬`,
  `整片受众假设（非测量）` and `复刻素材清单`.
- Never copy an old project's resource IDs, accounts, media or machine-local locator.
  Never infer an upload destination or authorization from a sample config.
- Remote operations require the current task's target and authorization. Reuse it
  across steps; do not ask repeatedly. Keep Feishu mutations serial, journal them,
  and read back before retrying an ambiguous result.
- Use synthetic fixtures for repository tests. Keep real-project evidence outside Git.
- Run `python scripts/run_checks.py` after logic changes; use `--media` when changing
  video/frame exports. Report skipped or untested remote paths honestly.
- Do not add a blanket open-source license until the provenance recorded in
  `PROVENANCE.md` is resolved.
# Link acquisition

- Use `video-link-extractor` before breakdown when starting from a share link.
- A platform route or implemented adapter is not a verified download. Preserve the actual provider status, native ID, title/caption origin and source URL.
- Only a `ready` acquisition with real video stream, complete decode and source SHA-256 may become the breakdown source. It remains unreviewed content.
- Never auto-export cookies, solve captcha, install system proxies/certificates or enable a third-party Channels parser silently.
- Channels request echoes cannot establish object identity. Missing native source anchors, unavailable providers and encrypted-only media remain blocked.
