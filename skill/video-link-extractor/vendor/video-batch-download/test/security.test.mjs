import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { BilibiliParser } from "../scripts/platforms/bilibili.js";
import { DouyinParser } from "../scripts/platforms/douyin.js";
import { KuaishouParser } from "../scripts/platforms/kuaishou.js";
import { XiaohongshuParser } from "../scripts/platforms/xiaohongshu.js";
import { rejectAccessChallenge } from "../scripts/platforms/security.js";
import { validateParsedVideo } from "../scripts/platforms/base.js";
import { acquire, parseArgs, makeProviderResult, main } from "../../../scripts/browser_acquire.mjs";

const options = { pageTimeoutMs: 50, mediaWaitMs: 1, downloadTimeoutMs: 50 };
function response(url, json, status = 200, headers = {}) {
  return { url: () => url, json: async () => json, status: () => status, ok: () => status >= 200 && status < 300, headers: () => headers };
}
function fakeBrowser(finalUrl, responses = [], body = "", pageState = null) {
  const listeners = new Set();
  const page = {
    on: (_, listener) => listeners.add(listener), off: (_, listener) => listeners.delete(listener),
    goto: async () => { for (const item of responses) for (const listener of listeners) listener(item); await Promise.resolve(); await Promise.resolve(); },
    url: () => finalUrl, title: async () => { throw new Error("Unbound page title must not be used"); },
    locator: () => ({ innerText: async () => body }), evaluate: async (callback) => String(callback).includes("captcha_container") ? false : pageState,
  };
  let closed = false;
  const context = { newPage: async () => page, close: async () => { closed = true; } };
  const manager = { start: async () => ({ newContext: async () => context }), close: async () => { closed = true; }, getUserAgent: () => "Synthetic test" };
  return { manager, page, isClosed: () => closed };
}
function douyinDetail(id, desc = "Target description") {
  return { status_code: 0, aweme_detail: { aweme_id: id, desc, author: { nickname: "Fixture" }, video: { width: 320, height: 240, play_addr: { url_list: [`https://fixture.douyinvod.com/${id}.mp4`] } } } };
}
const douyinUrl = "https://www.douyin.com/video/123456789";
test("Douyin takes only exact target detail streams and its description", async () => {
  const fake = fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("999999999", "Recommended")), response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("123456789"))]);
  const parsed = await new DouyinParser().parse(fake.manager, douyinUrl, options);
  validateParsedVideo(parsed, "douyin");
  assert(parsed.availableStreams.every((stream) => stream.format === "mp4"));
  assert.equal(parsed.videoId, "123456789"); assert.equal(parsed.title, "Target description"); assert.equal(parsed.titleKind, "description");
  assert(parsed.mediaStreams.every((item) => item.url.includes("123456789")));
  assert(fake.isClosed());
});
test("Douyin rejects recommendation detail and arbitrary runtime video", async () => {
  const fake = fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("999999999")), response("https://fixture.douyinvod.com/recommend.mp4", {}, 200, { "content-type": "video/mp4" })]);
  const parser = new DouyinParser(); parser._collectRuntimeMediaCandidates = async () => [{ url: "https://fixture.douyinvod.com/recommend.mp4", type: "video+audio" }];
  await assert.rejects(parser.parse(fake.manager, douyinUrl, options), { code: "IDENTITY_UNVERIFIED" });
});
test("Douyin rejects changed target on redirect", async () => {
  const fake = fakeBrowser("https://www.douyin.com/video/999999999", [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("999999999"))]);
  await assert.rejects(new DouyinParser().parse(fake.manager, douyinUrl, options), { code: "IDENTITY_UNVERIFIED" });
});
test("Verification barrier blocks even when target media was observed", async () => {
  const fake = fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("123456789"))], "请完成安全验证");
  await assert.rejects(new DouyinParser().parse(fake.manager, douyinUrl, options), { code: "ACCESS_BLOCKED" });
});
test("Douyin normal comment/optional HD login UI allows only exact target public media", async () => {
  const body = "请先登录后发表评论 登录后免费畅享高清视频 扫码登录 验证码登录 密码登录 获取验证码";
  const fake = fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("123456789"))], body);
  const parsed = await new DouyinParser().parse(fake.manager, douyinUrl, options);
  assert.equal(parsed.videoId, "123456789"); assert.equal(parsed.title, "Target description");
  await assert.rejects(new DouyinParser().parse(fakeBrowser(douyinUrl, [], body).manager, douyinUrl, options), { code: "ACCESS_BLOCKED" });
  const unrelated = fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("999999999"))], body);
  await assert.rejects(new DouyinParser().parse(unrelated.manager, douyinUrl, options), { code: "ACCESS_BLOCKED" });
});
test("Douyin visible captcha, verify URL, risk header and real login wall still stop", async () => {
  const parser = new DouyinParser();
  for (const [body, url, flags] of [
    ["验证码登录", douyinUrl, { visibleChallenge: true }],
    ["", "https://www.douyin.com/verify", {}],
    ["", douyinUrl, { riskControlled: true }],
    ["请拖动滑块完成验证", douyinUrl, {}],
    ["登录后才能观看", douyinUrl, {}],
    ["请输入下图验证码", douyinUrl, {}],
    ["登录后查看视频", douyinUrl, {}],
  ]) assert.throws(() => parser._rejectAccessBarrier(body, url, { boundTargetMedia: true, ...flags }), { code: "ACCESS_BLOCKED" });
  const fake = fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("123456789"))], "验证码登录");
  parser._hasVisibleChallenge = async () => true;
  await assert.rejects(parser.parse(fake.manager, douyinUrl, options), { code: "ACCESS_BLOCKED" });
});
test("Douyin access inspection failures cannot exempt login UI", async () => {
  const parser = new DouyinParser();
  const responses = [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("123456789"))];
  const fake = fakeBrowser(douyinUrl, responses, "验证码登录");
  fake.page.evaluate = async () => { throw new Error("Fixture DOM unavailable"); };
  await assert.rejects(parser.parse(fake.manager, douyinUrl, options), { code: "ACCESS_STATE_UNVERIFIED" });
  const bodyFailure = fakeBrowser(douyinUrl, responses);
  bodyFailure.page.locator = () => ({ innerText: async () => { throw new Error("Fixture body unavailable"); } });
  await assert.rejects(parser.parse(bodyFailure.manager, douyinUrl, options), { code: "ACCESS_STATE_UNVERIFIED" });
});
test("Douyin current URL is rechecked after target detail observation", async () => {
  const parser = new DouyinParser();
  const fake = fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", douyinDetail("123456789"))], "验证码登录");
  let reads = 0;
  fake.page.url = () => ++reads > 1 ? "https://www.douyin.com/login" : douyinUrl;
  await assert.rejects(parser.parse(fake.manager, douyinUrl, options), { code: "ACCESS_BLOCKED" });
});
test("Douyin normal login UI exemption cannot use failed detail or cover-only media", async () => {
  const body = "请先登录后发表评论 验证码登录 获取验证码";
  const failedDetail = douyinDetail("123456789"); failedDetail.status_code = 1;
  await assert.rejects(new DouyinParser().parse(fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", failedDetail)], body).manager, douyinUrl, options), { code: "ACCESS_BLOCKED" });
  const coverDetail = douyinDetail("123456789"); coverDetail.aweme_detail.video = { cover: { url_list: ["https://fixture.douyinvod.com/poster.mp4"] } };
  await assert.rejects(new DouyinParser().parse(fakeBrowser(douyinUrl, [response("https://www.douyin.com/aweme/v1/web/aweme/detail/", coverDetail)], body).manager, douyinUrl, options), { code: "ACCESS_BLOCKED" });
});
const xhsId = "1234567890abcdef12345678";
const xhsUrl = `https://www.xiaohongshu.com/explore/${xhsId}`;
function xhsNote(id = xhsId) { return { id, type: "video", title: "Native target title", video: { url: `https://sns-video-bd.xhscdn.com/${id}.mp4` } }; }
test("Xiaohongshu binds note ID and rejects different feed cards", async () => {
  const parser = new XiaohongshuParser();
  assert.equal(parser._extractNoteFromApi([{ items: [{ note_card: xhsNote("wrong") }, { note_card: xhsNote() }] }], [], xhsId).id, xhsId);
  assert.equal(parser._extractNoteFromApi([{ items: [{ note_card: xhsNote() }] }], [], null), null);
});
test("Xiaohongshu never uses runtime streams when target has no bound media", () => {
  const parser = new XiaohongshuParser();
  assert.deepEqual(parser._resolveAvailableStreams({ id: xhsId, type: "video" }, [{ url: "https://sns-video-bd.xhscdn.com/recommend.mp4" }], xhsUrl, true), []);
  assert.deepEqual(parser._collectNoteMediaCandidates({ id: xhsId, type: "video", related: { video: { url: "https://sns-video-bd.xhscdn.com/recommend.mp4" } } }), []);
});
test("Xiaohongshu cover-only target metadata cannot become a playable original", async () => {
  const url = "https://sns-video-bd.xhscdn.com/synthetic-cover.jpg";
  for (const video of [{ coverUrl: url }, { cover: { url: url.replace(".jpg", ".mp4") } }]) {
    const parser = new XiaohongshuParser();
    const note = { id: xhsId, type: "video", title: "Synthetic target", video };
    assert.deepEqual(parser._collectNoteMediaCandidates(note), []);
    parser._extractNoteFromPage = async () => note;
    const fake = fakeBrowser(xhsUrl);
    await assert.rejects(parser.parse(fake.manager, xhsUrl, options), { code: "MEDIA_DISCOVERY_FAILED" });
    assert(fake.isClosed());
  }
});
test("Xiaohongshu nested recommendation and bgm videos never substitute for the target", async () => {
  const other = "https://sns-video-bd.xhscdn.com/synthetic-other.mp4";
  const note = { id: xhsId, type: "video", title: "Synthetic target", video: {
    recommended: { video: { media: { stream: { h264: [{ masterUrl: other }] } } } },
    related: [{ videoUrl: other }], bgmInfo: { url: other }, poster: { downloadUrl: other },
  } };
  const parser = new XiaohongshuParser();
  assert.deepEqual(parser._collectNoteMediaCandidates(note), []);
  assert.deepEqual(parser._resolveAvailableStreams(note, [{ url: other }], xhsUrl, true), []);
  parser._extractNoteFromPage = async () => note;
  await assert.rejects(parser.parse(fakeBrowser(xhsUrl).manager, xhsUrl, options), { code: "MEDIA_DISCOVERY_FAILED" });
});
test("Xiaohongshu preserves explicit target streams, origin keys and direct video URLs at the provider boundary", async () => {
  const target = "https://sns-video-hw.xhscdn.com/target-stream?token=synthetic";
  const origin = "https://sns-video-bd.xhscdn.com/synthetic-origin";
  const direct = "https://media.xhscdn.com/synthetic-direct.mp4?token=synthetic";
  const other = "https://sns-video-bd.xhscdn.com/recommended.mp4";
  const note = { id: xhsId, type: "video", title: "Synthetic native title", video: {
    media: { stream: { h264: [{ masterUrl: target, width: 1280, height: 720 }] } },
    consumer: { originVideoKey: "synthetic-origin" }, downloadUrl: direct,
    cover: { url: other }, recommended: { url: other }, bgm: { url: other },
  } };
  const parser = new XiaohongshuParser(); parser._extractNoteFromPage = async () => note;
  const parsed = validateParsedVideo(await parser.parse(fakeBrowser(xhsUrl).manager, xhsUrl, options), "xiaohongshu");
  assert.equal(parsed.mediaStreams[0].url, target);
  assert.deepEqual(new Set(parsed.availableStreams.map((stream) => stream.url)), new Set([target, origin, direct]));
  assert(parsed.availableStreams.every((stream) => stream.format === "mp4" && stream.type === "video+audio"));
  const receipt = makeProviderResult({ url: xhsUrl, platform: "xiaohongshu" }, parsed);
  assert.equal(receipt.native_id, xhsId); assert.equal(receipt.title, note.title); assert.equal(receipt.identity_verified, true);
  assert.equal(parser._extractVideoUrl({ video: { url: direct } }), direct);
  assert.equal(parser._extractVideoUrl({ video: { originVideoKey: origin } }), origin);
});
test("Xiaohongshu rejects HTTP, lookalike CDN hosts, image extensions and query-only video labels", () => {
  const parser = new XiaohongshuParser();
  const bad = [
    "http://sns-video-bd.xhscdn.com/target.mp4", "https://evilxhscdn.com/target.mp4",
    "https://xhscdn.com.evil.test/target.mp4", "https://evil.test/sns-video-bd.xhscdn.com/target.mp4",
    "https://user@sns-video-bd.xhscdn.com/target.mp4", "https://sns-video-bd.xhscdn.com:8443/target.mp4",
    "https://sns-video-bd.xhscdn.com/target.mp4#fragment", " https://sns-video-bd.xhscdn.com/target.mp4",
    "https://sns-video-bd.xhscdn.com/cover.jpg", "https://sns-video-bd.xhscdn.com/cover%2Epng",
    "https://sns-video-bd.xhscdn.com/cover.webp!format", "https://image.xhscdn.com/resource?label=sns-video",
    "https://sns-video-bd.xhscdn.com/playlist.m3u8", "https://sns-video-bd.xhscdn.com/chunk.ts",
  ];
  for (const url of bad) {
    assert.equal(parser._isVideoCdnUrl(url), false, url);
    assert.deepEqual(parser._collectNoteMediaCandidates({ video: { url } }), [], url);
    assert.deepEqual(parser._collectNoteMediaCandidates({ video: { media: { stream: { h264: [{ masterUrl: url }] } } } }), [], url);
  }
});
test("Xiaohongshu accepts exact native note and its own stream", async () => {
  const fake = fakeBrowser(xhsUrl);
  const parser = new XiaohongshuParser(); parser._extractNoteFromPage = async () => xhsNote();
  const parsed = await parser.parse(fake.manager, xhsUrl, options);
  assert.equal(parsed.videoId, xhsId); assert.equal(parsed.titleKind, "native_title"); assert(parsed.mediaStreams[0].url.includes(xhsId));
});
test("Xiaohongshu refuses unresolved short-link/native ID and missing bound streams", async () => {
  const short = "https://xhslink.cn/o/fixture";
  await assert.rejects(new XiaohongshuParser().parse(fakeBrowser(short).manager, short, options), { code: "IDENTITY_UNVERIFIED" });
  const parser = new XiaohongshuParser(); parser._extractNoteFromPage = async () => ({ id: xhsId, type: "video" }); parser._collectRuntimeMediaCandidates = async () => [{ url: "https://sns-video-bd.xhscdn.com/recommend.mp4" }];
  await assert.rejects(parser.parse(fakeBrowser(xhsUrl).manager, xhsUrl, options));
});
test("Xiaohongshu refuses a note with mismatched embedded native ID", async () => {
  const parser = new XiaohongshuParser(); parser._extractNoteFromPage = async () => xhsNote("wrong");
  await assert.rejects(parser.parse(fakeBrowser(xhsUrl).manager, xhsUrl, options), { code: "IDENTITY_UNVERIFIED" });
});
test("Kuaishou ID URL matching is exact, not a substring", () => {
  const parser = new KuaishouParser();
  assert.equal(parser._candidateMatchesVideoId("https://fixture.kwaicdn.com/12345.mp4", "123"), false);
  assert.equal(parser._candidateMatchesVideoId("https://fixture.kwaicdn.com/123.mp4", "123"), true);
});
test("Kuaishou rejects ID-bearing runtime media without target metadata", async () => {
  const url = "https://www.kuaishou.com/short-video/abc123";
  const fake = fakeBrowser(url, [response("https://fixture.kwaicdn.com/abc123.mp4", {}, 200, { "content-type": "video/mp4" })]);
  const parser = new KuaishouParser(); parser._extractDetailFromPage = async () => null;
  await assert.rejects(parser.parse(fake.manager, url, options), { code: "IDENTITY_UNVERIFIED" });
});
test("Bilibili binds BV/av metadata and fetches only that video's CID", async () => {
  const url = "https://www.bilibili.com/video/BV1fixture";
  const meta = { bvid: "BV1fixture", aid: 42, cid: 55, title: "Native Bilibili fixture" };
  const fake = fakeBrowser(url, [], "", meta); const parser = new BilibiliParser();
  parser._fetchJsonFromPage = async (_page, requestUrl) => { const request = new URL(requestUrl); assert.equal(request.searchParams.get("bvid"), meta.bvid); assert.equal(request.searchParams.get("cid"), "55"); return { code: 0, data: { durl: [{ url: "https://fixture.example.test/target.mp4" }] } }; };
  const parsed = await parser.parse(fake.manager, url, options);
  assert.equal(parsed.videoId, meta.bvid); assert.equal(parsed.title, meta.title); assert.equal(parsed.identityEvidence.cid, 55);
});
test("Bilibili rejects recommendation metadata with another native ID", async () => {
  const url = "https://www.bilibili.com/video/BV1fixture";
  await assert.rejects(new BilibiliParser().parse(fakeBrowser(url, [], "", { bvid: "BV1other", aid: 777, cid: 66, title: "Recommendation" }).manager, url, options), { code: "IDENTITY_UNVERIFIED" });
});
test("Bilibili preserves av/BV alias and exact requested part", async () => {
  const input = "https://www.bilibili.com/video/av42?p=2";
  const resolved = "https://www.bilibili.com/video/BV1fixture?p=2";
  const meta = { bvid: "BV1fixture", aid: 42, cid: 55, title: "Native fixture", pages: [{ page: 1, cid: 55 }, { page: 2, cid: 66 }] };
  const parser = new BilibiliParser(); parser._fetchJsonFromPage = async (_page, requestUrl) => { assert.equal(new URL(requestUrl).searchParams.get("cid"), "66"); return { code: 0, data: { durl: [{ url: "https://fixture.example.test/part2.mp4" }] } }; };
  const parsed = await parser.parse(fakeBrowser(resolved, [], "", meta).manager, input, options);
  assert.equal(parsed.videoId, "BV1fixture"); assert.equal(parsed.identityEvidence.aid, 42);
  const unredirected = await parser.parse(fakeBrowser(input, [], "", meta).manager, input, options);
  assert.equal(unredirected.videoId, "BV1fixture"); assert.equal(unredirected.canonicalUrl, "https://www.bilibili.com/video/BV1fixture?p=2"); assert.equal(unredirected.resolvedUrl, input); assert.equal(unredirected.identityEvidence.requested_native_id, "av42");
  await assert.rejects(parser.parse(fakeBrowser("https://www.bilibili.com/video/BV1fixture?p=1", [], "", meta).manager, input, options), { code: "IDENTITY_UNVERIFIED" });
});
test("Bilibili p rejects invalid/multiple values and nonexistent part instead of falling back", () => {
  const parser = new BilibiliParser();
  const prefix = "https://www.bilibili.com/video/BV1fixture";
  const metadata = { cid: 55, pages: [{ page: 1, cid: 55 }, { page: 2, cid: 66 }] };
  for (const query of ["?p=0", "?p=-1", "?p=2junk", "?p=1&p=2", "?p=", "?p=01", "?p=9007199254740993"]) assert.throws(() => parser._resolveCid(metadata, prefix + query), { code: "IDENTITY_UNVERIFIED" });
  assert.throws(() => parser._resolveCid(metadata, prefix + "?p=9"), { code: "IDENTITY_UNVERIFIED" });
  assert.throws(() => parser._resolveCid({ cid: 55 }, prefix + "?p=2"), { code: "IDENTITY_UNVERIFIED" });
  assert.throws(() => parser._resolveCid({ pages: [{ page: 2, cid: 66 }, { page: 2, cid: 77 }] }, prefix + "?p=2"), { code: "IDENTITY_UNVERIFIED" });
  assert.equal(parser._resolveCid(metadata, prefix + "?p=2"), 66);
});
test("Bilibili AV cannot guess a BV identity when actual metadata lacks it", async () => {
  const url = "https://www.bilibili.com/video/av42";
  await assert.rejects(new BilibiliParser().parse(fakeBrowser(url, [], "", { aid: 42, cid: 55, title: "Fixture" }).manager, url, options), { code: "METADATA_MISSING" });
});
test("Access policy blocks login redirect, risk header and captcha", () => {
  for (const [text, url, risk] of [["captcha", xhsUrl, false], ["", "https://www.xiaohongshu.com/login", false], ["", douyinUrl, true]]) assert.throws(() => rejectAccessChallenge(text, url, risk), { code: "ACCESS_BLOCKED" });
});
test("CLI defaults to metadata and rejects ambiguous/foreign platform input", () => {
  assert.equal(parseArgs(["--url", douyinUrl, "--platform", "douyin", "--output", "fixture"], {}).download, false);
  assert.throws(() => parseArgs(["--url", xhsUrl, "--platform", "douyin", "--output", "fixture"], {}));
  assert.throws(() => parseArgs(["--url", douyinUrl, "--platform", "douyin", "--output", "fixture", "--metadata-only", "--download"], {}));
});
test("Metadata mode cannot call downloader; download mode never claims final ready", async () => {
  const parsed = { platform: "抖音", sourceUrl: douyinUrl, canonicalUrl: douyinUrl, videoId: "123456789", title: "Fixture", titleKind: "description", author: {}, statistics: {}, mediaStreams: [{ url: "https://fixture.douyinvod.com/target.mp4", type: "video+audio", format: "mp4" }], identityEvidence: { expected_id: "123456789", observed_id: "123456789", kind: "target-detail" } };
  const manager = fakeBrowser(douyinUrl).manager;
  let downloads = 0; const dependencies = { browserManager: manager, parser: { parse: async () => parsed }, downloadMedia: async () => { downloads++; return { filePath: "fixture.mp4", bytes: 1234 }; } };
  const request = { ...options, url: douyinUrl, platform: "douyin", output: "fixture", download: false };
  const metadata = await acquire(request, dependencies); assert.equal(downloads, 0); assert.equal(metadata.status, "metadata_ready"); assert.equal(metadata.media_path, null); assert.equal(metadata.input_url, douyinUrl); assert.equal(metadata.title_kind, "caption");
  const downloaded = await acquire({ ...request, download: true }, dependencies); assert.equal(downloads, 1); assert.equal(downloaded.status, "downloaded"); assert.equal(downloaded.final_ready, false);
  assert.throws(() => makeProviderResult(request, { ...parsed, identityEvidence: null }), { code: "IDENTITY_UNVERIFIED" });
  assert.throws(() => makeProviderResult(request, { ...parsed, title: "" }), { code: "IDENTITY_UNVERIFIED" });
  assert.throws(() => makeProviderResult(request, { ...parsed, canonicalUrl: douyinUrl + "000" }), { code: "IDENTITY_UNVERIFIED" });
});
test("Direct Node CLI preserves a preexisting receipt and original media without acquiring", async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "browser-acquire-existing-"));
  try {
    const receipt = '{"original_receipt":true}\n'; const media = Buffer.from("original source fixture");
    await fs.writeFile(path.join(temporary, "provider-result.json"), receipt);
    await fs.writeFile(path.join(temporary, "original.mp4"), media);
    let acquired = false;
    const code = await main(["--url", douyinUrl, "--platform", "douyin", "--output", temporary, "--download"], { parser: { parse: async () => { acquired = true; throw new Error("Must not acquire"); } }, browserManager: fakeBrowser(douyinUrl).manager });
    assert.equal(code, 2); assert.equal(acquired, false);
    assert.equal(await fs.readFile(path.join(temporary, "provider-result.json"), "utf8"), receipt);
    assert.deepEqual(await fs.readFile(path.join(temporary, "original.mp4")), media);
    assert.deepEqual((await fs.readdir(temporary)).sort(), ["original.mp4", "provider-result.json"]);
  } finally {
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert(path.basename(temporary).startsWith("browser-acquire-existing-"));
    await fs.rm(temporary, { recursive: true, force: true });
  }
});
test("Direct Node CLI accepts an existing empty directory and exclusively saves its receipt", async () => {
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), "browser-acquire-empty-"));
  try {
    const parsed = { platform: "抖音", sourceUrl: douyinUrl, canonicalUrl: douyinUrl, videoId: "123456789", title: "Fixture caption", titleKind: "description", author: {}, statistics: {}, mediaStreams: [{ url: "https://fixture.douyinvod.com/target.mp4", type: "video+audio", format: "mp4" }], identityEvidence: { expected_id: "123456789", observed_id: "123456789", kind: "target-detail" } };
    const code = await main(["--url", douyinUrl, "--platform", "douyin", "--output", temporary], { parser: { parse: async () => parsed }, browserManager: fakeBrowser(douyinUrl).manager });
    assert.equal(code, 0);
    assert.equal(JSON.parse(await fs.readFile(path.join(temporary, "provider-result.json"), "utf8")).status, "metadata_ready");
    assert.deepEqual(await fs.readdir(temporary), ["provider-result.json"]);
  } finally {
    assert.equal(path.dirname(path.resolve(temporary)), path.resolve(os.tmpdir()));
    assert(path.basename(temporary).startsWith("browser-acquire-empty-"));
    await fs.rm(temporary, { recursive: true, force: true });
  }
});
