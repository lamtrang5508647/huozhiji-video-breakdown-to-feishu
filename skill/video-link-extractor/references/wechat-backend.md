# 视频号 Provider 协议

## 明确授权的腾讯浏览器路径

统一入口可选择 `--wechat-browser --allow-tencent-parse`。这条路径单独实现于
`scripts/wechat_browser_acquire.mjs`，不需要安装上游捕获后端、系统代理或证书。
目前仅接受单条无额外查询参数或片段的 `https://weixin.qq.com/sph/...` 分享短链。
授权只允许本次指定链接提交腾讯元宝；登录需要正常人工操作，不自动读取或导出 Cookie。
需要等待扫码时加 `--manual-login`，它启用可见浏览器窗口；仅需可见窗口时也可用 `--headed`。运行命令见
[能力和运行条件](capabilities.md)。未登录且未选择人工登录时返回 `LOGIN_REQUIRED`。

浏览器在 `https://yuanbao.tencent.com` 页面上下文中请求
`/api/weixin/get_parse_result`，只发送当前链接和固定解析类型；随后在同一浏览器上下文
打开实际返回的 `https://channels.weixin.qq.com/finder-preview/pages/feed` 播放链接。
匹配 `/finder-preview/api/feed/get_feed_info` 的实际请求与响应，必须同时满足：

- 解析输入与用户本次链接一致，HTTP 为 2xx，明确业务码为 0。
- 播放链接只有一个非空 `eid` 和 `token`；详情请求的 `exportId` 与
  `baseReq.generalToken` 分别精确匹配，两者都不能仅由请求回显伪造。
- 详情响应 HTTP 为 2xx、`errCode=0`、`data.errMsg.type=0`，且
  `sceneInfo.dynamicExportId` 与播放 `eid`、实际请求 `exportId` 全等。
- 文案和媒体均来自这个已匹配详情响应的 `feedInfo`。解析响应的 `wx_export_id`
  可与播放 `eid` 不同，不能用它替代详情请求的导出标识。

保存原播放 URL 及完整签名参数，不重写为猜测地址。缺少身份链、只有封面、媒体加密
或登录/权限失败时，在下载前停止；只接受目标 feed 的 HTTPS 腾讯视频地址。

平台没有返回原始数字视频 ID 时，回执明确 `native_id_kind=platform_export_id`、
`platform_export_id=<原始导出标识>`、`identity_stability_verified=false`。
`source.json.video_id` 使用 `wxexp_` 加原标识 UTF-8 的无填充 Base64URL，
作为安全可逆的本地键。拆解源身份检查同时验证编码、原标识、原输入与播放 `eid`。
这个键不证明跨会话长期稳定，也不能传入只接受数字对象 ID 的平台接口。

Node 提供方只报告 `metadata_ready` 或 `downloaded`；外层 Python 完成实际视频流、
全片视频/音轨解码及哈希检查后，才报告 `ready`。以下旧详情 Provider 路径保留
原来的对象 ID/nonce/token 约束；它不能靠增加来源声明字段变成腾讯浏览器身份链。

## 显式配置的详情 Provider

`scripts/wechat_acquire.py` 是独立的链接采集入口。它只调用用户明确配置的
Provider，不安装证书、不修改系统代理、不启动微信或捕获服务、不读取或导出
Cookie，也不解密媒体。MIT 来源和固定上游版本见
`vendor/qiaomu-download/UPSTREAM.md` 与 `LICENSE`。

## 本地 Provider

必须显式提供本机 HTTP 基地址，例如：

```sh
python scripts/wechat_acquire.py --url "https://channels.weixin.qq.com/web/pages/feed?object_id=1234567890123456789&objectNonceId=syntheticNonce" --output "<任务目录>" --metadata-only --backend-url "http://127.0.0.1:2022"
```

基地址只接受 `127.0.0.1`、`localhost` 或 `::1`，不得携带认证、查询参数或
自定义路径。入口请求：

```text
GET /api/channels/feed/profile?url=<经 URL 编码的原分享 URL>
```

