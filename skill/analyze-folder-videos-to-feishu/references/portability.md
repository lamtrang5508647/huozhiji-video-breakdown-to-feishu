# Runtime and project portability

## Install and start

The distribution contains the Skill, reusable scripts, references and synthetic tests.
Project configuration, remote registries, review evidence, media and credentials are
separate runtime data. Install only `skill/analyze-folder-videos-to-feishu/` under
the agent's chosen skills directory. Do not overwrite an existing installation
without comparing it. A machine-local locator is optional and is never bundled.

When using the repository, `scripts/project.py init --project <new-directory>`
creates a local profile and empty state without contacting Feishu or inventing IDs.
`scripts/project.py demo --output <new-directory>` exercises the local exporters
and renderers with explicitly synthetic media. Neither command configures a remote Base.
Use `--help` for installation and dependency checks. When only the Skill is installed,
invoke the included helpers by absolute path and supply the user's project explicitly.

Old source paths must be rebound to verified file hashes if assets move. Never copy
another project's Base, table, document, owner or product identifiers as defaults.

## Dependency layers

| Stage | Required runtime |
| --- | --- |
| JSON validation and HTML/XML rendering | Python 3.10+ standard library |
| Media probe and reviewed clip export | FFmpeg and FFprobe |
| Exact PTS evidence extraction | PyAV (`av`) and Pillow |
| PNG preview capture | Node.js 22+ and Chrome/Edge/Chromium; no npm package |
| Optional ASR draft | Installed Whisper CLI or whisper.cpp, plus a local model |
| Feishu synchronization | Independently authenticated lark-cli and explicit project profile |

Resolve the actual executables on the host. macOS may use `python3`; Windows may use
`python` or `py -3`. Optional dependencies are required only for the selected stage.
Missing Feishu access does not prevent independent local analysis or rendering.

`offline_asr.py` requires a non-empty local checkpoint or cache by default. Pass
`--model <path>` or `--model-dir <cache-directory>`; named OpenAI Whisper downloads
require the explicit `--allow-download` flag. It does not install an engine.
Output is an ASR draft with `human_verified=false`, never evidence of manual listening.

## Feishu preflight

```text
python <skill-dir>/scripts/check_project.py --project <project-root> --output <receipt.json>
python <skill-dir>/scripts/check_project.py --project <project-root> --live --output <receipt.json>
```

Use the selected profile's `bot` or `user` identity. Empty local state is a legitimate
initialization state; it is not completed publication. Read the live schema before
planning or writing and create/register the first product/platform document explicitly.
The chapter publisher updates already registered groups; it is not a document bootstrapper.

Reuse a working identity. Do not change authentication, proxies or global CLI settings
merely because an old helper assumed user or bot mode. Project setting
`network.ignore_proxy_env=true` affects CLI child processes only and must match the
working network configuration; it is not an authorization workaround.

Legacy `check_environment.py`, `validate_payload.py` and `sync_to_feishu.py` require
an explicit `--config <legacy-config.json>` matching their separate 27-field schema.
The bundled example has placeholders. They are not the product/platform project check.

## Failure handling

- Missing source or media runtime: retain analysis and report the blocked export step.
- Unsupported download: use an authorized local original; source labels do not prove downloader support.
- Identity, scope or schema mismatch: preserve state and reconcile the actual target.
- Changed document or owner: compare the live state before updating or sharing.
- Unclear speech or branding: preserve original evidence and mark uncertainty.
- Ambiguous upload result: read the destination before retrying; never blindly append duplicates.
