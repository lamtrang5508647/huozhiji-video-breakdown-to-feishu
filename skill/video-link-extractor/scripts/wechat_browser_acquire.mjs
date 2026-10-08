#!/usr/bin/env node
import fs from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { randomUUID, createHash } from "node:crypto";
import { pathToFileURL } from "node:url";

export const PARSE_ENDPOINT = "https://yuanbao.tencent.com/api/weixin/get_parse_result";
export const FEED_ENDPOINT = "https://channels.weixin.qq.com/finder-preview/api/feed/get_feed_info";
const PLAY_PATH = "/finder-preview/pages/feed";
const validatedChains = new WeakSet();
const HELP = "wechat_browser_acquire.mjs --url https://weixin.qq.com/sph/ID --output DIR --allow-tencent-parse [--manual-login] [--metadata-only|--download] [--browser EXECUTABLE] [--storage-state FILE] [--headed] [--ffmpeg EXECUTABLE] [--timeout-ms MILLISECONDS]";

function failure(code, message, category = "identity") {
  return Object.assign(new Error(message), { code, category });
}

export function validateInputUrl(value) {
  if (typeof value !== "string" || !/^https:\/\/weixin\.qq\.com\/sph\/[A-Za-z0-9]+\/?$/.test(value)) {
    throw failure("INVALID_INPUT_URL", "A single HTTPS weixin.qq.com/sph link is required");
  }
  return value;
}

export function parseArgs(args, env = process.env) {
  const options = { download: false, headed: false, allowTencentParse: false, manualLogin: false,
    browser: env.VIDEO_LINK_EXTRACTOR_BROWSER || null, pageTimeoutMs: 45000,
    feedTimeoutMs: 25000, loginTimeoutMs: 900000, downloadTimeoutMs: 900000 };
  const valueFlags = { "--url": "url", "--output": "output", "--browser": "browser",
    "--storage-state": "storageState", "--ffmpeg": "ffmpegPath", "--timeout-ms": "timeoutMs" };
  const seen = new Set();
  let mode;
  for (let index = 0; index < args.length; index++) {
    const flag = args[index];
    if (flag === "--help" || flag === "-h") return { help: true };
    if (seen.has(flag)) throw failure("INVALID_ARGUMENT", "Duplicate arguments are not allowed");
    seen.add(flag);
    if (flag === "--download" || flag === "--metadata-only") {
      if (mode) throw failure("INVALID_ARGUMENT", "Choose one acquisition mode");
      mode = flag; options.download = flag === "--download"; continue;
    }
    if (flag === "--headed") { options.headed = true; continue; }
    if (flag === "--allow-tencent-parse") { options.allowTencentParse = true; continue; }
    if (flag === "--manual-login") { options.manualLogin = true; options.headed = true; continue; }
    const key = valueFlags[flag];
    if (!key || index + 1 >= args.length || args[index + 1].startsWith("--")) {
      throw failure("INVALID_ARGUMENT", "Unknown argument or missing value");
    }
    options[key] = args[++index];
  }
  if (!options.url || !options.output) throw failure("INVALID_ARGUMENT", "--url and --output are required");
  validateInputUrl(options.url);
  options.output = path.resolve(options.output);
  for (const field of ["browser", "storageState"]) {
    if (options[field]) options[field] = path.resolve(options[field]);
  }
  if (options.ffmpegPath && /[\\/]/.test(options.ffmpegPath)) options.ffmpegPath = path.resolve(options.ffmpegPath);
  if (options.timeoutMs != null) {
    if (!/^[1-9]\d*$/.test(options.timeoutMs) || !Number.isSafeInteger(Number(options.timeoutMs)) || Number(options.timeoutMs) > 2147483647) {
      throw failure("INVALID_ARGUMENT", "--timeout-ms must be a positive integer within the timer range");
    }
    options.timeoutMs = Number(options.timeoutMs);
  }
  return options;
}

function endpointMatches(value, expected) {
  try {
    const actual = new URL(value), wanted = new URL(expected);
    return actual.origin === wanted.origin && actual.pathname === wanted.pathname
      && !actual.username && !actual.password && !actual.hash;
  } catch { return false; }
}

