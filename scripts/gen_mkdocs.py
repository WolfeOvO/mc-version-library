#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate MkDocs Material pages from data/*.json."""
import json
import re
from collections import OrderedDict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
DATA = ROOT / "data"

DEVICES = OrderedDict([
    ("java", "Java 版"),
    ("android", "基岩版 · Android"),
    ("ios", "基岩版 · iOS"),
    ("windows", "基岩版 · Windows"),
    ("stardew", "星露谷物语"),
])
PLATFORM_PATH = {
    "java": "minecraft/java.md",
    "android": "minecraft/android.md",
    "ios": "minecraft/ios.md",
    "windows": "minecraft/windows.md",
    "stardew": "stardew/android.md",
}
PLATFORM_LABEL = {"java": "Java 版", "android": "基岩版 · Android",
                  "ios": "基岩版 · iOS", "windows": "基岩版 · Windows",
                  "stardew": "Android"}
GAMES = OrderedDict([
    ("minecraft", {"title": "Minecraft", "devices": ["java", "android", "ios", "windows"],
                   "extra": ("模组加载器（Fabric / Quilt / Forge / NeoForge / OptiFine / LiteLoader）", "resources/")}),
    ("stardew", {"title": "星露谷物语", "devices": ["stardew"], "extra": None}),
])
CH_LABEL = {"release": "正式版", "beta": "测试版", "preview": "预览版", "snapshot": "快照", "old": "远古版本"}
ARCH_LABEL = {"arm64-v8a": "ARMv8", "armeabi-v7a": "ARMv7", "x64": "x64", "x86": "x86", "arm": "ARM64", "": "通用"}
ARCH_NOTE = {
    "java": [("通用", "官方 client.jar / server.jar，任意平台可用")],
    "android": [("ARMv7", "32 位、老旧手机与平板首选"), ("ARMv8", "64 位、较新手机与平板首选")],
    "ios": [("ARM64", "iOS 设备通用，需自签或企业证书安装")],
    "windows": [("x64", "64 位 Windows 10/11 首选"), ("x86", "32 位系统 / 旧设备"), ("ARM64", "Windows on ARM 设备")],
    "stardew": [("armv7", "32 位 Android 手机 / 老旧设备"),
                ("armv8", "64 位 Android 手机 / 平板首选"),
                ("x86", "模拟器 / 32 位 x86 设备"),
                ("x86_64", "模拟器 / 64 位 x86 设备")],
}
SRC_NAME = {"mojang": "Mojang Piston", "mcdl": "MineBBS MCDL", "mcapks": "MCAPKS",
            "mcappx": "MCAPPX", "spectro": "星月版本库", "mcarc": "MCARC / zihao_il",
            "xiaobu": "小布のMC版本库", "ganquan": "甘泉版本库", "mcpelife": "MCPELIFE",
            "nullatom": "NullAtom 原子资源", "bmclapi": "BMCLAPI 镜像", "mcarc_static": "MCARC 旧版归档",
            "fastmirror": "FastMirror 无极镜像",
            "stardew": "星露谷版本库"}
NAMED = ("OneDrive", "夸克", "123", "百度", "迅雷", "光鸭", "UC", "阿里", "天翼", "移动")


def pad(n):
    return " " * n


def esc(s):
    return str(s).replace("|", "\\|")


def fmt_size(n):
    if not n:
        return ""
    u = ["B", "KB", "MB", "GB"]
    i = 0
    x = float(n)
    while x >= 1024 and i < len(u) - 1:
        x /= 1024
        i += 1
    return (f"{x:.1f}" if x < 10 and i else f"{round(x)}") + " " + u[i]


def major_of(v):
    m = re.match(r"^(\d+)\.(\d+)", str(v))
    if not m:
        return "其他"
    if m.group(1) == "26":     # 基岩版 26.x 整代合并
        return "26.x"
    return f"{m.group(1)}.{m.group(2)}"


def major_for(dev, v):
    return "1.x" if dev == "stardew" else major_of(v)


def slug(s):
    if s == "远古版本":
        return "legacy"
    s = str(s).replace(".", "-").replace(" ", "-")
    s = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]", "", s)
    return s or "other"


def tabs(pairs):
    """pymdownx tabbed block from [(label, body)]"""
    out = []
    for label, body in pairs:
        out.append(f'=== "{label}"')
        out.append("")
        for line in body.splitlines():
            out.append(("    " + line) if line.strip() else "")
        out.append("")
    return "\n".join(out)


def indent(text, n):
    p = pad(n)
    return "\n".join((p + l) if l.strip() else "" for l in text.splitlines())


def details(title, body, kind="info", open_=True):
    head = f'???{ "+" if open_ else "" } {kind} "{title}"'
    return head + "\n" + indent(body, 4)


def named_first(provider):
    return (0 if any(k.lower() in provider.lower() for k in NAMED) else 1, provider)


