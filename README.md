# 游戏版本库

把多个**公开**的 Minecraft / 星露谷物语版本来源，汇总成「平台 → 大版本 → 版本 → 多来源下载」的静态站点。

- 数据全部来自各站公开接口，本站只索引、不托管任何游戏文件
- 每次同步产出 `data/*.json`，再用 MkDocs Material 生成静态页
- 同一版本会合并多个来源的下载方式（官方 CDN、镜像、网盘分享页等），卡片上按来源标注

## 目录结构

```
.
├── data/                     # 同步产物：每个平台一个 JSON + catalog.json
├── docs/                     # MkDocs 站点；只提交主题资源，页面由脚本生成
│   ├── img/favicon.svg
│   └── stylesheets/index.v7.css
├── scripts/
│   ├── sync.py               # 抓取 + 归一化 + 合并（约 2300 行）
│   ├── gen_mkdocs.py         # data/*.json → docs/**/*.md
│   ├── make_sitemap.py       # 构建后生成 sitemap.xml / robots.txt
│   └── deploy.example.sh     # 通用 rsync 部署示例
├── tools/
│   └── probe_mcdl.py         # MCDL 分页 / 下载渠道解析探针
├── mkdocs.yml
└── requirements.txt
```

## 快速开始

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

python3 scripts/sync.py --only mcdl      # 先跑一个源试试（其余源见 --only 列表）
python3 scripts/sync.py --only all       # 全量同步（有缓存，首次约 15 分钟）
python3 scripts/gen_mkdocs.py            # 生成页面
mkdocs build                             # 产物在 site/
```

本地预览：

```bash
mkdocs serve
```

`scripts/sync.py` 的原始响应缓存在 `cache/`，重复跑会复用（部分源带 TTL）。

## 数据源

| 源 | 端点 | 说明 |
| --- | --- | --- |
| Mojang Piston | `piston-meta.mojang.com` + 每版本 JSON | Java 版官方清单，client/server jar 的 sha1 与体积来自官方 |
| BMCLAPI / 快速镜像 | `bmclapi2.bangbang93.com`、`download.fastmirror.net` | Java 版国内镜像 |
| MCDL（MineBBS） | `api.mc.minebbs.com/api/v1/versions/` | 基岩版 Android/iOS/Windows；下载链接是加密跳转，见下文 |
| MCAPKS | `mcapks.net` | 基岩版 APK，按 `armeabi-v7a` / `arm64-v8a` 分架构 |
| MCAPPX | `data.mcappx.com/v2/bedrock.json` | 基岩版 Windows（GDK / UWP） |
| 星月（Spectrollay） | `spectrollay.com/data/...` | 基岩版目录型资源，单独标 `scope=directory` |
| 星露谷 | 各公开清单 | Android 版 |

部分站点明确限制第三方抓取或无公开接口（MCARC 动态库、NullAtom、甘泉、小布等），代码里只做外链，不抓内容。

## 数据结构

`data/<device>.json`：

```jsonc
{
  "device": "android",
  "versions": [
    {
      "v": "1.21.90.3",          // 归一化版本号
      "channel": "release",       // release / beta / preview / snapshot / old
      "channels": ["beta", "release"],
      "released": "2025-06-17",
      "major": "1.21",
      "size": 723000000,
      "offers": [                 // 同一版本的所有下载方式
        {
          "source": "mcdl",       // 来源 id
          "provider": "123云盘",   // 展示名
          "kind": "share",        // file / share / page / link / directory
          "url": "https://www.123pan.com/s/xxxx",
          "arch": "arm64-v8a",
          "size": null,
          "checksum_type": "", "checksum": "",
          "scope": "file",
          "note": "MCDL 通道 2",
          "password": ""
        }
      ]
    }
  ],
  "directories": []
}
```

`data/catalog.json` 是站点导航用的汇总（每设备版本数、大版本分布、来源列表）。

## MCDL 下载原理（实测）

MCDL / `mc.minebbs.com` 是一个 Vue 3 SPA，页面本身没有文件，全部走接口：

1. **列表接口**：`GET https://api.mc.minebbs.com/api/v1/versions/?page=N&pageSize=10`
   - `pageSize` 上限 **10**，传 20 直接 `400 {"message":"参数验证失败","errors":["pageSize must be less than or equal to 10"]}`
   - 站点当前共 **59 页 / 581 条**；响应顶层只有 `status`、`totalPages`、`data`
   - 每条字段：`id`、`version`、`platform`（1=Android，2=iOS，3=Windows）、`environment`（1=正式版，0=快照/测试）、`size`、`time`、`total_download_count`、`log`、`wikiLinks`、`officialLinks`、`newsLinks`、`bdsLinks`、`downloadlinks`