这是**本适配器要求 Provider 满足的协议**，不是对任意现有视频号工具的可用性
保证。现有工具即使返回视频详情，也必须返回与原输入中 ID/nonce/token 精确
匹配的源视频对象字段；不得仅因为请求参数包含该链接，便把任意响应中的 ID
当作该链接的视频。`input_url`、`params.url` 等来源回显只用于检查矛盾，不能
单独证明身份，`provenance` 名称也不使字段自动成为已验证证据。

以下是合成响应，字段和媒体地址仅用于说明协议：

```json
{
  "code": 0,
  "input_url": "https://channels.weixin.qq.com/web/pages/feed?object_id=1234567890123456789&objectNonceId=syntheticNonce",
  "data": {
    "errCode": 0,
    "data": {
      "object": {
        "id": "1234567890123456789",
        "objectNonceId": "syntheticNonce",
        "objectDesc": {
          "description": "平台实际返回的完整视频文案",
          "media": [
            {
              "url": "https://finder.video.qq.com/synthetic.mp4",
              "decodeKey": 0
            }
          ]
        }
      }
    }
  }
}
```

来源回执也可位于 `provenance.input_url`、`provenance.source_url` 或
`params.url`；它必须与原输入的 HTTPS 域名、路径和查询参数一致，查询参数顺序
可不同。若存在多个回执，任何一个不一致都会拒绝。即使所有回执一致，也必须
通过以下原输入查询参数与源视频对象之间的独立比对。

原 URL 已含 `object_id`/`objectId`/`id`/`feed_id`/`feedId` 时，值必须与响应
原生 ID 一致；重复或冲突值都会拒绝。原 URL 的 `nonce_id`/`nonceId`/
`objectNonceId` 或 `token` 与响应 `object`/`feedInfo` 自身对应的 nonce/token
字段精确匹配，也可以建立来源绑定。响应外层或 `params` 的 nonce/token 回显
不参与证明。分享路径本身的 token、文件名和 URL 哈希不属于视频原生 ID，不能
代替 `object.id`。

上游微信播放长链接的 `oid`、`nid` 是 Base64 编码字段。本入口按上游
`DecodeBase64ToUint64String` 的大端 uint64 规则还原后，分别与对象 ID、对象
自身的 `objectNonceId` 比对；不会修改原始 URL 或用外层 nonce 回显作证。
空值、非法编码、重复或与其他身份参数冲突时，在下载前停止。

普通 `https://weixin.qq.com/sph/<分享token>` 短链接没有这些可比对的身份参数时，
即使 Provider 原样回显 URL 并返回某个真实 ID、标题和媒体，也只能得到
`metadata_missing/source_binding_missing`，不会标为 `metadata_ready` 或启动下载。
当前入口不自行解析短链重定向，也不把 Provider 声称的来源绑定升级为验证。

## 在线 Provider

在线解析必须同时提供 `--online-endpoint <HTTPS接口>` 和
`--allow-online-parser`，明确允许把该次分享 URL 发给该接口。没有默认第三方
服务，也没有失败后自动发送链接的回退。在线请求为：

```text
POST <显式配置的 HTTPS 接口>
Content-Type: application/json

{"url":"https://channels.weixin.qq.com/web/pages/feed?object_id=1234567890123456789&objectNonceId=syntheticNonce"}
```

响应可使用上述 `object` 结构，或 `data.feedInfo`，其中真实 `id`、真实
`title`/`description`、来源绑定证据仍然必须齐全。`h264VideoInfo.videoUrl`、
`h265VideoInfo.videoUrl` 或 `videoUrl` 可提供媒体地址。HTTP 200、成功提示和
仅含媒体地址的响应都不足以证明身份正确。

本入口不会发送 Cookie、微信登录态或抓包。接口不接受用户名/密码、查询参数、
自定义端口或重定向；需要这些认证能力的服务应由用户在本机配置受控桥接服务。
本机 Provider 与在线 Provider 每次只能选择一个。

## 状态和媒体边界

结果写到 `<任务目录>/provider-result.json`。标准输出只包含状态、固定错误码
和结果路径，不打印来源查询 token、原始响应、签名媒体地址或解密字段。