SOURCE_LABEL = {
    "mojang": "Mojang 官方", "bmclapi": "BMCLAPI 镜像", "fastmirror": "FastMirror 无极镜像",
    "mcdl": "MineBBS MCDL",
    "mcapks": "MCAPKS", "mcappx": "MCAPPX", "mcarc": "MCARC / zihao_il",
    "xiaobu": "小布", "ganquan": "甘泉", "mcpelife": "MCPELIFE",
    "nullatom": "NullAtom", "mcarc_static": "MCARC 旧版归档", "spectro": "星月",
    "stardew": "星露谷版本库",
}
SOURCE_ORDER = list(SOURCE_LABEL)
_PROVIDER_PREFIX = ("MCARC · ", "小布 · ", "甘泉 · ", "MCAPPX 加速节点 · ", "MCAPPX · ",
                    "NullAtom · ", "MCPELIFE · ", "Mojang 官方 · ", "BMCLAPI 镜像 · ",
                    "FastMirror 无极镜像 · ")


def short_provider(o):
    prov = (o.get("provider") or o.get("source") or "下载").strip()
    for p in _PROVIDER_PREFIX:
        if prov.startswith(p):
            prov = prov[len(p):].strip() or p.strip(" ·")
            break
    return prov


def split_provider(src, o):
    """provider -> (netdisk, variant). e.g. '123云盘(原OneDrive_365)' -> ('123云盘','原OneDrive_365')."""
    prov = (o.get("provider") or "").strip()
    for pre in _PROVIDER_PREFIX:
        if prov.startswith(pre):
            prov = prov[len(pre):].strip()
            break
    note = (o.get("note") or "").strip()
    if src == "mojang":
        return "官方直链", prov or "文件"
    if src == "bmclapi":
        return "镜像直链", prov or "文件"
    if src == "fastmirror":          # 无极镜像：和 BMCLAPI 一样是官方原版镜像
        return "镜像直链", prov or "文件"
    variant = ""
    m = re.match(r"^(.*?)\((原[^)]*)\)\s*$", prov)
    if m:  # e.g. 123云盘(原OneDrive_365): keep the origin as a variant
        prov, variant = m.group(1).strip(), m.group(2).strip()
    if " · " in prov:
        head, tail = prov.split(" · ", 1)
        prov, variant = head.strip(), (variant or tail.strip())
    if prov.endswith("直链") and len(prov) > 2:  # 123云盘直链 -> 123云盘
        prov = prov[:-2]
    if not variant:
        ch = re.search(r"(通道\s*\d+|线路\s*\d+)", note)
        if ch:
            variant = ch.group(1)
    if re.match(r"^(通道|线路)\s*\d+$", variant or ""):
        variant = ""   # multiple lines of the same netdisk: buttons, not tabs
    return prov or "下载", variant


def source_groups(v):
    """source -> netdisk -> variant -> arch -> [(label, url, kind), ...]"""
    groups = OrderedDict()
    for o in v.get("offers") or []:
        url = o.get("url")
        if not url:
            continue
        src = o.get("source") or "other"
        netdisk, variant = split_provider(src, o)
        arch = o.get("arch") or ""
        label = o.get("note") or ""
        bucket = (groups.setdefault(src, OrderedDict())
                  .setdefault(netdisk, OrderedDict())
                  .setdefault(variant, OrderedDict())
                  .setdefault(arch, []))
        if all(u != url for _, u, _k in bucket):
            bucket.append((label, url, o.get("kind") or ""))
    ordered = OrderedDict()
    for src in SOURCE_ORDER + [x for x in groups if x not in SOURCE_ORDER]:
        if src in groups:
            ordered[src] = groups[src]
    return ordered


def button(url, label="", multi=False, kind=""):
    if kind == "page":
        return f"[查看原帖 :material-arrow-top-right:]({url}){{ .md-button .mc-button }}\n"
    text = "下载"
    if multi and label:
        text = label[:18]
    return f"[{text} :material-download:]({url}){{ .md-button .mc-button }}\n"


def render_urls(urls):
    """Multiple lines of the same netdisk/arch -> enumerated buttons, not tabs."""
    if len(urls) > 1:
        return "\n".join(button(u, f"下载通道 {i + 1}", True, k)
                         for i, (lab, u, k) in enumerate(urls))
    lab, u, k = urls[0]
    return button(u, "", False, k)


def download_body(v):
    """4 levels: source site -> netdisk -> variant -> architecture -> buttons."""
    groups = source_groups(v)
    if not groups:
        return "_暂无可用下载链接。_\n"
    src_pairs = []
    for src, netdisks in groups.items():
        nd_pairs = []
        for nd, variants in netdisks.items():
            real_variants = [x for x in variants if x]
            if len(variants) > 1 or real_variants:
                var_pairs = []
                for var, archs in variants.items():
                    if len(archs) > 1:
                        apairs = [(ARCH_LABEL.get(a, a or "通用"), render_urls(u)) for a, u in archs.items()]
                        var_pairs.append((var or "默认", tabs(apairs)))
                    else:
                        _, urls = next(iter(archs.items()))
                        var_pairs.append((var or "默认", render_urls(urls)))
                nd_pairs.append((nd, tabs(var_pairs)))
            else:
                archs = variants.get("") or next(iter(variants.values()))
                _, urls = next(iter(archs.items()))
                nd_pairs.append((nd, render_urls(urls)))
        src_pairs.append((SOURCE_LABEL.get(src, src), tabs(nd_pairs)))
    return tabs(src_pairs)


