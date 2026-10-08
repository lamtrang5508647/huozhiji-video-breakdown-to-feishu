import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { EventEmitter } from "node:events";
import { PARSE_ENDPOINT, FEED_ENDPOINT, parseArgs, validateTencentChain, buildProviderResult,
  observeTargetFeed, downloadTargetMedia, acquire, main } from "../skill/video-link-extractor/scripts/wechat_browser_acquire.mjs";

const input = "https://weixin.qq.com/sph/SyntheticShare123";
const eid = "SyntheticExport_A1", token = "synthetic-private-token";
const video = "https://finder.video.qq.com/251/20304/stodownload?encfilekey=synthetic&token=media-private&X-snsvideoflag=1";
const playable = `https://channels.weixin.qq.com/finder-preview/pages/feed?eid=${eid}&token=${token}`;
function fixture() {
  return { inputUrl: input, parseRequest: { url: PARSE_ENDPOINT, method: "POST", body: { type: "video_channel_url", url: input, scene: 1 } },
    parseResponse: { status: 200, url: PARSE_ENDPOINT, redirected: false, body: { code: 0, data: { wx_export_id: "DifferentPlatformAlias", playable_url: playable } } },
    feedRequest: { url: FEED_ENDPOINT + "?_rid=synthetic", method: "POST", body: { exportId: eid, baseReq: { generalToken: token } } },
    feedResponse: { status: 201, body: { errCode: 0, data: { errMsg: { type: 0 }, sceneInfo: { dynamicExportId: eid },
      authorInfo: { nickname: "Synthetic author" }, feedInfo: { description: "Synthetic source caption", videoUrl: video } } } } };
}
const options = (output) => ({ url: input, output, allowTencentParse: true, manualLogin: false, headed: false,
  pageTimeoutMs: 100, feedTimeoutMs: 50, loginTimeoutMs: 3000, downloadTimeoutMs: 100 });
async function temporary(action) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "wechat-contract-"));
  try { return await action(directory); } finally { await fs.rm(directory, { recursive: true, force: true }); }
}
function responseFor(record) {
  return { url: () => record.feedRequest.url, request: () => ({ method: () => record.feedRequest.method,
    postDataJSON: () => record.feedRequest.body }), status: () => record.feedResponse.status,
    headers: () => record.feedResponse.headers ?? {}, json: async () => record.feedResponse.body };
}
function fakeSession(record = fixture(), { loggedIn = true, extraResponse, finalUrl } = {}) {
  let closed = 0, parsed = 0, started = 0;
  const feedPage = new EventEmitter();
  feedPage.url = () => finalUrl ?? playable;
  feedPage.goto = async () => { if (extraResponse) feedPage.emit("response", responseFor(extraResponse)); feedPage.emit("response", responseFor(record)); };
  const context = { newPage: async () => feedPage, close: async () => { closed++; } };
  const page = { context: () => context, url: () => "https://yuanbao.tencent.com/", evaluate: async (_, payload) => {
    parsed++; assert.deepEqual(payload.body, record.parseRequest.body); assert.ok(payload.timeoutMs > 0); return record.parseResponse;
  } };
  const browserManager = { start: async () => { started++; throw new Error("Injected context must be reused"); }, close: async () => { closed++; } };
  return { dependencies: { browserManager, context, yuanbaoPage: page, checkLoginState: async () => ({ loggedIn, challenge: false }), onProgress: () => {} },
    count: () => ({ closed, parsed, started }), feedPage };
}

test("request chain accepts HTTP 201 and a different parse alias without inventing native numeric identity", () => {
  const chain = validateTencentChain(fixture());
  const result = buildProviderResult(options("C:/synthetic-output"), chain);
  assert.equal(result.native_id, eid); assert.equal(result.native_id_kind, "platform_export_id");
  assert.equal(result.platform_export_id, eid); assert.equal(result.identity_stability_verified, false);
  assert.equal(result.title, "Synthetic source caption"); assert.equal(result.title_kind, "caption");
  assert.equal(result.identity_evidence.play_token_matched, true); assert.equal(result.final_ready, false);
  assert.equal(result.canonical_url, playable); assert.equal(result.identity_evidence.parse_playable_url, playable);
  assert.equal(result.identity_evidence.parse_http_status, 200); assert.equal(result.identity_evidence.feed_http_status, 201);
  assert.equal(result.identity_evidence.parse_code, 0); assert.equal(result.identity_evidence.feed_err_code, 0);
  assert.equal(result.identity_evidence.business_error_type, 0); assert.equal(JSON.stringify(result).includes("media-private"), false);
  assert.equal(chain.streams[0], video);
});

