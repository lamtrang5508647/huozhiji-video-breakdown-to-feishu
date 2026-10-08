import { PlatformError, PlatformParser, preferPlatformError } from "./base.js";
import { identityError, requireSameNativeId, rejectAccessChallenge } from "./security.js";
import { itemKey, sleep, settleWithin } from "../utils/common.js";

const URL_PATTERNS = [
  /^https?:\/\/(?:www\.)?bilibili\.com\/video\/(BV[\w]+|av\d+)/i,
  /^https?:\/\/b23\.tv\//i,
];

const QUALITY_LABELS = new Map([
  [127, "8K"], [126, "杜比视界"], [125, "HDR"], [120, "4K"],
  [116, "1080P60"], [112, "1080P+"], [80, "1080P"], [74, "720P60"],
  [64, "720P"], [32, "480P"], [16, "360P"], [6, "240P"],
]);

// Only known content/access codes are permanent; rate-limit / risk-control stay retryable.
const PERMANENT_VIEW_API_CODES = new Set([
  -404, // not found
  62002, // 稿件不可见
  62004, // 稿件审核中
]);

export function classifyBilibiliViewApiError(code) {
  if (code == null || Number(code) === 0) return null;
  const numeric = Number(code);
  const permanent = PERMANENT_VIEW_API_CODES.has(numeric);
  return new PlatformError(`Bilibili API error: code ${numeric}`, {
    code: permanent ? "CONTENT_UNAVAILABLE" : "PLATFORM_API_ERROR",
    category: permanent ? "content" : "platform",
    permanent,
    retryable: !permanent,
    retryScope: permanent ? "none" : "item",
    userMessage: permanent
      ? `B站接口返回内容不可用状态 ${numeric}，已跳过。`
      : `B站接口返回异常状态 ${numeric}，稍后会重新解析。`,
  });
}

function numericFps(value) {
  if (Number.isFinite(value)) return value;
  if (typeof value !== "string") return null;
  const [numerator, denominator = "1"] = value.split("/").map(Number);
  return Number.isFinite(numerator) && Number.isFinite(denominator) && denominator !== 0
    ? numerator / denominator
    : null;
}

function qualityLabel(quality, formatInfo) {
  return formatInfo?.new_description
    ?? formatInfo?.display_desc
    ?? formatInfo?.description
    ?? QUALITY_LABELS.get(Number(quality))
    ?? (quality != null ? `QN${quality}` : null);
}

export class BilibiliParser extends PlatformParser {
  static getPlatformName() {
    return "B站";
  }

  static matchesUrl(url) {
    return URL_PATTERNS.some((pattern) => pattern.test(url));
  }