def sibling_versions(v, pool, dev):
    """同为一次更新的构建号版本（1.4.0 ↔ 1.4.0.5、26.52 ↔ 26.52.3）；Java 不适用。"""
    if dev == "java":
        return []
    name = v.get("v") or ""
    if len(re.split(r"[.\-]", name)) >= 4:          # 本身就是四段构建号
        return []
    pref = name + "."
    sibs = sorted({x["v"] for x in pool if x["v"].startswith(pref)},
                  key=lambda s: [int(t) for t in re.findall(r"\d+", s)][:5])
    return sibs[:6]


def version_block(v, open_=True, sibs=None):
    meta = []
    if v.get("released"):
        meta.append(v["released"])
    if v.get("size"):
        meta.append(fmt_size(v["size"]))
    links = v.get("links") or {}
    label = f'{v["v"]} · {CH_LABEL.get(v.get("channel"), v.get("channel") or "版本")}'
    body = []
    if meta:
        body.append("- " + " · ".join(meta))
    if sibs:
        body.append("- 同一更新的构建号版本：" + "、".join(f"`{s}`" for s in sibs)
                    + "（来源数见本页对应条目）")
    if links.get("wiki"):
        body.append(f'- 更新日志：[Minecraft Wiki]({links["wiki"]})')
    elif links.get("official"):
        body.append(f'- 更新日志：[官方日志]({links["official"]})')
    elif links.get("page"):
        body.append(f'- 版本页：[MCAPPX]({links["page"]})')
    dir_notes = sorted({o.get("note") for o in v.get("offers") or []
                        if o.get("kind") == "directory" and o.get("note")})
    if dir_notes:
        rng = dir_notes[0].replace("（非单文件）", "").strip()
        if len(dir_notes) == 1 and rng.startswith("整分支目录 "):
            body.append(f"- 含**{rng}**分享（非单文件），请以目录内实际内容为准。")
        else:
            body.append("- 含**整分支目录**分享（非单文件），请以目录内实际内容为准。")
    body.append("")
    body.append(download_body(v))
    # colour-code by channel: release=green, beta/preview=amber, snapshot=purple, old=grey
    kind = {"release": "success", "beta": "warning", "preview": "warning",
            "snapshot": "example", "old": "quote"}.get(v.get("channel") or "", "info")
    return details(label, "\n".join(body), kind, open_)


def version_tuple(vstr):
    return [int(n) for n in re.findall(r"\d+", str(vstr))[:4]]


def sort_key(v):
    return (v.get("released") or "", version_tuple(v["v"]))


BEDROCK_DEVS = {"android", "ios", "windows"}


def page_sorted(dev, vs):
    """星露谷按上游数组顺序（0 = 最新，1.00 在最后）。

    基岩版按**版本号**倒序：26.x 起是单调递增编号（26.0 < 26.10 < 26.52.3 < 26.70.21），
    各来源给的 released 互相冲突（26.44.3 比 26.44 还早、26.22.1 比 26.22 早），
    按日期排会把 26.52.3 夹在 26.60.x 中间，所以日期只做同位 tiebreak。
    Java 保持按日期（Mojang manifest 的 releaseTime 权威，且快照号 26w14a 不能按数字排）。
    """
    if dev == "stardew":
        return sorted(vs, key=lambda v: int((v.get("meta") or {}).get("idx", 0)))
    if dev in BEDROCK_DEVS:
        return sorted(vs, key=lambda v: (version_tuple(v["v"]), v.get("released") or ""), reverse=True)
    return sorted(vs, key=sort_key, reverse=True)


def newest(versions, release=True, by_idx=False):
    """Newest by version number, preferring versions the wiki confirms as releases.

    by_idx: 星露谷用上游数组下标（0 = 最新，1.00 在最后）——它的点号不是小数，
            1.321 / 1.34 / 1.00 按数字排会全错。
    """
    pool = [v for v in versions if (v.get("channel") == "release") == release]
    if not release:                                  # 测试/快照池：剔掉远古版本与非数字版本号（inf-20100618 之类）
        pool = [v for v in pool if v.get("channel") != "old" and re.match(r"^[0-9]", v["v"])]
    if not pool:
        return None
    if release:
        confirmed = [v for v in pool if (v.get("meta") or {}).get("wiki") == "release"]
        if confirmed:
            pool = confirmed
        real = [v for v in pool
                if any(o.get("kind") in ("direct", "share", "link") for o in v.get("offers", []))]
        if real:
            pool = real
    if by_idx:
        return min(pool, key=lambda v: int((v.get("meta") or {}).get("idx", 0)))
    return max(pool, key=lambda v: (version_tuple(v["v"]), v.get("released") or ""))


SOURCE_GAME = {"stardew": "星露谷物语"}


def source_game(key):
    return SOURCE_GAME.get(key, "Minecraft")