2. **下载按钮**：前端组件 `downloadButton` 拿到 `downloadlinks` 数组后
   - 主按钮「下载」→ `window.open(downloadLinks[0])`
   - 鼠标悬停展开下拉，条目文字是 `下载通道 ①`、`②`、`③`…… 对应数组第 2 条起
   - 所以「一个按钮弹出 4 个渠道」= 该版本共 5 条链接：主按钮 1 条 + 下拉 4 条。实测分布：2 条 360 个版本、3 条 199 个、4 条 2 个、5 条 20 个

3. **跳转是加密的**：每条链接形如
   `//api.mc.minebbs.com/api/v1/redirect?url=U2FsdGVkX1...&id=588`
   其中 `url` 是服务端 AES 密文，客户端解不开也不该解，只是把整条 URL 打开；服务端解密后 `302` 到真实网盘。

4. **抓取要点（都踩过坑）**
   - 必须带 `Referer: https://mc.minebbs.com/`，否则（或带错）会 `302` 回首页 `https://mc.minebbs.com/`
   - 用 `GET` 且 `allow_redirects=False` 读 `Location`；`HEAD` 会返回 **200 且没有 Location**，不可用
   - 并发批量会被软限流：同样是 `302` 回首页，看着和死链一模一样。串行 `0.9s/条` 实测稳定；死链标记应短 TTL（本仓库 12 小时）并允许重试，否则会把活链误判成死链
   - 计数接口 `GET /api/v1/download/count/add/{id}` 需要页面侧参数，直接调用返回 `版本过期，请按 ctrl+F5 刷新页面`，抓取时忽略即可
   - 详情页路由是 `https://mc.minebbs.com/#/resources/{id}`

   一次全量解析 ≈ 1425 条链接 ≈ 25 分钟（串行）。`tools/probe_mcdl.py` 可以复现这套流程：

   ```bash
   python3 tools/probe_mcdl.py --pages          # 只抓分页，统计版本/平台/渠道数
   python3 tools/probe_mcdl.py --pages --resolve # 再逐条解析 302 目标（慢，默认 0.9s/条）
   ```

## 部署

`scripts/deploy.example.sh` 是一个最小示例：生成 → 构建 → `rsync` 到任意静态目录。

```bash
SITE_DIR=/var/www/mclib SITE_URL=https://example.com bash scripts/deploy.example.sh
```

`make_sitemap.py` 的域名来自环境变量 `SITE_URL`（默认 `https://example.com`），仓库里不含任何具体域名。

## 定时同步

```cron
17 */6 * * *  cd /path/to/repo && bash scripts/deploy.example.sh >> logs/cron.log 2>&1
```

## 已知限制

- 网盘分享页会失效（尤其猫盘 `cloud.nyan.run`），站点上表现为「转原帖」；同步脚本会对这类链接做一次可用性校验
- 部分上游接口没有公开文档，字段可能随时变化；`sync.py` 对单源失败是容错的，不会中断整体同步
- 版本号归一化是启发式的（例如 `1.26.70.21 → 26.70.21`、Windows 包 `1.21.12020.0 → 1.21.120.20`），极端历史版本可能有偏差

## 免责声明

本项目只汇总公开可访问的下载入口，不存储、不转存、不分发任何游戏文件。Minecraft 是 Mojang/Microsoft 的商标，星露谷物语是 ConcernedApe 的作品；请通过各来源的条款使用相应资源。

## 许可

MIT，见 [LICENSE](LICENSE)。
