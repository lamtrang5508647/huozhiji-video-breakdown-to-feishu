import { PlatformError } from "./base.js";

export function identityError(message) {
  return new PlatformError(message, { code: "IDENTITY_UNVERIFIED", category: "identity", permanent: true, retryable: false });
}

export function requireSameNativeId(expected, observed, label = "target") {
  if (!expected || !observed || String(expected) !== String(observed)) {
    throw identityError(`${label} native identity is missing or mismatched`);
  }
  return String(expected);
}

export function rejectAccessChallenge(bodyText, finalUrl, riskControlled = false) {
  const text = String(bodyText ?? "");
  let loginPath = false;
  try { loginPath = /\/(?:login|passport|captcha|verify)(?:[/?#]|$)/i.test(new URL(finalUrl).pathname); } catch {}
  if (riskControlled || loginPath || /验证码|安全验证|完成验证|滑块验证|访问频次|访问受限|captcha|verify you are human|请先登录|登录后查看/i.test(text)) {
    throw new PlatformError("Verification, login or risk-control barrier; acquisition stopped", {
      code: "ACCESS_BLOCKED", category: "access", permanent: true, retryable: false,
      userMessage: "页面触发登录、验证码或风控，已停止提取。",
    });
  }
}