def source_table(catalog, dev=None, versions=None, game=None):
    """来源表。传了 dev/versions 就只列该平台实际收录的来源，版本数按本平台统计；
    否则（全站来源页）列全部来源的全局版本数。"""
    per = {}
    for v in (versions or []):
        for s in set(v.get("sources") or []):
            per[s] = per.get(s, 0) + 1
    rows = ["| 来源 | 版本数 | 状态 | 最后同步 (UTC) |", "| --- | ---: | --- | --- |"]
    seen = set()
    for s in catalog.get("sources") or []:
        key = s.get("key")
        if dev and key not in per:
            continue
        if game and source_game(key) != game:
            continue
        seen.add(key)
        n = per.get(key, 0) if dev else s.get("count", 0)
        st = "正常" if s.get("ok") else "失败"
        last = (s.get("last_sync") or "")[:16].replace("T", " ")
        rows.append(f'| [{esc(s["name"])}]({s["homepage"]}) | {n} | {st} | {last} |')
    for key in sorted(set(per) - seen):   # 该平台用到了、但 catalog.sources 里没有的源
        rows.append(f'| {esc(SRC_NAME.get(key, key))} | {per[key]} | — | — |')
    return "\n".join(rows)


def spectro_platforms():
    p = DATA / "spectro_platforms.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def spectro_note(dev, cov):
    """星月每个平台的覆盖情况（Windows 上游只有占位空链，必须说清楚）。"""
    info = (cov.get("platforms") or {}).get(dev)
    if not info:
        return []
    if info.get("links"):
        line = (f"星月版本库：{info.get('branches')} 个分支目录，"
                f"{info.get('branches_with_links')} 个分支有分享链接（共 {info.get('links')} 条，均为整分支目录，非单文件）。")
    elif info.get("branches"):
        line = (f"星月版本库：上游只有 {info.get('branches')} 个分支的**占位条目**"
                f"（{info.get('blank_links')} 个网盘位置全为空链接，其站内 Windows 下载页跳转「即将推出」），"
                "暂无可索引地址；上游补充后会自动出现。")
    else:
        return []
    return ["", f"    - {line}"]



def platform_page(dev, doc, catalog):
    versions = page_sorted(dev, doc.get("versions") or [])
    majors = OrderedDict()
    for v in versions:
        majors.setdefault(major_for(dev, v["v"]), []).append(v)
    ordered_majors = sorted(majors, key=lambda m: [int(n) for n in re.findall(r"\d+", m)[:3]], reverse=True)
    bd = dev == "stardew"
    rel = [x for x in [newest(versions, True, bd)] if x]
    test = [x for x in [newest(versions, False, bd)] if x]
    name = DEVICES[dev]

    arch_rows = "\n".join(f"| `{a}` | {note} |" for a, note in ARCH_NOTE[dev])
    chips = "\n".join(
        f'[{m}](../download/{dev}/{slug(m)}.md){{ .md-button .button-all }}' for m in ordered_majors
    )
    generated = (catalog.get("generated") or "")[:16].replace("T", " ")

    intro = {
        "java": "收录 Mojang 官方 Piston 清单的全部 Java 版版本：正式版、快照、远古版本，"
                "每个版本给出官方 client.jar / server.jar 直链与 SHA-1，并附 BMCLAPI、"
                "FastMirror 无极镜像两条国内加速线路。",
        "android": "汇总多个公开来源的 Android 安装包版本，同一版本按来源与网盘并列。",
        "ios": "收录多个公开来源的 iOS 安装包（IPA，含完整目录分享），"
               "iOS 安装需自签或企业证书，本站只提供上游链接。",
        "windows": "汇总多个公开来源的 Windows 安装包："
                   "GDK 提供 msixvc 原始包直链，UWP 只能给 UpdateID 与版本页。",
        "stardew": "收录星露谷版本库（Spectrollay）的 Android 版安装包：原版 apks 与去签名重打包 apk 两套，"
                   "覆盖 armv7 / armv8 / x86 / x86_64，下载走版本库官方网盘目录。",
    }[dev]

    if dev == "stardew":
        notice = ("    - 本站只索引公开下载源，不托管游戏文件，与 ConcernedApe / 星露谷版本库无隶属关系。\n"
                  "    - 安装包版权归 ConcernedApe 与发行方，请通过 Google Play / Steam 等官方渠道支持正版。")
        mirror_hint = "    - 下载地址来自星露谷版本库的官方网盘目录（Huang1111 Drive），失效时请到上游目录另取。"
    else:
        notice = ("    - 本站只索引公开下载源，不托管游戏文件，与 Mojang / Microsoft 无隶属关系。\n"
                  "    - 安装包版权归 Mojang / Microsoft，请支持正版。")
        mirror_hint = "    - 网盘链接由上游提供，若失效请在该版本的其它来源中另选。"

    rel_md = "\n\n".join(version_block(v, sibs=sibling_versions(v, versions, dev)) for v in rel) or "_暂无数据_"
    test_md = "\n\n".join(version_block(v, sibs=sibling_versions(v, versions, dev)) for v in test) or "_暂无数据_"

    game_key = "stardew" if dev == "stardew" else "minecraft"
    h1 = "星露谷物语 · Android" if dev == "stardew" else "Minecraft " + name
    body = f'''# {h1} 版本库

[← {GAMES[game_key]["title"]} 专题](index.md)

{intro}

!!! note "公告"

    - 数据每 6 小时自动同步一次，最后同步：{generated} UTC。
{mirror_hint}

???+ warning "注意"

{notice}

## 最新版本

{tabs([
    ("最新正式版", rel_md),
    ("最新测试版", test_md),
    ("全版本", chips),
])}

## 架构说明

| 架构 | 适用情况 |
| --- | --- |
{arch_rows}
'''
    out = DOCS / PLATFORM_PATH[dev]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")

    for m, arr in majors.items():
        d = DOCS / "download" / dev
        d.mkdir(parents=True, exist_ok=True)
        blocks = "\n\n".join(version_block(v, open_=(i == 0), sibs=sibling_versions(v, arr, dev))
                             for i, v in enumerate(arr))
        order_note = ("按上游发布顺序（新 → 旧）" if dev == "stardew"
                      else "按版本号倒序" if dev in BEDROCK_DEVS
                      else "按发布时间倒序")
        page = f'''# {name} · {m}

共 **{len(arr)}** 个版本，{order_note}。

{blocks}
'''
        (d / f"{slug(m)}.md").write_text(page, encoding="utf-8")
    return len(versions), len(majors)