function exactParameter(url, name) {
  const values = url.searchParams.getAll(name);
  if (values.length !== 1 || !values[0] || /\s|[\u0000-\u001f\u007f]/u.test(values[0])) {
    throw failure("IDENTITY_UNVERIFIED", "The platform playback identity parameters are missing or ambiguous");
  }
  return values[0];
}

export function validatePlayableUrl(value) {
  let url;
  if (typeof value !== "string" || /\s|[\u0000-\u001f\u007f]/u.test(value)) {
    throw failure("IDENTITY_UNVERIFIED", "Invalid platform playback URL");
  }
  try { url = new URL(value); } catch { throw failure("IDENTITY_UNVERIFIED", "Invalid platform playback URL"); }
  if (url.origin !== "https://channels.weixin.qq.com" || url.pathname !== PLAY_PATH
      || url.username || url.password || url.hash) {
    throw failure("IDENTITY_UNVERIFIED", "Playback URL is outside the expected Tencent page");
  }
  return { url, rawUrl: value, eid: exactParameter(url, "eid"), token: exactParameter(url, "token") };
}

function checkHttp(response, stage, endpoint = null) {
  const status = response?.status;
  if (status === 401) throw failure("LOGIN_REQUIRED", "Normal Tencent login is required", "access");
  const headers = response?.headers ?? {};
  if (status === 403 || /risk-control/i.test(headers["intercept-result"] ?? "")
      || (headers["x-risk-control"] && headers["x-risk-control"] !== "0")) {
    throw failure("ACCESS_BLOCKED", "Tencent rejected access or requested verification", "access");
  }
  if (!Number.isInteger(status) || status < 200 || status >= 300) {
    throw failure("PLATFORM_HTTP_ERROR", `${stage} did not return a successful HTTP status`, "platform");
  }
  if (!response.body || typeof response.body !== "object" || Array.isArray(response.body)) {
    throw failure("INVALID_PLATFORM_RESPONSE", `${stage} did not return an object`, "platform");
  }
  if (response.redirected === true || (endpoint && response.url && !endpointMatches(response.url, endpoint))) {
    throw failure("IDENTITY_UNVERIFIED", "A platform response was redirected outside its expected endpoint");
  }
}

function rejectEncryption(value, depth = 0) {
  if (!value || typeof value !== "object" || depth > 12) return;
  for (const [key, child] of Object.entries(value)) {
    if (/cover|poster|picture|picInfo|bgm|author|recommend|related/i.test(key)) continue;
    if (/crypto|encrypt|decrypt|decode.?key|drm/i.test(key)
        && child !== null && child !== undefined && child !== "" && child !== false
        && child !== 0 && child !== "0") {
      throw failure("ENCRYPTED_MEDIA_UNSUPPORTED", "Encrypted or protected media is not accepted", "access");
    }
    if (child && typeof child === "object") rejectEncryption(child, depth + 1);
  }
}