| 条件 | status | reason |
| --- | --- | --- |
| 未配置 Provider | `blocked` | `provider_not_configured` |
| 已启动本地服务，但微信页面 socket 未连接 | `blocked` | `page_connection_required` |
| 在线接口未明确允许 | `blocked` | `online_parser_not_authorized` |
| 无平台原生 ID | `metadata_missing` | `native_id_missing` |
| 无真实标题/文案 | `metadata_missing` | `title_missing` |
| 缺少原输入身份参数与对象字段的匹配，或只有 URL 回显 | `metadata_missing` | `source_binding_missing` |
| 来源回执、ID 或 nonce/token 冲突 | `blocked` | `unexpected_source` |
| 下载时发现加密标志/非零 decodeKey | `blocked` | `encrypted_media_not_supported` |
| 身份、标题和来源绑定通过 | `metadata_ready` | 无 |
| 非加密媒体字节下载完成 | `downloaded` | 无 |

`--metadata-only` 是默认模式，结果的 `media_path` 为 null。
`--download` 仅允许非加密、可访问的 HTTPS 腾讯媒体地址，并检查重定向域名、
传输超时和大小上限；不覆盖已有媒体文件。遇到验证码、登录或不可访问内容应
由 Provider 返回失败，不通过本入口绕过限制。

成功元数据含 `platform=wechat_channels`、`input_url`、`canonical_url`、
`native_id`、真实 `title`、`title_kind`、`identity_verified=true`。
`downloaded` 另含绝对 `media_path`，并明确 `media_verified=false`。
`downloaded` 只证明字节传输完成；外层统一 acquire 流程必须执行 FFprobe
视频流检查、完整解码及文件哈希校验，之后才可报告最终 `ready`。

此入口的测试使用合成响应和模拟传输，不建立真实微信登录态，也不证明当前
外部接口或真实平台链接可下载。

## 本地服务与微信页面连接

已核对上游 `ltaoo/wx_channels_download` 的 v260907，官方 Windows x86_64 普通
构建包 SHA-256 为 `2d4903ed994292ffa1ad019f8524c7b0bf37c25e0966a2e7903de90f3fb678ab`。
构建包应按官方 Release 的摘要验证，存放在仓库外或忽略的运行目录，不随 Skill 分发。
上游许可证为 MIT 加 Commons Clause；不得将该后端标成无附加条件的 MIT。

使用独立配置和空工作目录，`server` 子命令可只启动 loopback API，并关闭证书
自动安装、拦截代理和系统代理修改。API 地址由 `api.hostname`、`api.port` 指定；
`--hostname`、`--port` 属于代理参数。应同时关闭桥接、自动更新和远程下载。
建议显式配置自行生成且未信任的独立证书文件，避免证书加载器读取其他软件的已有私钥。

`GET /api/channels/status` 中 `data.available=false` 表示微信页面未连接。
此时 `/api/channels/feed/profile` 可能返回 HTTP 200，但 JSON 为 `code=400`，
并带精确消息 `please initialize the client socket connection first`。
本入口将其识别为 `page_connection_required`；HTTP 200 不能当作提取成功。

仅启动 `server` 不会给微信页面注入连接代码，用户仅打开视频也不能保证连接存在。
上游普通启动流程涉及证书和拦截代理，必须先确认具体配置与授权。
默认根证书及私钥是共享的，不应直接信任；Windows 退出逻辑只关闭匹配的代理，
不会恢复原来的代理配置。若选择本地捕获方式，必须用独立证书、精确的原配置快照
和外层恢复守护，再正常登录并打开目标视频。禁止将服务启动或页面播放当作已下载。

官方来源：[Release](https://github.com/ltaoo/wx_channels_download/releases/tag/v260907)、
[server 模式](https://github.com/ltaoo/wx_channels_download/blob/v260907/cmd/server.go)、
[证书配置](https://github.com/ltaoo/wx_channels_download/blob/v260907/docs/config/cert.md)、
[Windows 代理实现](https://github.com/ltaoo/wx_channels_download/blob/v260907/pkg/system/proxy_windows.go)。
