#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { pathToFileURL } from "node:url";
import { BilibiliParser } from "../vendor/video-batch-download/scripts/platforms/bilibili.js";
import { DouyinParser } from "../vendor/video-batch-download/scripts/platforms/douyin.js";
import { KuaishouParser } from "../vendor/video-batch-download/scripts/platforms/kuaishou.js";
import { XiaohongshuParser } from "../vendor/video-batch-download/scripts/platforms/xiaohongshu.js";
import { validateParsedVideo } from "../vendor/video-batch-download/scripts/platforms/base.js";
import { downloadMedia } from "../vendor/video-batch-download/scripts/media/downloader.js";
import { identityError, requireSameNativeId } from "../vendor/video-batch-download/scripts/platforms/security.js";

export const PARSERS = Object.freeze({ bilibili: BilibiliParser, douyin: DouyinParser, kuaishou: KuaishouParser, xiaohongshu: XiaohongshuParser });
const HELP = "browser_acquire.mjs --url URL --platform bilibili|douyin|kuaishou|xiaohongshu --output DIR [--metadata-only|--download] [--browser EXECUTABLE] [--storage-state FILE] [--headed]";

export function parseArgs(args, env = process.env) {
  const options = { download: false, headed: false, browser: env.VIDEO_LINK_EXTRACTOR_BROWSER || null, pageTimeoutMs: 45000, mediaWaitMs: 25000, downloadTimeoutMs: 900000 };
  const valueFlags = { "--url": "url", "--platform": "platform", "--output": "output", "--browser": "browser", "--storage-state": "storageState", "--ffmpeg": "ffmpegPath" };
  let mode = null;
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === "--help" || flag === "-h") return { help: true };
    if (flag === "--download" || flag === "--metadata-only") {
      if (mode && mode !== flag) throw new Error("Choose one acquisition mode");
      mode = flag; options.download = flag === "--download"; continue;
    }
    if (flag === "--headed") { options.headed = true; continue; }
    const key = valueFlags[flag];
    if (!key) throw new Error(`Unknown argument: ${flag}`);
    if (index + 1 >= args.length || args[index + 1].startsWith("--")) throw new Error(`Missing value for ${flag}`);
    options[key] = args[++index];
  }
  if (!options.url || !options.platform || !options.output) throw new Error("--url, --platform and --output are required");
  const Parser = PARSERS[options.platform];
  if (!Parser) throw new Error("Unsupported platform");
  const parsedUrl = new URL(options.url);
  if (!["http:", "https:"].includes(parsedUrl.protocol) || parsedUrl.username || parsedUrl.password || parsedUrl.port) throw new Error("Expected a public platform HTTP(S) link without credentials or custom port");
  if (!Parser.matchesUrl(options.url)) throw new Error("URL does not match the explicitly selected platform");
  options.output = path.resolve(options.output);
  if (options.storageState) options.storageState = path.resolve(options.storageState);
  if (options.browser) options.browser = path.resolve(options.browser);
  return options;
}

export function makeProviderResult(options, parsed, downloaded = null) {
  const evidence = parsed.identityEvidence;
  if (!evidence?.expected_id || !evidence?.observed_id) throw identityError("Provider has no exact native identity evidence");
  requireSameNativeId(evidence.expected_id, evidence.observed_id);
  if (!parsed.videoId || !PARSERS[options.platform].matchesUrl(parsed.canonicalUrl)) throw identityError("Provider returned an invalid canonical identity");
  requireSameNativeId(parsed.videoId, evidence.expected_id, "Provider receipt");
  const canonical = new URL(parsed.canonicalUrl);
  const identityTokens = [...(canonical.pathname.match(/[A-Za-z0-9_-]+/g) ?? []), ...canonical.searchParams.values()];
  if (!identityTokens.includes(String(parsed.videoId))) throw identityError("Canonical URL has no exact native identity token");
  if (typeof parsed.title !== "string" || !parsed.title.trim()) throw identityError("Actual target title or caption is missing; no template fallback is allowed");
  return {
    schema_version: 1, provider: "browser-curated-video-batch-download", platform: options.platform,
    status: downloaded ? "downloaded" : "metadata_ready", input_url: options.url, original_url: options.url,
    source_url: options.url, canonical_url: parsed.canonicalUrl, resolved_url: parsed.resolvedUrl ?? parsed.canonicalUrl,
    native_id: String(parsed.videoId), title: parsed.title || "", title_kind: parsed.titleKind === "native_title" ? "title" : parsed.titleKind === "description" ? "caption" : "missing",
    identity_verified: true, identity_evidence: evidence,
    author: parsed.author, description: parsed.description ?? null, duration_hint: parsed.duration ?? null,
    metadata: { author: parsed.author, description: parsed.description ?? null, post_time: parsed.postTime ?? null, statistics: parsed.statistics },
    media_path: downloaded?.filePath ? path.resolve(downloaded.filePath) : null,
    bytes: downloaded?.bytes ?? null, media_has_audio: downloaded?.mediaHasAudio ?? null,
    media_validation: "pending_external_hash_ffprobe_full_decode", final_ready: false,
    acquired_at: new Date().toISOString(),
  };
}