test("only strict single sph inputs and explicit nonduplicated flags are accepted", () => {
  for (const bad of ["http://weixin.qq.com/sph/Test", "https://weixin.qq.com.evil/sph/Test", input + "?x=1",
    "https://user@weixin.qq.com/sph/Test", input + "#x", input + " " + input, "https://weixin.qq.com/sph/../Test"]) {
    assert.throws(() => parseArgs(["--url", bad, "--output", "."]));
  }
  assert.throws(() => parseArgs(["--url", input, "--url", input, "--output", "."]));
  assert.equal(parseArgs(["--url", input, "--output", ".", "--allow-tencent-parse"]).download, false);
  assert.equal(parseArgs(["--url", input, "--output", ".", "--manual-login"]).headed, true);
  assert.equal(parseArgs(["--url", input, "--output", ".", "--ffmpeg", "ffmpeg"]).ffmpegPath, "ffmpeg");
  assert.equal(parseArgs(["--url", input, "--output", ".", "--timeout-ms", "1200000"]).timeoutMs, 1200000);
  for (const bad of ["0", "-1", "1.5", "1e3", "true", "2147483648"]) assert.throws(() => parseArgs(["--url", input, "--output", ".", "--timeout-ms", bad]));
});

test("all three export fields and the actual parse input must match exactly", () => {
  for (const alter of [
    (f) => { f.parseRequest.body.url = input + "Other"; },
    (f) => { f.parseRequest.body.type = "other"; },
    (f) => { f.feedRequest.body.exportId += "Other"; },
    (f) => { f.feedRequest.body.baseReq.generalToken += "Other"; },
    (f) => { f.feedResponse.body.data.sceneInfo.dynamicExportId += "Other"; },
    (f) => { f.feedRequest.url = "https://channels.weixin.qq.com.evil/finder-preview/api/feed/get_feed_info"; }
  ]) { const value = fixture(); alter(value); assert.throws(() => validateTencentChain(value), { code: "IDENTITY_UNVERIFIED" }); }
});

test("playback URL rejects foreign origins, duplicate credentials, missing token and wrong page", () => {
  for (const url of [playable + "&eid=Other", playable + "&token=Other", playable.replace("channels.weixin.qq.com", "evil.example"),
    playable.replace("/pages/feed", "/pages/sph"), `https://channels.weixin.qq.com/finder-preview/pages/feed?eid=${eid}`, " " + playable, playable + "\r\n"]) {
    const value = fixture(); value.parseResponse.body.data.playable_url = url;
    assert.throws(() => validateTencentChain(value), { code: "IDENTITY_UNVERIFIED" });
  }
});

test("HTTP success alone cannot pass business errors, risk headers or crypto flags", () => {
  for (const alter of [
    (f) => { f.parseResponse.body.code = 1; }, (f) => { f.feedResponse.body.errCode = 1; },
    (f) => { f.parseResponse.status = true; }, (f) => { f.parseResponse.body.code = false; },
    (f) => { f.feedResponse.body.errCode = false; }, (f) => { f.feedResponse.body.data.errMsg.type = false; },
    (f) => { f.parseResponse.redirected = true; },
    (f) => { f.parseResponse.url = "https://evil.example/api/weixin/get_parse_result"; },
    (f) => { f.feedResponse.body.data.errMsg.type = 2; }, (f) => { f.feedResponse.status = 403; },
    (f) => { f.feedResponse.headers = { "intercept-result": "risk-control" }; },
    (f) => { f.feedResponse.body.data.feedInfo.encryptType = 1; },
    (f) => { f.feedResponse.body.data.feedInfo.h264VideoInfo = { videoUrl: video, decodeKey: "secret" }; }
  ]) { const value = fixture(); alter(value); assert.throws(() => validateTencentChain(value)); }
});