  async parse(browserManager, url, options) {
    const inputPage = this._requestedPart(url);
    const browser = await browserManager.start();
    const context = await browser.newContext(BilibiliParser.getBrowserContextOptions(browserManager, options));
    const page = await context.newPage();
    const metadata = [];
    const tasks = new Set();
    let riskControlled = false;
    const handleResponse = (response) => {
      const task = (async () => {
        if (response.status() === 403 || response.headers()["intercept-result"]?.includes("risk-control")) riskControlled = true;
        if (!/\/x\/web-interface\/view(?:[?#]|$)/.test(response.url()) || !response.ok()) return;
        const json = await response.json();
        if (json.code === 0 && json.data) metadata.push(json.data);
      })().catch(() => {});
      tasks.add(task);
      task.finally(() => tasks.delete(task));
    };
    page.on("response", handleResponse);
    try {
      await page.goto(url, { waitUntil: "domcontentloaded", timeout: options.pageTimeoutMs });
      const finalUrl = page.url();
      if (!BilibiliParser.matchesUrl(finalUrl)) throw identityError("Bilibili redirect did not resolve to a video page");
      const resolvedId = this._extractNativeId(finalUrl);
      const inputId = this._extractNativeId(url);
      if (!resolvedId) throw identityError("Bilibili native ID missing after redirect");
      const resolvedPage = this._requestedPart(finalUrl);
      if ((inputId || new URL(url).searchParams.has("p")) && inputPage !== resolvedPage) throw identityError("Bilibili redirect changed requested video part");
      const deadline = Date.now() + options.mediaWaitMs;
      let viewApiData = null;
      while (Date.now() < deadline) {
        viewApiData = metadata.find((item) => this._matchesNativeId(item, resolvedId) && (!inputId || this._matchesNativeId(item, inputId))) ?? null;
        if (!viewApiData) {
          const pageState = await page.evaluate(() => window.__INITIAL_STATE__?.videoData ?? null).catch(() => null);
          if (this._matchesNativeId(pageState, resolvedId) && (!inputId || this._matchesNativeId(pageState, inputId))) viewApiData = pageState;
        }
        if (viewApiData) break;
        await sleep(100);
      }
      await settleWithin(Promise.allSettled([...tasks]), 2000);
      const bodyText = await page.locator("body").innerText({ timeout: 3000 }).catch(() => "");
      rejectAccessChallenge(bodyText, finalUrl, riskControlled);
      if (/视频不存在|已被删除|审核中|仅限港澳台地区/u.test(bodyText)) {
        throw new PlatformError("Bilibili target is unavailable", { code: "CONTENT_UNAVAILABLE", category: "content", permanent: true });
      }
      if (!viewApiData) throw identityError("No exact Bilibili target metadata; recommendation data rejected");
      const nativeBvid = typeof viewApiData.bvid === "string" && /^BV[A-Za-z0-9]+$/.test(viewApiData.bvid) ? viewApiData.bvid : null;
      if (!nativeBvid) throw new PlatformError("Bound Bilibili metadata has no actual BV identity", { code: "METADATA_MISSING", category: "identity", permanent: true, retryable: false });
      const canonicalUrl = `https://www.bilibili.com/video/${nativeBvid}?p=${resolvedPage}`;
      // No intercepted or runtime stream is trusted: request the bound native ID and CID explicitly.
      const playurlApiData = await this._fetchPlayurlFallback(page, viewApiData, finalUrl);
      if (!playurlApiData) throw identityError("No media response for bound Bilibili ID/CID");
      const availableStreams = this._extractAvailableStreams(playurlApiData).map((stream) => ({ ...stream, referer: "https://www.bilibili.com/" }));
      const mediaAlternatives = this._buildMediaAlternatives(availableStreams);
      if (!mediaAlternatives.length) throw identityError("Bound Bilibili target has no supported playable streams");
      const owner = viewApiData.owner ?? {};
      return {
        platform: BilibiliParser.getPlatformName(), sourceUrl: url, canonicalUrl, resolvedUrl: finalUrl,
        videoId: nativeBvid, title: viewApiData.title ?? "",
        titleKind: viewApiData.title ? "native_title" : "missing",
        identityEvidence: { kind: "target-id-cid", expected_id: nativeBvid, observed_id: nativeBvid, requested_native_id: inputId, resolved_native_id: resolvedId, resolved_url: finalUrl, metadata_bvid: nativeBvid, aid: viewApiData.aid ?? null, page: resolvedPage, cid: this._resolveCid(viewApiData, finalUrl) },
        author: { nickname: owner.name ?? null, uid: owner.mid ? String(owner.mid) : null, url: owner.mid ? `https://space.bilibili.com/${owner.mid}` : null },
        description: viewApiData.desc ?? null, postTime: viewApiData.pubdate ? new Date(viewApiData.pubdate * 1000).toISOString() : null,
        duration: viewApiData.duration ?? null, statistics: viewApiData.stat ?? {},
        referer: "https://www.bilibili.com/", availableStreams, mediaAlternatives, mediaStreams: mediaAlternatives[0],
      };
    } finally {
      page.off("response", handleResponse);
      await settleWithin(Promise.allSettled([...tasks]), 2000);
      await settleWithin(context.close(), 5000);
    }
  }

  _extractNativeId(url) {
    try { return new URL(url).pathname.match(/\/video\/(BV[\w]+|av\d+)/i)?.[1] ?? null; } catch { return null; }
  }

  _matchesNativeId(metadata, nativeId) {
    if (!metadata || !nativeId) return false;
    if (/^BV/i.test(nativeId)) return String(metadata.bvid ?? "") === nativeId;
    return `av${metadata.aid}` === nativeId;
  }

  _requestedPart(url) {
    const values = new URL(url).searchParams.getAll("p");
    if (values.length > 1 || (values.length && !/^[1-9][0-9]*$/.test(values[0]))) throw identityError("Bilibili p must be exactly one positive integer");
    const pageNo = Number(values[0] ?? "1");
    if (!Number.isSafeInteger(pageNo)) throw identityError("Bilibili p exceeds a safe integer");
    return pageNo;
  }

  _resolveCid(viewApiData, url) {
    const pageNo = this._requestedPart(url);
    const pages = Array.isArray(viewApiData?.pages) ? viewApiData.pages : [];
    let cid;
    if (pages.length) {
      const matching = pages.filter((item) => Number(item?.page) === pageNo);
      if (matching.length !== 1) throw identityError("Requested Bilibili part has no unique trusted page/CID mapping");
      cid = matching[0].cid;
    } else {
      if (pageNo > 1) throw identityError("Bilibili multi-part request requires a trusted pages mapping");
      cid = viewApiData?.cid;
    }
    if (!/^[1-9][0-9]*$/.test(String(cid ?? ""))) throw identityError("Bound Bilibili part has no actual CID");
    return cid;
  }

  _normalizePlayurlData(json) {
    if (!json) return null;
    const data = json.data ?? json.result ?? null;
    if (!data) return null;
    return this._extractMediaStreams(data).length > 0 ? data : null;
  }

  async _extractPlayinfoFromPage(page) {
    const json = await page.evaluate(() => window.__playinfo__ ?? null).catch(() => null);
    return this._normalizePlayurlData(json);
  }

  async _fetchJsonFromPage(page, requestUrl) {
    return await page.evaluate(async (apiUrl) => {
      const response = await fetch(apiUrl, {
        credentials: "include",
        headers: { Accept: "application/json, text/plain, */*" },
      });
      const text = await response.text();
      try {
        return JSON.parse(text);
      } catch {
        return { code: -1, message: `Invalid JSON response: ${text.slice(0, 200)}`, httpStatus: response.status };
      }
    }, requestUrl).catch(() => null);
  }

  async _fetchPlayurlFallback(page, viewApiData, url) {
    const bvid = viewApiData?.bvid ?? url.match(/(BV[\w]+)/i)?.[1] ?? null;
    const aid = viewApiData?.aid ?? null;
    const cid = this._resolveCid(viewApiData, url);
    if (!cid || (!bvid && aid == null)) return null;

    const variants = [
      { qn: "127", fnval: "4048" },
      { qn: "127", fnval: "16" },
      { qn: "127", fnval: "0" },
    ];

    for (const variant of variants) {
      const params = new URLSearchParams({
        cid: String(cid),
        qn: variant.qn,
        fnval: variant.fnval,
        fnver: "0",
        fourk: "1",
        otype: "json",
        platform: "pc",
      });
      if (bvid) params.set("bvid", bvid);
      else params.set("avid", String(aid));

      const json = await this._fetchJsonFromPage(
        page,
        `https://api.bilibili.com/x/player/playurl?${params.toString()}`
      );
      const data = this._normalizePlayurlData(json);
      if (data) return data;
    }

    return null;
  }

  _extractMediaStreams(playurlData) {
    return this._buildMediaAlternatives(this._extractAvailableStreams(playurlData))[0] ?? [];
  }

  _extractAvailableStreams(playurlData) {
    const streams = [];
    const formatByQuality = new Map(
      (playurlData?.support_formats ?? []).map((format) => [Number(format.quality), format]),
    );
    const dash = playurlData?.dash;

    for (const video of dash?.video ?? []) {
      const url = video.baseUrl ?? video.base_url;
      if (!url) continue;
      streams.push({
        url,
        type: "video",
        format: "m4s",
        width: video.width ?? null,
        height: video.height ?? null,
        fps: numericFps(video.frameRate ?? video.frame_rate),
        bitrate: video.bandwidth ?? null,
        codec: video.codecs ?? (video.codecid != null ? String(video.codecid) : null),
        quality: video.id ?? null,
        label: qualityLabel(video.id, formatByQuality.get(Number(video.id))),
        source: "dash.video",
        totalBytes: video.size ?? null,
      });
    }

    const audioGroups = [
      [dash?.audio ?? [], "dash.audio"],
      [Array.isArray(dash?.dolby?.audio) ? dash.dolby.audio : dash?.dolby?.audio ? [dash.dolby.audio] : [], "dash.dolby.audio"],
      [Array.isArray(dash?.flac?.audio) ? dash.flac.audio : dash?.flac?.audio ? [dash.flac.audio] : [], "dash.flac.audio"],
    ];
    for (const [audios, source] of audioGroups) {
      for (const audio of audios) {
        const url = audio.baseUrl ?? audio.base_url;
        if (!url) continue;
        streams.push({
          url,
          type: "audio",
          format: "m4s",
          width: null,
          height: null,
          fps: null,
          bitrate: audio.bandwidth ?? null,
          codec: audio.codecs ?? (audio.codecid != null ? String(audio.codecid) : null),
          quality: audio.id ?? null,
          label: source === "dash.flac.audio" ? "Hi-Res无损" : source === "dash.dolby.audio" ? "杜比音频" : null,
          source,
          totalBytes: audio.size ?? null,
        });
      }
    }

    for (const durl of playurlData?.durl ?? []) {
      if (!durl?.url) continue;
      streams.push({
        url: durl.url,
        type: "video+audio",
        format: "mp4",
        width: playurlData.width ?? null,
        height: playurlData.height ?? null,
        fps: numericFps(playurlData.frame_rate),
        bitrate: playurlData.bandwidth ?? null,
        codec: null,
        quality: playurlData.quality ?? null,
        label: qualityLabel(playurlData.quality, formatByQuality.get(Number(playurlData.quality))),
        source: "durl",
        totalBytes: durl.size ?? null,
      });
    }
    return streams;
  }

  _videoRank(stream) {
    return [
      (Number(stream.width) || 0) * (Number(stream.height) || 0),
      Number(stream.quality) || 0,
      Number(stream.fps) || 0,
      Number(stream.bitrate) || 0,
      Number(stream.totalBytes) || 0,
    ];
  }

  _compareVideoStreams(a, b) {
    const rankA = this._videoRank(a);
    const rankB = this._videoRank(b);
    for (let index = 0; index < rankA.length; index += 1) {
      if (rankA[index] !== rankB[index]) return rankB[index] - rankA[index];
    }
    return 0;
  }

  _buildMediaAlternatives(availableStreams) {
    const videos = availableStreams.filter((stream) => stream.type === "video")
      .sort((a, b) => this._compareVideoStreams(a, b));
    const bestAudio = availableStreams.filter((stream) => stream.type === "audio")
      .sort((a, b) => (Number(b.bitrate) || 0) - (Number(a.bitrate) || 0))[0];
    if (videos.length > 0 && bestAudio) return videos.map((video) => [video, bestAudio]);

    return availableStreams.filter((stream) => stream.type === "video+audio")
      .sort((a, b) => this._compareVideoStreams(a, b))
      .map((stream) => [stream]);
  }

  _buildQualityAudit(playurlDataList, availableStreams, selectedStreams) {
    const advertised = new Map();
    for (const data of playurlDataList) {
      const formats = new Map((data?.support_formats ?? []).map((format) => [Number(format.quality), format]));
      const qualities = new Set([...(data?.accept_quality ?? []), ...formats.keys()]);
      for (const quality of qualities) {
        advertised.set(Number(quality), qualityLabel(quality, formats.get(Number(quality))));
      }
    }

    const accessible = new Map();
    for (const stream of availableStreams.filter((candidate) => candidate.type !== "audio")) {
      accessible.set(Number(stream.quality) || 0, stream.label ?? qualityLabel(stream.quality));
    }
    const advertisedQualities = [...advertised].sort((a, b) => b[0] - a[0]).map(([, label]) => label);
    const accessibleQualities = [...accessible].sort((a, b) => b[0] - a[0]).map(([, label]) => label);
    const selectedVideo = selectedStreams.find((stream) => stream.type !== "audio") ?? null;
    const highestAdvertised = Math.max(0, ...advertised.keys());
    const highestAccessible = Math.max(0, ...accessible.keys());

    return {
      advertisedQualities,
      accessibleQualities,
      selectedQuality: selectedVideo?.label ?? qualityLabel(selectedVideo?.quality),
      selectionReason: selectedVideo
        ? "Selected the highest-quality stream actually returned to the anonymous session; audio uses the highest available bitrate."
        : "No selectable stream was returned.",
      ...(highestAdvertised > highestAccessible ? { limitedBy: "anonymous_platform_access" } : {}),
    };
  }
}