def game_page(gkey, catalog):
    """游戏专题页：平台入口 + 该游戏最新版本 + 数据来源。"""
    g = GAMES[gkey]
    devs = catalog.get("devices") or {}
    lines = [f"# {g['title']}", ""]
    if gkey == "minecraft":
        lines += ["Minecraft 由 Mojang 开发。这里把 **Java 版**与**基岩版（Android / iOS / Windows）**的公开版本库汇到一处："
                  "官方 Piston 清单 + 十余个公开来源，按平台与大版本归档，同一版本并列多个下载来源。", ""]
    else:
        lines += ["《星露谷物语》（Stardew Valley）Android 版全版本归档：原版 apks（按架构拆分）与去签名重打包 apk 两套，"
                  "下载走版本库官方 Huang1111 Drive 目录。", ""]
    lines += ['???+ tip "平台入口"', ""]
    for dev in g["devices"]:
        d = devs.get(dev) or {}
        fn = PLATFORM_PATH[dev].split("/", 1)[1]
        lines.append(f'    - [{PLATFORM_LABEL[dev]}（{d.get("count", 0)} 个版本）]({fn})')
    if g.get("extra"):
        lines.append(f'    - [{g["extra"][0]}](../{g["extra"][1]})')
    lines += ["", "## 最新版本", "",
              "| 平台 | 最新正式版 | 发布日期 | 体积 | 入口 |",
              "| --- | --- | --- | --- | --- |"]
    for dev in g["devices"]:
        doc = load(dev) or {}
        vs = page_sorted(dev, doc.get("versions") or [])
        r = newest(vs, True, dev == "stardew")
        fn = PLATFORM_PATH[dev].split("/", 1)[1]
        if r:
            lines.append(f'| {PLATFORM_LABEL[dev]} | `{r["v"]}` | {r.get("released") or "—"} | '
                         f'{fmt_size(r.get("size")) or "—"} | [进入]({fn}) |')
    lines += [""]
    out = DOCS / gkey / "index.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


def home_page(catalog):
    devs = catalog.get("devices") or {}
    lines = ["# 游戏版本库", "",
             "把 Minecraft（Java 版、基岩版 Android/iOS/Windows）与星露谷物语的公开版本清单汇成一个目录："
             "按平台与大版本归档，同一个版本并列显示多个下载来源。", "",
             '!!! note "公告"', "",
             "    - 数据每 6 小时自动同步，网盘失效请换其它来源。",
             "    - 本站只做索引，不存储游戏文件。", ""]
    lines += ['???+ tip "游戏入口"', ""]
    for gkey, g in GAMES.items():
        total = sum((devs.get(d) or {}).get("count", 0) for d in g["devices"])
        lines.append(f'    - [{g["title"]}（{len(g["devices"])} 个平台 · {total} 个版本）]({gkey}/)')
    lines += [""]
    (DOCS / "index.md").write_text("\n".join(lines), encoding="utf-8")


def spectro_coverage_section():
    cov = spectro_platforms()
    plats = cov.get("platforms") or {}
    if not plats:
        return ""
    names = {"android": "Android", "ios": "iOS", "windows": "Windows", "linux": "Linux"}
    rows = ["| 平台 | 分支目录 | 有链接分支 | 分享链接 | 空占位 | 状态 |",
            "| --- | ---: | ---: | ---: | ---: | --- |"]
    for dev, info in plats.items():
        if not info.get("branches"):
            continue
        state = "已收录" if info.get("links") else "上游未开放"
        rows.append(f'| {names.get(dev, dev)} | {info.get("branches")} | {info.get("branches_with_links")} '
                    f'| {info.get("links")} | {info.get("blank_links")} | {state} |')
    eds = cov.get("editions") or {}
    ed_names = {"bedrock": "基岩版", "java": "Java 版", "education": "教育版", "trial": "试玩版",
                "server": "服务端", "story_mode": "故事模式", "earth": "Earth", "dungeons": "Dungeons",
                "legends": "Legends"}
    def _ed_state(v):
        return " 已公开" if v else (" 未公开" if v is False else " 探测失败")
    ed_bits = "、".join(f'{ed_names.get(k, k)}{_ed_state(v)}' for k, v in eds.items())
    gen = (cov.get("generated") or "")[:16].replace("T", " ")
    return f'''## 星月版本库覆盖明细

星月（Spectrollay）把下载条目按「分支 → 平台 → 网盘」组织，Android 与 iOS 已给出真实分享链接，
Windows 只先建了条目、四个网盘位置全为空链接（其站内 Windows 页直接跳「即将推出」），因此本站在 Windows
页只展示其他来源，等上游补链后自动收录。

{"".join(chr(10) + r for r in rows)}

- 版本库发布状态（探测 `*_versions.json`）：{ed_bits}。
- 探测时间：{gen} UTC；数据入口 `{cov.get("source", "")}`。
'''