test("cover, bgm, recommendations and blank captions cannot substitute for target video", () => {
  const cover = fixture(); cover.feedResponse.body.data.feedInfo = { description: "Real caption", coverUrl: video,
    bgmInfo: { videoUrl: video }, recommended: { videoUrl: video } };
  assert.throws(() => validateTencentChain(cover), { code: "TARGET_MEDIA_MISSING" });
  const blank = fixture(); blank.feedResponse.body.data.feedInfo.description = " ";
  assert.throws(() => validateTencentChain(blank), { code: "METADATA_MISSING" });
  const foreign = fixture(); foreign.feedResponse.body.data.feedInfo.videoUrl = "https://finder.video.qq.com.evil/video.mp4";
  assert.throws(() => validateTencentChain(foreign), { code: "INVALID_MEDIA_URL" });
});

test("a fabricated or mismatched receipt and download path are rejected", () => {
  assert.throws(() => buildProviderResult(options("C:/out"), {}), { code: "IDENTITY_UNVERIFIED" });
  const chain = validateTencentChain(fixture());
  assert.throws(() => buildProviderResult({ ...options("C:/out"), url: input + "Other" }, chain));
  assert.throws(() => buildProviderResult(options(path.resolve("out")), chain, { filePath: path.resolve("elsewhere/video.mp4") }), { code: "INVALID_DOWNLOAD_RESULT" });
});

test("an unrelated response is ignored; the exact naturally requested target is accepted", async () => {
  const unrelated = fixture(); unrelated.feedRequest.body.exportId = "RecommendedOther";
  const session = fakeSession(fixture(), { extraResponse: unrelated });
  const result = await acquire(options(path.resolve("out")), session.dependencies);
  assert.equal(result.status, "metadata_ready"); assert.deepEqual(session.count(), { closed: 1, parsed: 1, started: 0 });
  assert.equal(session.feedPage.listenerCount("response"), 0);
});

test("a mismatched feed response does not trigger parse retry or become a receipt", async () => {
  const wrong = fixture(); wrong.feedRequest.body.baseReq.generalToken = "Other";
  const session = fakeSession(wrong);
  await assert.rejects(acquire({ ...options(path.resolve("out")), feedTimeoutMs: 5 }, session.dependencies), { code: "TARGET_FEED_MISSING" });
  assert.equal(session.count().parsed, 1); assert.equal(session.count().closed, 1);
});

test("a target navigation cannot change to login or a different export identity", async () => {
  const session = fakeSession(fixture(), { finalUrl: "https://channels.weixin.qq.com/login" });
  await assert.rejects(acquire(options(path.resolve("out")), session.dependencies), { code: "IDENTITY_UNVERIFIED" });
});

test("without explicit online parser permission no browser or API is used", async () => {
  const session = fakeSession();
  await assert.rejects(acquire({ ...options(path.resolve("out")), allowTencentParse: false }, session.dependencies), { code: "TENCENT_PARSE_NOT_ALLOWED" });
  assert.equal(session.count().parsed, 0);
});

test("actual same-origin parse preserves risk headers and never follows redirects", async () => {
  const session = fakeSession();
  const previousFetch = globalThis.fetch, previousLocation = globalThis.location;
  let calls = 0, feedVisits = 0;
  session.feedPage.goto = async () => { feedVisits++; };
  session.dependencies.yuanbaoPage.evaluate = async (execute, body) => {
    globalThis.location = { origin: "https://yuanbao.tencent.com" };
    globalThis.fetch = async (url, request) => {
      calls++; assert.equal(url, PARSE_ENDPOINT); assert.equal(request.mode, "same-origin");
      assert.equal(request.credentials, "same-origin"); assert.equal(request.redirect, "error");
      assert.deepEqual(JSON.parse(request.body), fixture().parseRequest.body);
      return { status: 200, url: PARSE_ENDPOINT, redirected: false,
        headers: new Headers({ "x-risk-control": "1" }), json: async () => fixture().parseResponse.body };
    };
    try { return await execute(body); } finally { globalThis.fetch = previousFetch; globalThis.location = previousLocation; }
  };
  await assert.rejects(acquire(options(path.resolve("out")), session.dependencies), { code: "ACCESS_BLOCKED" });
  assert.equal(calls, 1); assert.equal(feedVisits, 0);
});

test("missing login without manual-login neither opens QR nor submits target", async () => {
  const session = fakeSession(fixture(), { loggedIn: false });
  let prepared = 0; session.dependencies.prepareLogin = async () => { prepared++; };
  await assert.rejects(acquire(options(path.resolve("out")), session.dependencies), { code: "LOGIN_REQUIRED" });
  assert.equal(prepared, 0); assert.equal(session.count().parsed, 0); assert.equal(session.count().closed, 1);
});