function mediaUrl(value) {
  if (typeof value !== "string" || !value) return null;
  if (/\s|[\u0000-\u001f\u007f]/u.test(value)) throw failure("INVALID_MEDIA_URL", "Invalid target media URL");
  let url;
  try { url = new URL(value); } catch { throw failure("INVALID_MEDIA_URL", "Invalid target media URL"); }
  if (url.origin !== "https://finder.video.qq.com" || url.username || url.password || url.hash
      || /\.m3u8(?:[?#]|$)|\.(?:ts|m2ts)(?:[?#]|$)/i.test(value)) {
    throw failure("INVALID_MEDIA_URL", "Target media is outside the supported Tencent video CDN");
  }
  // Keep every signed query parameter exactly as the platform supplied it.
  return value;
}

export function validateTencentChain({ inputUrl, parseRequest, parseResponse, feedRequest, feedResponse }) {
  validateInputUrl(inputUrl);
  if (!endpointMatches(parseRequest?.url, PARSE_ENDPOINT)
      || parseRequest?.method !== "POST" || parseRequest?.body?.type !== "video_channel_url"
      || parseRequest.body.url !== inputUrl || parseRequest.body.scene !== 1) {
    throw failure("IDENTITY_UNVERIFIED", "Parse request was not bound to the exact input link");
  }
  checkHttp(parseResponse, "Tencent parse", PARSE_ENDPOINT);
  if (parseResponse.body.code !== 0) {
    const login = /登录|login|auth/i.test(String(parseResponse.body.msg ?? ""));
    throw failure(login ? "LOGIN_REQUIRED" : "PLATFORM_PARSE_ERROR",
      login ? "Normal Tencent login is required" : "Tencent did not resolve this link", login ? "access" : "platform");
  }
  const parsed = parseResponse.body.data;
  if (!parsed || typeof parsed.wx_export_id !== "string" || !parsed.wx_export_id.trim()) {
    throw failure("INVALID_PLATFORM_RESPONSE", "Tencent parse identity is missing", "platform");
  }
  rejectEncryption(parsed);
  const play = validatePlayableUrl(parsed.playable_url);
  if (!endpointMatches(feedRequest?.url, FEED_ENDPOINT) || feedRequest?.method !== "POST"
      || feedRequest?.body?.exportId !== play.eid
      || feedRequest?.body?.baseReq?.generalToken !== play.token) {
    throw failure("IDENTITY_UNVERIFIED", "Feed request identity does not match this playback response");
  }
  checkHttp(feedResponse, "Tencent feed", FEED_ENDPOINT);
  const response = feedResponse.body;
  if (response.errCode !== 0 || response.data?.errMsg?.type !== 0) {
    throw failure("ACCESS_BLOCKED", "Tencent reported an unavailable or restricted target", "access");
  }
  if (response.data?.sceneInfo?.dynamicExportId !== play.eid) {
    throw failure("IDENTITY_UNVERIFIED", "Feed response export identity differs from this target");
  }
  const feed = response.data.feedInfo;
  if (!feed || typeof feed !== "object" || Array.isArray(feed)) {
    throw failure("METADATA_MISSING", "Target feed metadata is missing");
  }
  rejectEncryption(response.data);
  if (typeof feed.description !== "string" || !feed.description.trim()) {
    throw failure("METADATA_MISSING", "Actual target caption is missing");
  }
  const streams = [...new Set([feed.h264VideoInfo?.videoUrl, feed.h265VideoInfo?.videoUrl, feed.videoUrl]
    .filter((value) => value != null && value !== "").map(mediaUrl).filter(Boolean))];
  if (!streams.length) throw failure("TARGET_MEDIA_MISSING", "The bound target has no supported video stream");
  const evidence = Object.freeze({ kind: "tencent_same_origin_request_chain", parse_input_url: inputUrl,
    parse_playable_url: play.rawUrl, parse_endpoint: PARSE_ENDPOINT, feed_endpoint: FEED_ENDPOINT, play_eid: play.eid,
    feed_export_id: feedRequest.body.exportId, dynamic_export_id: response.data.sceneInfo.dynamicExportId,
    play_token_matched: true, parse_http_status: parseResponse.status, feed_http_status: feedResponse.status,
    parse_code: parseResponse.body.code, feed_err_code: response.errCode, business_error_type: response.data.errMsg.type });
  const nickname = response.data.authorInfo?.nickname;
  const chain = Object.freeze({ inputUrl, eid: play.eid, canonicalUrl: play.rawUrl,
    caption: feed.description, author: Object.freeze({ nickname: typeof nickname === "string" ? nickname : null,
      uid: null, url: null }), createTime: feed.createtime ?? null, streams: Object.freeze(streams), evidence });
  validatedChains.add(chain);
  return chain;
}

function outputMediaPath(output, value) {
  if (typeof value !== "string" || !path.isAbsolute(value)) throw failure("INVALID_DOWNLOAD_RESULT", "Download returned no absolute local media path", "output");
  const relative = path.relative(path.resolve(output), path.resolve(value));
  if (!relative || relative.startsWith(`..${path.sep}`) || relative === ".." || path.isAbsolute(relative)) {
    throw failure("INVALID_DOWNLOAD_RESULT", "Download returned a path outside its output directory", "output");
  }
  return path.resolve(value);
}

export function buildProviderResult(options, chain, downloaded = null) {
  if (!validatedChains.has(chain) || options.url !== chain.inputUrl) {
    throw failure("IDENTITY_UNVERIFIED", "A validated request chain is required before producing a receipt");
  }
  return { schema_version: 1, provider: "tencent-same-origin-browser", platform: "wechat_channels",
    status: downloaded ? "downloaded" : "metadata_ready", input_url: options.url, original_url: options.url,
    source_url: options.url, canonical_url: chain.canonicalUrl, resolved_url: chain.canonicalUrl,
    native_id: chain.eid, native_id_kind: "platform_export_id", platform_export_id: chain.eid,
    identity_stability_verified: false, identity_verified: true, identity_evidence: { ...chain.evidence },
    title: chain.caption, title_kind: "caption", author: { ...chain.author }, description: chain.caption,
    metadata: { author: { ...chain.author }, description: chain.caption, create_time: chain.createTime },
    media_path: downloaded ? outputMediaPath(options.output, downloaded.filePath) : null,
    bytes: downloaded?.bytes ?? null, media_has_audio: downloaded?.mediaHasAudio ?? null,
    media_validation: "pending_external_hash_ffprobe_full_decode", final_ready: false,
    acquired_at: new Date().toISOString() };
}

// Read only login controls and the login dialog, never body text, chat history, or account names.
export async function checkLoginUi(page) {
  const state = await page.evaluate(() => {
    const visible = (node) => {
      const style = getComputedStyle(node), rect = node.getBoundingClientRect();
      return style.display !== "none" && style.visibility !== "hidden" && Number(style.opacity) !== 0
        && rect.width > 0 && rect.height > 0;
    };
    const controls = [...document.querySelectorAll('[aria-label="登录"],[aria-label="微信登录"],[aria-label="扫码登录"],[data-testid="login-button"],[class*="login-btn"],[class*="loginBtn"],[class*="login-button"]')];
    const loginVisible = controls.some(visible);
    const dialogs = [...document.querySelectorAll('[data-testid="login-dialog"],[class*="login-dialog"],[class*="login-modal"],[class*="LoginDialog"],[class*="LoginModal"]')];
    const dialogVisible = dialogs.some(visible);
    const challenge = [...document.querySelectorAll('#captcha_container,#captcha_verify_container,.secsdk-captcha-drag-wrapper,[data-testid="captcha-container"]')].some(visible);
    const composerReady = [...document.querySelectorAll('textarea,[contenteditable="true"],input[placeholder]')].some(visible);
    return { loggedIn: composerReady && !loginVisible && !dialogVisible, loginVisible, dialogVisible, challenge };
  }).catch(() => null);
  if (!state || typeof state.loggedIn !== "boolean" || typeof state.challenge !== "boolean") {
    throw failure("ACCESS_STATE_UNVERIFIED", "Login controls could not be inspected", "access");
  }
  if (state.challenge) throw failure("ACCESS_BLOCKED", "Tencent requested a verification challenge", "access");
  return state;
}

async function prepareManualLogin(page, options, progress) {
  for (const label of ["登录", "微信登录"]) {
    const control = page.getByRole("button", { name: label, exact: true }).first();
    if (await control.isVisible().catch(() => false)) await control.click({ timeout: 5000 });
  }
  // Only the explicitly identified login modal may be captured, never the whole page or chat UI.
  const qr = page.locator('[data-testid="login-dialog"],[class*="login-dialog"],[class*="login-modal"],[class*="LoginDialog"],[class*="LoginModal"]').first();
  if (await qr.isVisible().catch(() => false)) {
    const qrPath = path.join(options.output, "yuanbao-login.png");
    await fs.access(qrPath).then(() => { throw failure("OUTPUT_EXISTS", "Login screenshot already exists", "output"); }, (error) => { if (error.code !== "ENOENT") throw error; });
    await qr.screenshot({ path: qrPath, timeout: 5000 });
    progress({ status: "login_pending", qr_path: qrPath });
  } else progress({ status: "login_pending" });
}

async function requireLogin(page, options, dependencies, progress) {
  const check = dependencies.checkLoginState ?? checkLoginUi;
  const read = async () => {
    const state = await check(page);
    if (state?.challenge) throw failure("ACCESS_BLOCKED", "Tencent requested verification", "access");
    if (typeof state?.loggedIn !== "boolean") throw failure("ACCESS_STATE_UNVERIFIED", "Login state could not be verified", "access");
    return state.loggedIn;
  };
  if (await read()) return;
  if (!options.manualLogin) throw failure("LOGIN_REQUIRED", "Use normal manual login to continue", "access");
  await (dependencies.prepareLogin ?? prepareManualLogin)(page, options, progress);
  const now = dependencies.now ?? Date.now;
  const sleep = dependencies.sleep ?? ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  const deadline = now() + Math.min(options.loginTimeoutMs ?? 900000, 900000);
  while (now() < deadline) {
    await sleep(Math.min(1000, Math.max(1, deadline - now())));
    if (await read()) return;
  }
  throw failure("LOGIN_REQUIRED", "Manual login did not complete within fifteen minutes", "access");
}

async function parseTarget(page, inputUrl, timeoutMs = 45000) {
  if (new URL(page.url()).origin !== "https://yuanbao.tencent.com") {
    throw failure("LOGIN_REQUIRED", "The owned page is not the normal Tencent Yuanbao session", "access");
  }
  const body = { type: "video_channel_url", url: inputUrl, scene: 1 };
  const response = await page.evaluate(async (payload) => {
    if (location.origin !== "https://yuanbao.tencent.com") throw new Error("Unexpected parse page origin");
    const reply = await fetch("https://yuanbao.tencent.com/api/weixin/get_parse_result", { method: "POST",
      mode: "same-origin", credentials: "same-origin", redirect: "error",
      headers: { "content-type": "application/json" }, body: JSON.stringify(payload.body), signal: AbortSignal.timeout(payload.timeoutMs) });
    return { status: reply.status, url: reply.url, redirected: reply.redirected,
      headers: { "intercept-result": reply.headers.get("intercept-result") ?? "",
        "x-risk-control": reply.headers.get("x-risk-control") ?? "" },
      body: await reply.json() };
  }, { body, timeoutMs }).catch(() => { throw failure("PLATFORM_PARSE_ERROR", "Tencent parse request did not complete", "platform"); });
  return { request: { url: PARSE_ENDPOINT, method: "POST", body }, response };
}

export async function observeTargetFeed(page, play, options) {
  let resolve, reject, claimed = false;
  const completion = new Promise((yes, no) => { resolve = yes; reject = no; });
  // Attach a rejection handler before navigation to avoid an unhandled rejection during goto.
  completion.catch(() => {});
  const timer = setTimeout(() => reject(failure("TARGET_FEED_MISSING", "No exact target feed response arrived", "platform")), options.feedTimeoutMs ?? 25000);
  const onResponse = (response) => {
    if (claimed || !endpointMatches(response.url(), FEED_ENDPOINT)) return;
    const request = response.request();
    let body;
    try { body = request.postDataJSON(); } catch { return; }
    if (request.method() !== "POST" || body?.exportId !== play.eid || body?.baseReq?.generalToken !== play.token) return;
    claimed = true;
    Promise.resolve().then(async () => ({ request: { url: response.url(), method: request.method(), body },
      response: { status: response.status(), url: response.url(), headers: response.headers(), body: await response.json() } }))
      .then(resolve, () => reject(failure("INVALID_PLATFORM_RESPONSE", "The exact feed response could not be read", "platform")));
  };
  page.on("response", onResponse);
  try {
    await page.goto(play.url.href, { waitUntil: "domcontentloaded", timeout: options.pageTimeoutMs ?? 45000 });
    const final = new URL(page.url());
    if (final.origin !== play.url.origin || final.pathname !== PLAY_PATH
        || exactParameter(final, "eid") !== play.eid || exactParameter(final, "token") !== play.token) {
      throw failure("IDENTITY_UNVERIFIED", "The target playback page changed identity during navigation");
    }
    const captured = await completion;
    const current = validatePlayableUrl(page.url());
    if (current.eid !== play.eid || current.token !== play.token) throw failure("IDENTITY_UNVERIFIED", "Target identity changed while waiting for feed metadata");
    return captured;
  } finally { clearTimeout(timer); page.off("response", onResponse); }
}

function parsedDownload(chain, userAgent) {
  const stream = { url: chain.streams[0], type: "video+audio", format: "mp4", referer: "https://channels.weixin.qq.com/",
    ...(userAgent ? { headers: { "User-Agent": userAgent } } : {}) };
  return { platform: "视频号", sourceUrl: chain.inputUrl, canonicalUrl: chain.canonicalUrl,
    // Raw export IDs are never used as filenames; this identifier is exclusively a local cache key.
    videoId: "wechat-" + createHash("sha256").update(chain.eid).digest("hex").slice(0, 24),
    title: chain.caption, author: { ...chain.author }, description: chain.caption, statistics: {},
    mediaStreams: [stream], availableStreams: [stream], mediaAlternatives: [[stream]] };
}

export async function downloadTargetMedia(parsed, output, timeoutMs = 900000, _ffmpegPath = null, options = {}) {
  const fetchImpl = options.fetch ?? globalThis.fetch;
  const maximum = Math.min(options.maxBytes ?? 1024 ** 3, 1024 ** 3);
  if (!Number.isSafeInteger(maximum) || maximum < 1) throw failure("INVALID_DOWNLOAD_LIMIT", "Invalid media size limit", "media");
  let current = mediaUrl(parsed.mediaStreams?.[0]?.url);
  if (!current) throw failure("TARGET_MEDIA_MISSING", "No bound video URL is available");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Math.min(timeoutMs, 900000));
  const partPath = path.join(output, `.wechat-video-${randomUUID()}.part`);
  const finalPath = path.join(output, "wechat-video.mp4");
  let handle, ownedPart = false;
  try {
    let response;
    for (let hop = 0; hop <= 5; hop++) {
      response = await fetchImpl(current, { redirect: "manual", signal: controller.signal,
        headers: { Referer: "https://channels.weixin.qq.com/", Accept: "video/mp4,video/*;q=0.9,application/octet-stream;q=0.8",
          ...(parsed.mediaStreams[0].headers?.["User-Agent"] ? { "User-Agent": parsed.mediaStreams[0].headers["User-Agent"] } : {}) } });
      if (![301, 302, 303, 307, 308].includes(response.status)) break;
      const location = response.headers.get("location");
      await Promise.resolve(response.body?.cancel?.()).catch(() => {});
      if (hop === 5 || !location || /\s|[\u0000-\u001f\u007f]/u.test(location)) throw failure("MEDIA_REDIRECT_REJECTED", "Media redirect is invalid or exceeded its bound", "access");
      const next = new URL(location, current).href;
      mediaUrl(next); // Reject an external/downgraded hop before making any request to it.
      current = next;
    }
    if (!Number.isInteger(response.status) || response.status < 200 || response.status >= 300 || !response.body) throw failure("MEDIA_HTTP_ERROR", "Target media did not return a usable response", "media");
    if (response.redirected === true || (response.url && new URL(response.url).href !== new URL(current).href)) throw failure("MEDIA_REDIRECT_REJECTED", "Unexpected media response location", "access");
    const contentType = (response.headers.get("content-type") ?? "").toLowerCase();
    if (/^(?:image\/|text\/)|json|xml/.test(contentType)) throw failure("INVALID_MEDIA_RESPONSE", "Target media returned an image or document", "media");
    const lengthText = response.headers.get("content-length");
    const expected = lengthText && /^\d+$/.test(lengthText) ? Number(lengthText) : null;
    if (expected != null && (!Number.isSafeInteger(expected) || expected > maximum)) throw failure("MEDIA_SIZE_LIMIT", "Target media exceeds the bounded size", "media");
    handle = await fs.open(partPath, "wx"); ownedPart = true;
    let bytes = 0;
    for await (const value of response.body) {
      const chunk = Buffer.from(value); bytes += chunk.length;
      if (bytes > maximum) { controller.abort(); throw failure("MEDIA_SIZE_LIMIT", "Target media exceeds the bounded size", "media"); }
      let offset = 0;
      while (offset < chunk.length) {
        const written = await handle.write(chunk, offset, chunk.length - offset);
        if (!written.bytesWritten) throw failure("MEDIA_WRITE_FAILED", "Media file could not be written", "output");
        offset += written.bytesWritten;
      }
    }
    if (!bytes || (expected != null && bytes !== expected)) throw failure("MEDIA_INCOMPLETE", "Target media is empty or incomplete", "media");
    await handle.close(); handle = null;
    await fs.link(partPath, finalPath); // Atomic creation, EEXIST preserves any concurrent writer's file.
    await fs.unlink(partPath); ownedPart = false;
    return { filePath: path.resolve(finalPath), bytes, mediaHasAudio: null };
  } finally {
    clearTimeout(timer); controller.abort(); await handle?.close().catch(() => {});
    if (ownedPart) await fs.unlink(partPath).catch(() => {});
  }
}

export async function acquire(options, dependencies = {}) {
  validateInputUrl(options.url);
  if (!options.allowTencentParse) throw failure("TENCENT_PARSE_NOT_ALLOWED", "Explicit Tencent online-parser authorization is required", "access");
  const budget = options.timeoutMs ?? 1800000;
  if (!Number.isSafeInteger(budget) || budget < 1 || budget > 2147483647) throw failure("INVALID_ARGUMENT", "Invalid acquisition time budget");
  const now = dependencies.now ?? Date.now;
  // Reserve cleanup time before the outer process deadline; Python owns the hard process-tree bound.
  const deadline = now() + budget - Math.min(2000, Math.floor(budget / 10));
  const limits = () => {
    const remaining = Math.floor(deadline - now());
    if (remaining < 1) throw failure("ACQUISITION_TIMEOUT", "Acquisition exhausted its total time budget", "platform");
    return { ...options, pageTimeoutMs: Math.min(options.pageTimeoutMs ?? 45000, remaining),
      feedTimeoutMs: Math.min(options.feedTimeoutMs ?? 25000, remaining),
      loginTimeoutMs: Math.min(options.loginTimeoutMs ?? 900000, remaining),
      downloadTimeoutMs: Math.min(options.downloadTimeoutMs ?? 900000, remaining) };
  };
  let manager = dependencies.browserManager;
  let context = dependencies.context ?? manager?.context;
  let createdContext = false;
  const progress = dependencies.onProgress ?? ((value) => process.stdout.write(`${JSON.stringify(value)}\n`));
  if (!manager) {
    if (options.browser) await fs.access(options.browser);
    const { BrowserManager } = await import("../vendor/video-batch-download/scripts/utils/browser-manager.js");
    manager = new BrowserManager(options.headed, options.browser);
  }
  try {
    if (!context) {
      if (options.storageState) await fs.access(options.storageState);
      // The shared owned manager emits a startup message; the CLI emits only structured status/path lines.
      const previousLog = console.log;
      let browser;
      try { console.log = () => {}; browser = await manager.start(); } finally { console.log = previousLog; }
      context = await browser.newContext({ locale: "zh-CN", viewport: { width: 1280, height: 800 },
        ...(manager.getUserAgent?.() ? { userAgent: manager.getUserAgent() } : {}),
        ...(options.storageState ? { storageState: options.storageState } : {}) });
      createdContext = true;
    }
    const page = dependencies.yuanbaoPage ?? await context.newPage();
    if (dependencies.yuanbaoPage && typeof page.context === "function" && page.context() !== context) {
      throw failure("IDENTITY_UNVERIFIED", "Injected login page is not part of the owned context");
    }
    if (!dependencies.yuanbaoPage) await page.goto("https://yuanbao.tencent.com/", { waitUntil: "domcontentloaded", timeout: limits().pageTimeoutMs });
    await requireLogin(page, limits(), dependencies, progress);
    const parsed = await parseTarget(page, options.url, limits().pageTimeoutMs);
    checkHttp(parsed.response, "Tencent parse", PARSE_ENDPOINT);
    if (parsed.response.body.code !== 0) {
      throw failure(/登录|login|auth/i.test(String(parsed.response.body.msg ?? "")) ? "LOGIN_REQUIRED" : "PLATFORM_PARSE_ERROR", "Tencent did not authorize or resolve this target", "access");
    }
    const play = validatePlayableUrl(parsed.response.body.data?.playable_url);
    const feed = await observeTargetFeed(await context.newPage(), play, limits());
    const chain = validateTencentChain({ inputUrl: options.url, parseRequest: parsed.request, parseResponse: parsed.response,
      feedRequest: feed.request, feedResponse: feed.response });
    buildProviderResult(options, chain);
    let downloaded = null;
    if (options.download) {
      const download = dependencies.downloadMedia ?? downloadTargetMedia;
      downloaded = await download(parsedDownload(chain, manager.getUserAgent?.()), options.output, limits().downloadTimeoutMs,
        options.ffmpegPath ?? null, { requireAudio: false, fetch: dependencies.fetch, maxBytes: options.maxMediaBytes });
      if (!downloaded?.filePath) throw failure("INVALID_DOWNLOAD_RESULT", "Download produced no local media", "media");
      const local = outputMediaPath(options.output, downloaded.filePath);
      const stat = await fs.stat(local);
      if (!stat.isFile() || stat.size === 0) throw failure("INVALID_DOWNLOAD_RESULT", "Download produced no nonempty file", "media");
    }
    limits();
    return buildProviderResult(options, chain, downloaded);
  } finally {
    if (createdContext) await context?.close().catch(() => {});
    await manager.close();
  }
}

export async function main(args = process.argv.slice(2), dependencies = {}) {
  let options;
  try { options = parseArgs(args); } catch {
    process.stderr.write(`${JSON.stringify({ status: "blocked", error_code: "INVALID_ARGUMENT" })}\n`); return 2;
  }
  if (options.help) { process.stdout.write(`${HELP}\n`); return 0; }
  const lockPath = path.join(options.output, ".wechat-browser-acquire.lock"), lockToken = randomUUID();
  let owned = false;
  try {
    await fs.mkdir(options.output, { recursive: true });
    const stat = await fs.lstat(options.output);
    if (!stat.isDirectory() || stat.isSymbolicLink() || (await fs.readdir(options.output)).length) throw new Error("output");
    await fs.writeFile(lockPath, lockToken, { encoding: "utf8", flag: "wx" });
    owned = true;
  } catch {
    process.stderr.write(`${JSON.stringify({ status: "blocked", error_code: "OUTPUT_EXISTS" })}\n`); return 2;
  }
  try {
    let result, code = 0;
    try { result = await acquire(options, dependencies); } catch (error) {
      code = 1;
      const errorCode = error.code === "ERR_MODULE_NOT_FOUND" ? "RUNTIME_MISSING" : error.code ?? "ACQUISITION_FAILED";
      result = { schema_version: 1, provider: "tencent-same-origin-browser", platform: "wechat_channels",
        status: ["access", "identity", "output"].includes(error.category) ? "blocked" : "failed",
        input_url: options.url, canonical_url: null, native_id: null, title: "", title_kind: "missing",
        identity_verified: false, identity_stability_verified: false, media_path: null, final_ready: false,
        error_code: errorCode, message: "Tencent browser acquisition did not establish a usable target; see the error code" };
    }
    const reportPath = path.join(options.output, "provider-result.json");
    await fs.writeFile(reportPath, `${JSON.stringify(result, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
    process.stdout.write(`${JSON.stringify({ status: result.status, result_path: reportPath })}\n`);
    return code;
  } finally {
    if (owned && await fs.readFile(lockPath, "utf8").catch(() => null) === lockToken) await fs.unlink(lockPath).catch(() => {});
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  main().then((code) => { process.exitCode = code; }).catch(() => {
    process.stderr.write(`${JSON.stringify({ status: "failed", error_code: "OUTPUT_WRITE_FAILED" })}\n`);
    process.exitCode = 1;
  });
}