def sources_page(catalog):
    ext = "\n".join(f'- [{esc(s["name"])}]({s["homepage"]}) — {esc(s.get("note", ""))}'
                    for s in catalog.get("external") or [])
    body = f'''# 来源与致谢

本站只做版本索引：下载文件全部由上游站点提供，本站不存储游戏文件。

## 编程接入的来源

### Minecraft

{source_table(catalog, game="Minecraft")}

### 星露谷物语

{source_table(catalog, game="星露谷物语")}

## 外链来源（不抓取）

{ext}

## 版本库覆盖明细

### Minecraft

| 来源 | 抓取方式 | 处理 |
| --- | --- | --- |
| MineBBS · MCDL | API + 短链即时解析 | 收录（短链 token 只在同步时有效，存解析后的网盘地址） |
| MCAPKS | 开放 API | 收录 |
| MCAPPX | JSON + 656 个版本页 | 收录（123云盘 / 日美加速节点） |
| 星月版本库 | JSONC 静态 | 收录 Android / iOS 整分支目录分享；Windows 上游只有占位空链 |
| MCARC / zihao 动态库 | POST API | 收录 |
| mc233 / zihao 静态站 | — | 与 MCARC 同源，不重复收录 |
| mcarc.github.io（旧版归档） | 网页抓取 | 收录（0.1–1.20 旧版，OneDrive 直链） |
| NullAtom 原子资源 | POST API + repo 直链 | 收录（iOS IPA / UWP / Android） |
| 甘泉版本库 | 网页抓取 | 收录；API 需合作伙伴令牌 |
| 小布のMC版本库 | API | 收录 |
| MCPELIFE | 网页抓取 | 收录 |
| MCVersions | — | Java 官方镜像，Mojang/BMCLAPI 已覆盖 |
| Omniarchive | — | 归档目录，无版本包直链，仅外链 |
| MCArchive | — | 模组归档 API，非游戏版本库 |
| BMCLAPI | API | 原版镜像 + 模组加载器（Forge/NeoForge/OptiFine/LiteLoader） |
| FastMirror 无极镜像 | API v3 | 收录 Vanilla 服务端 JAR（国内加速，逐版本大小/抽样 sha1 与官方一致才上线） |
| Fabric（FabricMC meta） | API v2 | 收录加载器与安装器（官方 maven 直链） |
| Quilt（QuiltMC meta） | API v3 | 收录加载器与安装器（官方 maven 直链） |

### 星露谷物语

| 来源 | 抓取方式 | 处理 |
| --- | --- | --- |
| 星露谷版本库 | JSONC 静态 | 收录（Android 原版 apks / 重打包 apk，Huang1111 Drive 目录分享） |

{spectro_coverage_section()}
## 同步机制

- 每 6 小时执行一次全量同步，缓存上游响应，失败时保留上次快照。
- 版本号统一归一化（如 `1.26.70.21 → 26.70.21`），并按平台/渠道/架构保留来源。
- 正式版 / 测试版归类以 [Minecraft Wiki 版本历史](https://minecraft.wiki/w/Bedrock_Edition_version_history) 交叉校验：Wiki 明确的 Preview/Beta 不会留在正式版；上游原始标记保留在 `meta.upstream_channel`。
'''
    (DOCS / "sources.md").write_text(body, encoding="utf-8")


def about_page(catalog):
    g = (catalog.get("generated") or "")[:16].replace("T", " ")
    body = f'''# 关于本站

这是一个**游戏版本库**：把公开可访问的版本库汇总成「按平台 / 大版本 / 版本」的目录。
目前收录 Minecraft（Java 版、基岩版 Android/iOS/Windows）与星露谷物语（Android）。

本站**不是** Mojang、Microsoft、MineBBS、MCAPKS、MCAPPX、星月或星露谷版本库的官方站点，与上述主体无隶属关系。

## 数据更新

- 最后同步：{g} UTC
- 同步周期：每 6 小时
- 失败策略：保留上次成功快照，页面标注来源状态

## 免责声明

- 本站不存储、不上传任何游戏文件，全部下载链接指向原始上游。
- 请支持正版：https://www.minecraft.net/ ／ https://www.stardewvalley.net/
'''
    (DOCS / "about.md").write_text(body, encoding="utf-8")