test("QR is only progress; a manual login timeout never reports metadata success", async () => {
  const session = fakeSession(fixture(), { loggedIn: false });
  let clock = 0, prepared = 0;
  Object.assign(session.dependencies, { now: () => clock, sleep: async () => { clock += 1000; }, prepareLogin: async () => { prepared++; } });
  await assert.rejects(acquire({ ...options(path.resolve("out")), manualLogin: true, loginTimeoutMs: 2000 }, session.dependencies), { code: "LOGIN_REQUIRED" });
  assert.equal(prepared, 1); assert.equal(session.count().parsed, 0);
});

test("a verification challenge during manual login stops before parse", async () => {
  const session = fakeSession(); session.dependencies.checkLoginState = async () => ({ loggedIn: false, challenge: true });
  await assert.rejects(acquire({ ...options(path.resolve("out")), manualLogin: true }, session.dependencies), { code: "ACCESS_BLOCKED" });
  assert.equal(session.count().parsed, 0);
});

test("total budget caps manual login and blocks further remote work after elapsed time", async () => {
  const pending = fakeSession(fixture(), { loggedIn: false });
  let clock = 0;
  Object.assign(pending.dependencies, { now: () => clock, sleep: async (ms) => { clock += ms; }, prepareLogin: async () => {} });
  await assert.rejects(acquire({ ...options(path.resolve("out")), manualLogin: true, timeoutMs: 100 }, pending.dependencies), { code: "LOGIN_REQUIRED" });
  assert.equal(clock, 90); assert.equal(pending.count().parsed, 0);
  const expired = fakeSession(); let visits = 0;
  expired.feedPage.goto = async () => { visits++; };
  clock = 0; expired.dependencies.now = () => clock;
  const evaluate = expired.dependencies.yuanbaoPage.evaluate;
  expired.dependencies.yuanbaoPage.evaluate = async (...args) => { const result = await evaluate(...args); clock = 100; return result; };
  await assert.rejects(acquire({ ...options(path.resolve("out")), timeoutMs: 100 }, expired.dependencies), { code: "ACQUISITION_TIMEOUT" });
  assert.equal(visits, 0); assert.equal(expired.count().closed, 1);
});

test("download occurs only after validation and receives a safe cache identifier", async () => temporary(async (directory) => {
  const session = fakeSession(); let calls = 0;
  session.dependencies.downloadMedia = async (parsed, output) => {
    calls++; assert.match(parsed.videoId, /^wechat-[a-f0-9]{24}$/); assert.notEqual(parsed.videoId, eid);
    assert.equal(parsed.mediaStreams[0].url, video);
    const filePath = path.join(output, "source.mp4"); await fs.writeFile(filePath, "synthetic media");
    return { filePath, bytes: 15, mediaHasAudio: true };
  };
  const result = await acquire({ ...options(directory), download: true }, session.dependencies);
  assert.equal(calls, 1); assert.equal(result.status, "downloaded"); assert.equal(result.final_ready, false);
  assert.equal(result.media_validation, "pending_external_hash_ffprobe_full_decode");
  const invalid = fixture(); invalid.feedResponse.body.data.sceneInfo.dynamicExportId = "Other";
  const rejected = fakeSession(invalid); rejected.dependencies.downloadMedia = session.dependencies.downloadMedia;
  await assert.rejects(acquire({ ...options(directory), download: true }, rejected.dependencies));
  assert.equal(calls, 1);
}));

test("CLI preserves existing receipts and originals before any acquisition", async () => temporary(async (directory) => {
  for (const name of ["provider-result.json", "original.mp4"]) {
    const output = path.join(directory, name); await fs.mkdir(output); await fs.writeFile(path.join(output, name), "keep-original");
    const session = fakeSession();
    assert.equal(await main(["--url", input, "--output", output, "--allow-tencent-parse", "--download"], session.dependencies), 2);
    assert.equal(await fs.readFile(path.join(output, name), "utf8"), "keep-original"); assert.equal(session.count().parsed, 0);
  }
}));