export async function acquire(options, dependencies = {}) {
  const Parser = PARSERS[options.platform];
  if (options.storageState) await fs.access(options.storageState);
  if (options.browser) await fs.access(options.browser);
  let manager = dependencies.browserManager;
  if (!manager) {
    const { BrowserManager } = await import("../vendor/video-batch-download/scripts/utils/browser-manager.js");
    manager = new BrowserManager(options.headed, options.browser);
  }
  try {
    const parser = dependencies.parser ?? new Parser();
    const parsed = validateParsedVideo(await parser.parse(manager, options.url, options), options.platform);
    // Check identity before any network media download.
    makeProviderResult(options, parsed);
    const downloaded = options.download ? await (dependencies.downloadMedia ?? downloadMedia)(parsed, options.output, options.downloadTimeoutMs, options.ffmpegPath ?? null, { requireAudio: false }) : null;
    if (options.download && !downloaded?.filePath) throw new Error("Download produced no local media path");
    return makeProviderResult(options, parsed, downloaded);
  } finally {
    await manager.close();
  }
}

export async function main(args = process.argv.slice(2), dependencies = {}) {
  let options;
  try { options = parseArgs(args); } catch (error) { process.stderr.write(`${error.message}\n${HELP}\n`); return 2; }
  if (options.help) { process.stdout.write(`${HELP}\n`); return 0; }
  const lockPath = path.join(options.output, ".browser-acquire.lock");
  const lockToken = `${process.pid}:${Date.now()}`;
  let lockOwned = false;
  try {
    await fs.mkdir(options.output, { recursive: true });
    if ((await fs.readdir(options.output)).length) throw new Error("Output must be a new or empty directory; existing files are preserved");
    // Exclusive creation also prevents two concurrent callers using the same empty directory.
    await fs.writeFile(lockPath, lockToken, { encoding: "utf8", flag: "wx" });
    lockOwned = true;
  } catch (error) {
    process.stderr.write(`${JSON.stringify({ status: "blocked", error_code: "OUTPUT_EXISTS", message: error.message })}\n`);
    return 2;
  }
  try {
  let result;
  let code = 0;
  try { result = await acquire(options, dependencies); } catch (error) {
    code = 1;
    const blocked = error.category === "access" || error.category === "identity";
    result = {
      schema_version: 1, provider: "browser-curated-video-batch-download", platform: options.platform,
      status: blocked ? "blocked" : "failed", input_url: options.url, original_url: options.url, source_url: options.url,
      canonical_url: null, resolved_url: null, native_id: null, title: "", title_kind: "missing",
      identity_verified: false, media_path: null, final_ready: false,
      error_code: error.code === "ERR_MODULE_NOT_FOUND" ? "RUNTIME_MISSING" : error.code ?? "ACQUISITION_FAILED",
      message: error.userMessage ?? error.message,
      error_message: error.userMessage ?? error.message, acquired_at: new Date().toISOString(),
    };
  }
  const reportPath = path.join(options.output, "provider-result.json");
  // Never replace a receipt created by another caller, even after the empty-directory check.
  await fs.writeFile(reportPath, `${JSON.stringify(result, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
  process.stdout.write(`${JSON.stringify({ status: result.status, result_path: reportPath })}\n`);
  return code;
  } finally {
    if (lockOwned && await fs.readFile(lockPath, "utf8").catch(() => null) === lockToken) {
      await fs.unlink(lockPath).catch(() => {});
    }
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().then((code) => { process.exitCode = code; }).catch((error) => { process.stderr.write(`${error.message}\n`); process.exitCode = 1; });
}