def load(dev):
    f = DATA / f"{dev}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def resources_pages():
    """BMCLAPI resource lists: Forge / NeoForge / OptiFine / LiteLoader."""
    p = DATA / "resources.json"
    if not p.exists():
        return
    r = json.loads(p.read_text(encoding="utf-8"))
    forge, neo = r.get("forge") or [], r.get("neoforge") or []
    optifine, ll = r.get("optifine") or [], r.get("liteloader") or []
    base = DOCS / "resources"
    (base / "forge").mkdir(parents=True, exist_ok=True)
    (base / "neoforge").mkdir(parents=True, exist_ok=True)

    def group(items):
        g = OrderedDict()
        for it in items:
            g.setdefault(it.get("mcversion") or "其他", []).append(it)
        return g

    def mc_slug(mc):
        return re.sub(r"[^0-9A-Za-z._-]", "-", str(mc))

    def by_ver(items):
        return sorted(items, key=lambda x: (version_tuple(x.get("version")), x.get("build") or 0), reverse=True)

    fg, ng = group(forge), group(neo)

    # ---- index
    fabric = r.get("fabric") or {}
    quilt = r.get("quilt") or {}
    fab_ld, fab_in = fabric.get("loader") or [], fabric.get("installer") or []
    qui_ld, qui_in = quilt.get("loader") or [], quilt.get("installer") or []
    lines = ["# 模组加载器", "",
             "模组加载器、优化补丁与安装器。Forge / NeoForge / OptiFine / LiteLoader 走 BMCLAPI 镜像，"
             "Fabric / Quilt 走各自官方 maven。", "",
             "| 资源 | 条目 | 说明 |", "| --- | ---: | --- |",
             f"| [Fabric](fabric/index.md) | {len(fab_ld)} | {len(fab_in)} 个安装器；加载器与 MC 版本解耦 |",
             f"| [Quilt](quilt/index.md) | {len(qui_ld)} | {len(qui_in)} 个安装器；兼容 Fabric 模组 |",
             f"| [Forge](forge/index.md) | {len(forge)} | {len(fg)} 个 MC 版本 |",
             f"| [NeoForge](neoforge/index.md) | {len(neo)} | {len(ng)} 个 MC 版本 |",
             f"| [OptiFine](optifine.md) | {len(optifine)} | 各 MC 版本 HD_U / preview |",
             f"| [LiteLoader](liteloader.md) | {len(ll)} | 1.5.2–1.12.2（已停止维护） |", "",
             "!!! note \"说明\"", "",
             "    - Java 运行时列表（`/java/list`）当前返回 403，未收录。",
             "    - authlib-injector 镜像页：<https://bmclapi2.bangbang93.com/mirrors/authlib-injector>", "",
             f"_数据生成于 {r.get('generated', '')[:16].replace('T', ' ')} UTC_"]
    (base / "index.md").write_text("\n".join(lines), encoding="utf-8")

    # ---- forge index + per-mc
    fi = ["# Forge", "", f"共 **{len(forge)}** 个构建，覆盖 **{len(fg)}** 个 MC 版本。", "",
          "| MC 版本 | 构建数 | 最新 Forge |", "| --- | ---: | --- |"]
    for mc, arr in sorted(fg.items(), key=lambda kv: version_tuple(kv[0]), reverse=True):
        newest = by_ver(arr)[0]
        fi.append(f'| [{mc}]({mc_slug(mc)}.md) | {len(arr)} | `{newest["version"]}` |')
    (base / "forge" / "index.md").write_text("\n".join(fi), encoding="utf-8")
    for mc, arr in fg.items():
        rows = ["| Forge 版本 | Build | 下载 |", "| --- | ---: | --- |"]
        for it in by_ver(arr):
            rows.append(f'| `{it["version"]}` | {it.get("build") or "—"} | [下载 :material-download:]({it["url"]}){{ .md-button .button-all }} |')
        title = f"# Forge · {mc}\n\n共 **{len(arr)}** 个构建。\n\n" + "\n".join(rows) + "\n"
        (base / "forge" / f"{mc_slug(mc)}.md").write_text(title, encoding="utf-8")

    # ---- neoforge
    ni = ["# NeoForge", "", f"共 **{len(neo)}** 个版本，覆盖 **{len(ng)}** 个 MC 版本。", "",
          "| MC 版本 | 版本数 | 最新 NeoForge |", "| --- | ---: | --- |"]
    for mc, arr in sorted(ng.items(), key=lambda kv: version_tuple(kv[0]), reverse=True):
        ni.append(f'| [{mc}]({mc_slug(mc)}.md) | {len(arr)} | `{by_ver(arr)[0]["version"]}` |')
    (base / "neoforge" / "index.md").write_text("\n".join(ni), encoding="utf-8")
    for mc, arr in ng.items():
        rows = ["| NeoForge 版本 | 下载 |", "| --- | --- |"]
        for it in by_ver(arr):
            if it.get("url"):
                rows.append(f'| `{it["version"]}` | [下载 :material-download:]({it["url"]}){{ .md-button .button-all }} |')
            else:
                rows.append(f'| `{it["version"]}` | — |')
        page = f"# NeoForge · {mc}\n\n共 **{len(arr)}** 个版本。\n\n" + "\n".join(rows) + "\n"
        (base / "neoforge" / f"{mc_slug(mc)}.md").write_text(page, encoding="utf-8")

    # ---- optifine
    of = ["# OptiFine", "", f"共 **{len(optifine)}** 个文件。", "",
          "| MC 版本 | 类型 | 补丁 | 文件 | 下载 |", "| --- | --- | --- | --- | --- |"]
    for it in sorted(optifine, key=lambda x: (version_tuple(x.get("mcversion")), x.get("patch") or ""), reverse=True):
        of.append(f'| {it["mcversion"]} | {it["type"]} | {it["patch"]} | `{it["filename"]}` | '
                  f'[下载 :material-download:]({it["url"]}){{ .md-button .button-all }} |')
    (base / "optifine.md").write_text("\n".join(of), encoding="utf-8")

    # ---- liteloader
    lls = ["# LiteLoader", "", f"共 **{len(ll)}** 个构建。", "",
           "| MC 版本 | 版本 | 文件 | 下载 |", "| --- | --- | --- | --- |"]
    for it in sorted(ll, key=lambda x: version_tuple(x.get("mcversion")), reverse=True):
        lls.append(f'| {it["mcversion"]} | `{it["version"]}` | `{it["file"]}` | '
                   f'[下载 :material-download:]({it["url"]}){{ .md-button .button-all }} |')
    (base / "liteloader.md").write_text("\n".join(lls), encoding="utf-8")

    # ---- fabric / quilt（官方 meta：加载器 + 安装器；加载器与 MC 版本解耦）
    for key, data, note in (
        ("fabric", fabric, "Fabric 加载器不绑定具体 MC 版本：同一加载器版本可用于其支持的所有 MC 版本，"
                           "安装时由 Fabric Installer 生成对应版本的启动配置。"),
        ("quilt", quilt, "Quilt 兼容 Fabric 模组；加载器同样与 MC 版本解耦，需用 Quilt Installer 安装。"),
    ):
        ld, inst = data.get("loader") or [], data.get("installer") or []
        if not ld and not inst:
            continue
        d = base / key
        d.mkdir(parents=True, exist_ok=True)
        name = data.get("name") or key.title()
        rows = ["| 加载器版本 | 稳定性 | 下载 |", "| --- | --- | --- |"]
        for it in sorted(ld, key=lambda x: version_tuple(x.get("version")), reverse=True):
            stable = "稳定" if it.get("stable") else "测试"
            url = it.get("url") or ""
            btn = f"[下载 :material-download:]({url}){{ .md-button .button-all }}" if url else "—"
            rows.append(f'| `{it.get("version")}` | {stable} | {btn} |')
        irows = ["| 安装器版本 | 稳定性 | 下载 |", "| --- | --- | --- |"]
        for it in sorted(inst, key=lambda x: version_tuple(x.get("version")), reverse=True):
            stable = "稳定" if it.get("stable") else "测试"
            url = it.get("url") or ""
            btn = f"[下载 :material-download:]({url}){{ .md-button .button-all }}" if url else "—"
            irows.append(f'| `{it.get("version")}` | {stable} | {btn} |')
        page = (f"# {name}\n\n{note}\n\n"
                f"数据来源：{data.get('homepage', '')}（官方 meta 接口 `{data.get('api', '')}`）。\n\n"
                f"## 加载器（{len(ld)}）\n\n" + "\n".join(rows) + "\n\n"
                f"## 安装器（{len(inst)}）\n\n" + "\n".join(irows) + "\n")
        (d / "index.md").write_text(page, encoding="utf-8")
    print(f"resources: forge={len(forge)} neoforge={len(neo)} optifine={len(optifine)} "
          f"liteloader={len(ll)} fabric={len(fab_ld)} quilt={len(qui_ld)}")


def main():
    DOCS.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.rmtree(DOCS / "download", ignore_errors=True)   # drop stale major pages
    catalog = json.loads((DATA / "catalog.json").read_text(encoding="utf-8"))
    for stale in ("java.md", "android.md", "ios.md", "windows.md", "stardew.md"):
        (DOCS / stale).unlink(missing_ok=True)          # 旧平铺路径已移到 <游戏>/ 下
    for gkey in GAMES:
        game_page(gkey, catalog)
    for dev in DEVICES:
        doc = load(dev)
        if not doc:
            continue
        n, m = platform_page(dev, doc, catalog)
        print(f"{dev}: {n} versions, {m} majors")
    home_page(catalog)
    sources_page(catalog)
    about_page(catalog)
    resources_pages()
    # favicon (already committed under docs/img; only copy if a source exists elsewhere)
    img = DOCS / "img"
    img.mkdir(exist_ok=True)
    src = ROOT / "docs" / "img" / "favicon.svg"
    if src.exists():
        print("favicon: docs/img/favicon.svg")
    else:
        print("favicon: missing docs/img/favicon.svg")
    print("generated mkdocs pages")


if __name__ == "__main__":
    main()