test("CLI accepts empty output and keeps full playback URL exclusively in the private receipt", async () => temporary(async (directory) => {
  const session = fakeSession();
  const chunks = [], previous = process.stdout.write;
  try {
    process.stdout.write = (chunk) => { chunks.push(String(chunk)); return true; };
    assert.equal(await main(["--url", input, "--output", directory, "--allow-tencent-parse"], session.dependencies), 0);
  } finally { process.stdout.write = previous; }
  const text = await fs.readFile(path.join(directory, "provider-result.json"), "utf8");
  assert.equal(JSON.parse(text).status, "metadata_ready"); assert.equal(JSON.parse(text).canonical_url, playable);
  assert.equal(chunks.join("").includes(token), false); assert.equal(chunks.join("").includes("media-private"), false);
  assert.deepEqual(await fs.readdir(directory), ["provider-result.json"]);
}));

test("a receipt arriving during acquisition is never overwritten", async () => temporary(async (directory) => {
  const session = fakeSession();
  session.dependencies.checkLoginState = async () => {
    await fs.writeFile(path.join(directory, "provider-result.json"), "another-writer", { flag: "wx" });
    return { loggedIn: true, challenge: false };
  };
  await assert.rejects(main(["--url", input, "--output", directory, "--allow-tencent-parse"], session.dependencies), { code: "EEXIST" });
  assert.equal(await fs.readFile(path.join(directory, "provider-result.json"), "utf8"), "another-writer");
}));

test("source contains no Cookie/state export or fixed authentication header identities", async () => {
  const source = await fs.readFile(new URL("../skill/video-link-extractor/scripts/wechat_browser_acquire.mjs", import.meta.url), "utf8");
  for (const pattern of [/\.cookies\s*\(/, /\.storageState\s*\(/, /document\.cookie/, /context\.request/, /x-webdriver/i, /x-device-id/i]) assert.doesNotMatch(source, pattern);
});

test("built-in download refuses external redirects before requesting the foreign host", async () => temporary(async (directory) => {
  const requested = [];
  const fetch = async (url) => { requested.push(url); return new Response(null, { status: 302, headers: { location: "https://evil.example/video.mp4" } }); };
  await assert.rejects(downloadTargetMedia({ mediaStreams: [{ url: video }] }, directory, 100, null, { fetch }), { code: "INVALID_MEDIA_URL" });
  assert.deepEqual(requested, [video]); assert.deepEqual(await fs.readdir(directory), []);
}));

test("built-in download rejects hidden redirects and changed signed response URLs", async () => temporary(async (directory) => {
  for (const fields of [{ redirected: true }, { url: video + "&token=other" }, { status: undefined }]) {
    const response = new Response("synthetic media", { status: 200, headers: { "content-type": "video/mp4" } });
    for (const [key, value] of Object.entries(fields)) Object.defineProperty(response, key, { value });
    await assert.rejects(downloadTargetMedia({ mediaStreams: [{ url: video }] }, directory, 100, null,
      { fetch: async () => response }));
    assert.deepEqual(await fs.readdir(directory), []);
  }
}));

test("built-in download validates each CDN hop, writes a fixed exclusive name and caps bytes", async () => temporary(async (directory) => {
  let calls = 0;
  const fetch = async (_url, request) => {
    assert.equal(request.redirect, "manual"); calls++;
    if (calls === 1) return new Response(null, { status: 302, headers: { location: "/synthetic/video.mp4?token=still-signed" } });
    return new Response(Buffer.from("synthetic media"), { status: 200, headers: { "content-type": "video/mp4", "content-length": "15" } });
  };
  const result = await downloadTargetMedia({ mediaStreams: [{ url: video }] }, directory, 100, null, { fetch });
  assert.equal(path.basename(result.filePath), "wechat-video.mp4"); assert.equal(result.bytes, 15); assert.equal(calls, 2);
  const original = await fs.readFile(result.filePath, "utf8");
  await assert.rejects(downloadTargetMedia({ mediaStreams: [{ url: video }] }, directory, 100, null,
    { fetch: async () => new Response("replacement", { headers: { "content-type": "video/mp4" } }) }), { code: "EEXIST" });
  assert.equal(await fs.readFile(result.filePath, "utf8"), original);
  await assert.rejects(downloadTargetMedia({ mediaStreams: [{ url: video }] }, directory, 100, null,
    { maxBytes: 2, fetch: async () => new Response("too long", { headers: { "content-type": "video/mp4" } }) }), { code: "MEDIA_SIZE_LIMIT" });
  assert.deepEqual(await fs.readdir(directory), ["wechat-video.mp4"]);
}));
