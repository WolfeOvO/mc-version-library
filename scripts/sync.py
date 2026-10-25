#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Minecraft version library — multi-source sync.

Sources: Mojang Piston, MineBBS MCDL, MCAPKS, MCAPPX, Spectrollay.
Writes data/<device>.json + data/catalog.json. Raw responses cached under cache/.
"""
import argparse
import gzip
import json
import os
import re
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlparse, quote

import requests

BASE = Path(__file__).resolve().parent.parent
CACHE = BASE / "cache"
DATA = BASE / "data"
LOG = BASE / "logs" / "sync.log"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"})

CACHE.mkdir(parents=True, exist_ok=True)
DATA.mkdir(parents=True, exist_ok=True)
LOG.parent.mkdir(parents=True, exist_ok=True)

ALL_SOURCES = ["mojang", "bmclapi", "fastmirror", "mcdl", "mcapks", "mcappx", "spectro", "mcarc",
               "xiaobu", "ganquan", "mcpelife", "nullatom", "mcarc_static", "stardew"]

DEVICES = {
    "java": "Minecraft Java 版",
    "android": "Minecraft 基岩版 · Android",
    "ios": "Minecraft 基岩版 · iOS",
    "windows": "Minecraft 基岩版 · Windows",
    "stardew": "星露谷物语 · Android",
}

PAN_PROVIDERS = {
    "pan.quark.cn": "夸克网盘",
    "pan.baidu.com": "百度网盘",
    "www.123pan.com": "123云盘",
    "123pan.com": "123云盘",
    "123684.com": "123云盘",
    "drive.uc.cn": "UC 网盘",
    "pan.xunlei.com": "迅雷网盘",
    "pan.huang1111.cn": "Huang1111 Drive",
    "cloud.nyan.run": "猫盘",
    "nyan.run": "猫盘",
    "cloud.189.cn": "天翼云盘",
    "caiyun.139.com": "移动云盘",
    "www.aliyundrive.com": "阿里云盘",
    "pan.aliyundrive.com": "阿里云盘",
    "mypikpak.com": "PikPak",
    "1drv.ms": "OneDrive",
    "onedrive.live.com": "OneDrive",
}
DIRECT_EXTS = (".apk", ".apks", ".zip", ".jar", ".exe", ".msixvc", ".appx",
               ".appxbundle", ".msix", ".ipa", ".tar.gz", ".7z", ".rar")


def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def http_json(url, cache_key=None, max_age=0, method="GET", data=None,
              headers=None, timeout=45, retries=3):
    """GET/POST JSON with optional disk cache. cache_key is relative to cache/."""
    if cache_key:
        p = CACHE / cache_key
        if p.exists() and max_age > 0 and (time.time() - p.stat().st_mtime) < max_age:
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    last = None
    for attempt in range(retries):
        try:
            r = S.request(method, url, data=data, headers=headers, timeout=timeout)
            r.raise_for_status()
            j = r.json()
            if cache_key:
                p = CACHE / cache_key
                p.parent.mkdir(parents=True, exist_ok=True)
                tmp = p.with_suffix(".tmp")
                tmp.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
                tmp.replace(p)
            return j
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"{url}: {last}")


def curl_html(url, timeout=40, compressed=False):
    """Fetch HTML via system curl: mcappx.com rejects Python TLS fingerprints with a CF challenge."""
    import subprocess
    try:
        cmd = ["curl", "-sL", "-A", UA, "--max-time", str(timeout)]
        if compressed:
            cmd.append("--compressed")
        cmd.append(url)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def strip_jsonc(s):
    out = []
    i, n = 0, len(s)
    instr = False
    while i < n:
        c = s[i]
        if instr:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                instr = False
            i += 1
            continue
        if c == '"':
            instr = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "/":
            while i < n and s[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and s[i + 1] == "*":
            i += 2
            while i + 1 < n and not (s[i] == "*" and s[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def norm_ver(v):
    v = str(v).strip()
    parts = v.split(".")
    if len(parts) >= 2 and parts[0] == "1" and parts[1].isdigit() and int(parts[1]) >= 22:
        return ".".join(parts[1:])
    return v


def game_ver(v):
    """1.21.12020.0 -> 1.21.120.20 (MCDL Windows package id)."""
    m = re.match(r"^1\.(\d+)\.(\d{3,})\.\d+$", str(v).strip())
    if m:
        return f"1.{m.group(1)}.{int(m.group(2)) // 100}.{int(m.group(2)) % 100}"
    return norm_ver(v)


def major_of(v):
    v = str(v)
    if re.match(r"^[ab]\d", v, re.I):
        return "远古版本"
    m = re.match(r"^(\d+)\.(\d+)", v)
    if not m:
        return "其他"
    # 基岩版 26.x 起改成两段编号（26.0 / 26.10 / 26.70…），整代合成一个「26.x」大版本
    if m.group(1) == "26":
        return "26.x"
    return f"{m.group(1)}.{m.group(2)}"


def major_for(dev, v):
    """星露谷只有 1.x 一代（老版本 1.00–1.33 编号混乱），整代并成一页。"""
    return "1.x" if dev == "stardew" else major_of(v)


def sort_key(v):
    parts = re.findall(r"\d+", str(v))
    return [int(x) for x in parts[:5]] + [0] * (5 - len(parts[:5]))


def classify_url(url):
    host = (urlparse(url).hostname or "").lower()
    path = (urlparse(url).path or "").lower()
    provider = PAN_PROVIDERS.get(host)
    if not provider:
        if host.endswith("123pan.cn") or host.endswith("123pan.com") or \
           host.endswith("123912.com") or host.endswith("123684.com") or host.endswith("123pan.cc"):
            provider = "123云盘"
        elif host.endswith("sharepoint.cn"):
            provider = "OneDrive 世纪互联"
        elif host.endswith("sharepoint.com") or host.endswith("1drv.ms"):
            provider = "OneDrive"
        elif host == "www.guangyapan.com":
            provider = "光鸭网盘"
        elif host.endswith("xboxlive.cn") or host.endswith("xboxlive.com"):
            provider = "Xbox CDN"
        elif "openlist" in host or "endyun" in host:
            provider = "直链"
        elif host:
            provider = host
        else:
            provider = "下载"
    kind = "direct" if path.endswith(DIRECT_EXTS) else "share"
    return provider, kind


def offer(source, provider, url, **kw):
    o = {
        "source": source,
        "provider": provider,
        "kind": kw.pop("kind", "share"),
        "url": url,
        "arch": kw.pop("arch", ""),
        "size": kw.pop("size", None),
        "checksum_type": kw.pop("checksum_type", ""),
        "checksum": kw.pop("checksum", ""),
        "scope": kw.pop("scope", "file"),
        "note": kw.pop("note", ""),
        "password": kw.pop("password", ""),
    }
    o.update(kw)
    return o


def add_offer(ver, o):
    key = (o["source"], o["provider"], o["arch"], o["kind"], o["url"], o["note"])
    seen = ver.setdefault("_seen", set())
    if key in seen:
        return
    seen.add(key)
    ver["offers"].append(o)


def merge_version(store, device, version, channel, released, source, notes="", meta=None):
    key = version
    if key not in store:
        store[key] = {
            "v": version,
            "device": device,
            "channel": channel,
            "channels": [channel],
            "released": released or "",
            "notes": notes or "",
            "meta": meta or {},
            "sources": [],
            "offers": [],
            "_seen": set(),
        }
    v = store[key]
    prio = {"release": 6, "beta": 5, "preview": 4, "snapshot": 3, "old": 2, "directory": 1}
    if prio.get(channel, 0) > prio.get(v["channel"], 0):
        v["channel"] = channel
    if channel not in v["channels"]:
        v["channels"].append(channel)
    if released and (not v["released"] or released > v["released"]):
        v["released"] = released
    if not v["notes"] and notes:
        v["notes"] = notes
    if source not in v["sources"]:
        v["sources"].append(source)
    if meta:
        v["meta"].setdefault(source, dict(meta))
    return v


# ---------------------------------------------------------------- Mojang
def sync_mojang():
    log("mojang: manifest")
    man = http_json("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json",
                    cache_key="mojang/manifest.json", max_age=1800)
    versions = man["versions"]
    log(f"mojang: {len(versions)} versions, fetching per-version JSON (cached)")

    def fetch(item):
        key = "mojang/" + re.sub(r"[^0-9A-Za-z._-]", "_", item["id"]) + ".json"
        try:
            return item, http_json(item["url"], cache_key=key, max_age=7 * 86400, retries=2)
        except Exception as e:  # noqa: BLE001
            return item, {"_error": str(e)}

    store = {}
    ch_map = {"release": "release", "snapshot": "snapshot",
              "old_beta": "old", "old_alpha": "old"}
    ch_name = {"release": "正式版", "snapshot": "快照", "old_beta": "旧版 Beta", "old_alpha": "旧版 Alpha"}
    done = 0
    with ThreadPoolExecutor(max_workers=16) as ex:
        futs = [ex.submit(fetch, it) for it in versions]
        for fut in as_completed(futs):
            item, detail = fut.result()
            done += 1
            if done % 200 == 0:
                log(f"mojang: {done}/{len(versions)}")
            ch = ch_map.get(item["type"], "release")
            v = merge_version(store, "java", item["id"], ch,
                              (item.get("releaseTime") or "")[:10], "mojang",
                              notes="",
                              meta={"type": item.get("type"), "manifest": item["url"]})
            if "_error" in detail:
                v["meta"]["mojang_error"] = detail["_error"][:200]
                continue
            dl = detail.get("downloads") or {}
            for art in ("client", "server"):
                d = dl.get(art)
                if not d or not d.get("url"):
                    continue
                label = {"client": "客户端 JAR", "server": "服务端 JAR"}[art]
                add_offer(v, offer("mojang", "Mojang 官方 · " + label, d["url"], kind="direct",
                                   size=d.get("size"), checksum_type="sha1",
                                   checksum=d.get("sha1", ""), scope="file",
                                   note="官方 " + ch_name.get(item["type"], item["type"]),
                                   artifact=art))
                mirror = f"https://bmclapi2.bangbang93.com/version/{quote(item['id'])}/{art}"
                add_offer(v, offer("bmclapi", "BMCLAPI 镜像 · " + label, mirror, kind="direct",
                                   size=d.get("size"), checksum_type="sha1",
                                   checksum=d.get("sha1", ""), scope="file",
                                   note="BMCLAPI 镜像（国内加速）", artifact=art))
    log(f"mojang: {len(store)} versions")
    return "java", store


# ---------------------------------------------------------- FastMirror 无极镜像
FASTMIRROR_API = "https://download.fastmirror.net/api/v3"


def fm_vanilla_list():
    j = http_json(FASTMIRROR_API + "/Vanilla", cache_key="fastmirror/vanilla.json",
                  max_age=24 * 3600)
    return set(((j.get("data") or {}).get("mc_versions") or []))


FM_PACE = 0.9          # 全局节流：≈66 req/min，低于上游 200 req/2.5min 的配额
_fm_next = [0.0]
_fm_lock = threading.Lock()


def fm_pace():
    with _fm_lock:
        now = time.time()
        if _fm_next[0] > now:
            time.sleep(_fm_next[0] - now)
            now = _fm_next[0]
        _fm_next[0] = now + FM_PACE


def fm_detail(ver, attempts=5):
    """单版本构建详情（sha1 + 直链），带 30 天缓存与配额退避。

    上游超限时返回 HTTP 403 + `err::permission::request_limit`（不是 429），
    必须显式识别并等窗口重置，否则会把整批版本当成「没有构建」。
    """
    key = "fastmirror/" + re.sub(r"[^0-9A-Za-z._-]", "_", str(ver)) + ".json"
    p = CACHE / key
    if p.exists() and (time.time() - p.stat().st_mtime) < 30 * 86400:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    url = f"{FASTMIRROR_API}/Vanilla/{quote(str(ver))}/release"
    for attempt in range(attempts):
        fm_pace()
        try:
            r = S.get(url, timeout=30)
        except Exception:  # noqa: BLE001
            time.sleep(3 + 2 * attempt)
            continue
        body = r.text or ""
        if r.status_code in (403, 429) or "request_limit" in body:
            log(f"fastmirror: quota hit ({r.status_code}), cooling down 60s")
            time.sleep(60)
            continue
        if r.status_code >= 400:
            time.sleep(2 + attempt)
            continue
        try:
            j = r.json()
        except Exception:  # noqa: BLE001
            time.sleep(2)
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(j, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
        return j
    return None


FM_CDN_PACE = 0.4        # CDN HEAD 全局节流：≈150 req/min（burst 会 403）
_fm_cdn_next = [0.0]
_fm_cdn_lock = threading.Lock()
FM_VERIFIED = CACHE / "fastmirror" / "verified.json"


def fm_cdn_pace():
    with _fm_cdn_lock:
        now = time.time()
        if _fm_cdn_next[0] > now:
            time.sleep(_fm_cdn_next[0] - now)
            now = _fm_cdn_next[0]
        _fm_cdn_next[0] = now + FM_CDN_PACE


def load_fm_verified():
    if FM_VERIFIED.exists():
        try:
            return json.loads(FM_VERIFIED.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_fm_verified(d):
    FM_VERIFIED.parent.mkdir(parents=True, exist_ok=True)
    tmp = FM_VERIFIED.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.replace(FM_VERIFIED)


def fm_head(ver, kinds=("release", "snapshot"), attempts=2):
    """HEAD 确定性下载地址，拿 content-length / filename（CDN 侧，不消耗 API 配额）。

    路径里最后一段是构建类型：正式版在 `/release`，快照/预发布在 `/snapshot`
    （`26.4-snapshot-3/release` → 404，`…/snapshot` → 200），两个都要试。
    必须走系统 curl：requests 的共享 Session 多线程打这个 CDN 会大量失败
    （604 条只剩 10 条），curl 稳定。
    """
    import subprocess
    for kind in kinds:
        hit = _fm_head_kind(ver, kind, attempts)
        if hit:
            return hit
    return None


def _fm_head_kind(ver, kind, attempts=2):
    import subprocess
    url = f"https://download.fastmirror.net/download/Vanilla/{quote(str(ver))}/{kind}"
    for a in range(attempts + 3):
        fm_cdn_pace()
        try:
            out = subprocess.run(["curl", "-sI", "-A", UA, "-m", "25", url],
                                 capture_output=True, text=True, timeout=40).stdout
        except Exception:  # noqa: BLE001
            time.sleep(1 + a)
            continue
        head, _, rest = out.partition("\r\n\r\n")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        if not lines:
            time.sleep(1 + a)
            continue
        status = lines[0].split()[1] if len(lines[0].split()) > 1 else ""
        if status in ("403", "429"):
            # CDN 也会限流（不是 404）：冷却后重试，别当成「上游没有这个构建」
            time.sleep(25)
            continue
        if status == "200":
            size = 0
            name = ""
            for ln in lines[1:]:
                low = ln.lower()
                if low.startswith("content-length:"):
                    try:
                        size = int(ln.split(":", 1)[1].strip())
                    except ValueError:
                        size = 0
                elif low.startswith("content-disposition:"):
                    m = re.search(r"filename=([^;]+)", ln)
                    if m:
                        name = m.group(1).strip().strip('"')
            return url, size, name
        if status == "404":
            return None
        time.sleep(1 + a)
    return None


def attach_fastmirror(java_store, workers=8):
    """无极镜像（FastMirror）Vanilla 服务端 JAR 国内镜像。

    收录判定（fail-closed）：对每个版本 HEAD 确定性地址
    `https://download.fastmirror.net/download/Vanilla/<v>/release`，要求
    HTTP 200 且 `content-length` 与 Mojang manifest 里 `server.size` 完全相等
    （字节数一致）；不一致或取不到就整条丢弃。

    为什么不用详情 API 的 sha1：`download.fastmirror.net/api/v3` 有硬配额
    （200 req / 窗口，超限回 403 `err::permission::request_limit`），全量 604 个
    版本跑不完；HEAD 走 CDN 不占配额，10 并发 604 条约 3 分钟。sha1 一致性另行
    抽样验证（26.3 / 26.2 / 1.21.11 实测与官方逐字节相同），因此这里只承诺
    「大小与官方一致」，不写 checksum。
    """
    avail = fm_vanilla_list()
    if not avail:
        raise RuntimeError("empty vanilla list")
    targets = []
    for v in java_store.values():
        srv = next((o for o in v["offers"]
                    if o["source"] == "mojang" and o.get("artifact") == "server"), None)
        if srv is not None and v["v"] in avail:
            targets.append((v, srv))
    log(f"fastmirror: {len(targets)} candidate versions (upstream list {len(avail)})")
    done = 0
    mismatch = []
    missed = []

    verified = load_fm_verified()
    now = time.time()

    def one(pair):
        v, srv = pair
        c = verified.get(v["v"])
        if c and c.get("size") and (now - c.get("ts", 0)) < 7 * 86400:
            return v, srv, (c["url"], c["size"], c.get("name", ""))
        kinds = ("release", "snapshot") if v.get("channel") == "release" else ("snapshot", "release")
        hit = fm_head(v["v"], kinds)
        if hit:
            verified[v["v"]] = {"url": hit[0], "size": hit[1], "name": hit[2], "ts": now}
        return v, srv, hit

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for v, srv, hit in ex.map(one, targets):
            done += 1
            if done % 50 == 0:
                log(f"fastmirror: {done}/{len(targets)}")
                save_fm_verified(verified)   # 断点续跑：中途被打断也不丢已验结果
            if not hit:
                missed.append(v["v"])
                continue
            url, size, _name = hit
            want = srv.get("size") or 0
            if not size or not want or size != want:
                mismatch.append(f"{v['v']}({size}!={want})")
                continue
            add_offer(v, offer("fastmirror", "FastMirror 无极镜像 · 服务端 JAR",
                               url, kind="direct", size=size,
                               scope="file", note="无极镜像（国内加速，与官方字节大小一致）",
                               artifact="server"))
    save_fm_verified(verified)
    if mismatch:
        log(f"fastmirror: size mismatch {len(mismatch)} {mismatch[:6]}")
    if missed:
        log(f"fastmirror: {len(missed)} 个版本上游没有对应构建（release/snapshot 都 404），跳过")
    n = sum(1 for v in java_store.values()
            if any(o["source"] == "fastmirror" for o in v["offers"]))
    log(f"fastmirror: {n} versions mirrored")
    return n


# ---------------------------------------------------------------- MCDL
MCDL_CACHE = CACHE / "mcdl_redirects.json"


def load_mcdl_cache():
    if MCDL_CACHE.exists():
        try:
            return json.loads(MCDL_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_mcdl_cache(d):
    tmp = MCDL_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(MCDL_CACHE)


def resolve_mcdl(link, cache, lock, ttl=12 * 3600):
    """Resolve the redirect endpoint to the real pan URL (GET, no follow).

    Facts verified live:
      * Without `Referer: https://mc.minebbs.com/` the endpoint 302s to the homepage.
      * HEAD is useless here: it often answers 200 with no Location while GET 302s.
      * Bursts get soft-throttled: it bounces to the homepage for a few seconds.

    So we pace every request, and on a homepage bounce we retry with backoff before
    recording the link as dead. Dead marks expire after 12h (a link that looks dead
    during a throttle burst is usually fine moments later).
    """
    with lock:
        val = cache.get(link)
        if isinstance(val, str):
            if val.startswith("dead:"):
                try:
                    if time.time() - float(val[5:]) < ttl:
                        return ""
                except ValueError:
                    pass
            elif val:
                return val
    url = "https:" + link if link.startswith("//") else link
    final = ""
    for attempt in range(3):
        time.sleep(0.9)                      # never burst: soft throttle 302s to home
        try:
            r = S.get(url, headers={"Referer": "https://mc.minebbs.com/"},
                      allow_redirects=False, timeout=25)
            loc = r.headers.get("Location", "")
            if loc and "mc.minebbs.com" not in loc:
                final = loc
                break
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            time.sleep(6 * (attempt + 1))    # homepage bounce: let the throttle expire
        except Exception:
            time.sleep(2 * (attempt + 1))
    with lock:
        cache[link] = final if final else "dead:" + str(int(time.time()))
    return final



def mcdl_post(item):
    """MineBBS 原帖地址：用资源 id 拼（实测 200）。newsLinks 常指向 minecraft.wiki，不能当原帖。"""
    rid = item.get("id")
    if rid:
        return f"https://mc.minebbs.com/#/resources/{rid}"
    return (item.get("newsLinks") or "").strip()


def catpan_ok(url, cache, lock, ttl=7 * 86400):
    """猫盘（cloud.nyan.run）：分享页走 v4 share API 校验，文件直链用 HEAD。"""
    key = "verify:" + url
    with lock:
        val = cache.get(key)
        if isinstance(val, str) and ":" in val:
            state, _, ts = val.partition(":")
            try:
                if time.time() - float(ts) < ttl:
                    return state == "ok"
            except ValueError:
                pass
    ok = False
    try:
        m = re.search(r"cloud\.nyan\.run/s/([A-Za-z0-9]+)", url)
        if m:
            r = S.get(f"https://cloud.nyan.run/api/v4/share/info/{m.group(1)}", timeout=20)
            ok = r.status_code == 200 and r.json().get("code") == 0
        else:
            r = S.head(url, allow_redirects=False, timeout=20)
            ok = r.status_code in (200, 206, 302, 416)
    except Exception:  # noqa: BLE001
        ok = True                      # 网络抖动不要误杀
    with lock:
        cache[key] = ("ok:" if ok else "dead:") + str(int(time.time()))
    return ok


def sync_mcdl():
    log("mcdl: page 1")
    # tokens in downloadlinks are short-lived: always fetch fresh pages
    first = http_json("https://api.mc.minebbs.com/api/v1/versions/?page=1&pageSize=10",
                      cache_key="mcdl/page1.json", max_age=0)
    total_pages = int(first.get("totalPages") or 1)
    pages = {1: first}

    def fetch_page(n):
        url = f"https://api.mc.minebbs.com/api/v1/versions/?page={n}&pageSize=10"
        for attempt in range(5):
            try:
                r = S.get(url, timeout=45)
                ct = r.headers.get("content-type", "")
                if "json" in ct:
                    return r.json()
            except Exception:
                pass
            time.sleep(8 * (attempt + 1))   # upstream throttles bursts
        raise RuntimeError(f"mcdl page {n}: not JSON after retries")
    cache = {k: v for k, v in load_mcdl_cache().items() if k.startswith("verify:")}
    lock = __import__("threading").Lock()

    def resolve_page(j):
        links = []
        for it in j.get("data") or []:
            for l in it.get("downloadlinks") or []:
                if l and l not in links:
                    links.append(l)
        # Sequential on purpose: 3 parallel workers trip the upstream soft throttle,
        # which looks exactly like a dead share (302 to homepage).
        for l in links:
            resolve_mcdl(l, cache, lock)
        return len(links)

    done = resolve_page(first)
    for n in range(2, total_pages + 1):
        time.sleep(0.4)
        j = fetch_page(n)
        pages[n] = j
        done += resolve_page(j)
        save_mcdl_cache(cache)       # checkpoint every page: a killed run must not lose resolutions
        if n % 10 == 0:
            log(f"mcdl: page {n}/{total_pages}, resolved {done} links")
    save_mcdl_cache(cache)

    items = []
    for n in range(1, total_pages + 1):
        items.extend((pages.get(n) or {}).get("data") or [])
    log(f"mcdl: {len(items)} items")

    store = {"android": {}, "ios": {}, "windows": {}}
    plat_map = {1: "android", 2: "ios", 3: "windows"}
    for it in items:
        plat = it.get("platform")
        device = plat_map.get(plat)
        if not device:
            continue
        raw = str(it.get("version") or "").strip()
        if not raw:
            continue
        ver = norm_ver(game_ver(raw)) if device == "windows" else norm_ver(raw)
        ch = "release" if int(it.get("environment") or 0) == 1 else "beta"
        released = datetime.fromtimestamp(it["time"], timezone.utc).strftime("%Y-%m-%d") if it.get("time") else ""
        v = merge_version(store[device], device, ver, ch, released, "mcdl",
                          notes=it.get("abstract") or "",
                          meta={"raw": raw, "platform": plat,
                                "downloads": it.get("total_download_count"),
                                "size": it.get("size"),
                                "wiki": it.get("wikiLinks"), "official": it.get("officialLinks"),
                                "news": it.get("newsLinks"), "id": it.get("id")})
        for i, l in enumerate(it.get("downloadlinks") or [], 1):
            final = cache.get(l, "")
            if isinstance(final, str) and final.startswith("dead:"):
                final = ""
            if not final:
                post = mcdl_post(it)
                if post:
                    add_offer(v, offer("mcdl", "MineBBS 原帖", post, kind="page",
                                       note=f"MCDL 通道 {i} 未解析出网盘地址，转原帖"))
                continue
            provider, kind = classify_url(final)
            # 猫盘（cloud.nyan.run）分享经常整批失效：点开前先验一次，挂了就转原帖
            if provider == "猫盘" and not catpan_ok(final, cache, lock):
                post = mcdl_post(it)
                if post:
                    add_offer(v, offer("mcdl", "MineBBS 原帖", post, kind="page",
                                       note=f"MCDL 通道 {i} 猫盘链接已失效（v3 接口），转原帖"))
                continue
            # MCDL 的 redirect token 短时效，直接存解析后的网盘地址，点击时才不会退回首页
            add_offer(v, offer("mcdl", provider, final, kind=kind,
                               size=it.get("size"), scope="file",
                               note=f"MCDL 通道 {i}"))
        bds = it.get("bdsLinks")
        if bds:
            add_offer(v, offer("mcdl", "MCDL 服务端", bds if bds.startswith("http") else "https:" + bds,
                               kind="link", note="服务端下载"))
    log(f"mcdl: {sum(len(v) for v in store.values())} versions")
    save_mcdl_cache(cache)          # 把猫盘校验结果一并落盘（上一处 save 只存了 redirect 解析）
    return "mcdl", store


# ---------------------------------------------------------------- MCAPKS
def sync_mcapks(limit=0):
    log("mcapks: list")
    j = http_json("https://mcapks.net/api/get-vslist.php",
                  cache_key="mcapks/list.json", max_age=1800)
    if not j.get("success"):
        raise RuntimeError("mcapks list failed: " + str(j.get("message"))[:200])
    items = (j.get("data") or {}).get("versions") or []
    log(f"mcapks: {len(items)} versions")
    if limit:
        items = items[:limit]

    def fetch(item):
        ver = item["version"] if isinstance(item, dict) else item
        out = {}
        for typ in ("v8a", "v7a"):
            key = f"mcapks/{re.sub(r'[^0-9A-Za-z._-]', '_', str(ver))}_{typ}.json"
            try:
                out[typ] = http_json(
                    f"https://mcapks.net/api/get-download.php?version={quote(str(ver))}&type={typ}",
                    cache_key=key, max_age=30 * 86400, retries=2)
            except Exception as e:  # noqa: BLE001
                out[typ] = {"success": False, "error": str(e)}
        return item, out

    store = {}
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(fetch, it) for it in items]
        for fut in as_completed(futs):
            item, res = fut.result()
            done += 1
            if done % 100 == 0:
                log(f"mcapks: {done}/{len(items)}")
            raw = item["version"] if isinstance(item, dict) else item
            ver = norm_ver(raw)
            ch = "beta" if (isinstance(item, dict) and item.get("beta")) else "release"
            v = merge_version(store, "android", ver, ch,
                              (item.get("date") or "")[:10] if isinstance(item, dict) else "",
                              "mcapks", meta={"raw": raw, "size": item.get("size") if isinstance(item, dict) else ""})
            for typ, jj in res.items():
                if not jj or not jj.get("success"):
                    continue
                d = jj.get("data") or {}
                real_type = d.get("type") or typ
                arch = "arm64-v8a" if real_type == "v8a" else "armeabi-v7a"
                for dl in d.get("downloads") or []:
                    if not dl.get("url"):
                        continue
                    provider = dl.get("name") or "网盘"
                    _, kind = classify_url(dl["url"])
                    add_offer(v, offer("mcapks", provider, dl["url"], kind=kind,
                                       arch=arch, scope="file",
                                       password=dl.get("password", ""),
                                       note="MCAPKS " + arch))
    enrich_mcapks_web(store)
    log(f"mcapks: {len(store)} versions")
    return "mcapks", store


# ---------------------------------------------------------------- MCAPKS web
MCAPKS_INFO_CACHE = CACHE / "mcapks" / "info"


def mcapks_web_offers(ver, info_key, lock):
    """MCAPKS 开放 API 不含网页独有的渠道（光鸭/移动云盘等），从 info 页补齐。"""
    import base64
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(ver))
    p = MCAPKS_INFO_CACHE / f"{safe}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    b = base64.b64encode(str(ver).encode()).decode().rstrip("=")
    url = f"https://mcapks.net/info/{b}/{info_key}.html"
    try:
        r = S.get(url, timeout=40)
        if r.status_code != 200:
            return []
    except Exception:
        return []
    h = r.text
    m = re.search(r"copyDownloadLinks\(\)\s*\{\s*copyText\('(.*?)'\);", h, re.S)
    items = []
    seen = set()

    HOSTS = ("123pan", "quark", "baidu", "xunlei", "uc.cn", "guangya", "139.com",
             "aliyundrive", "alipan", "189.cn", "ctyun", "lanzou", "onedrive", "sharepoint",
             "1drv", "mega.nz", "mediafire")

    def push(prov, url, pw=""):
        prov = {"迅雷云盘": "迅雷网盘", "光鸭云盘": "光鸭网盘"}.get(prov.strip(), prov.strip())
        if not prov or not url.startswith("http"):
            return
        if not any(h in url.lower() for h in HOSTS):
            return
        key = (prov, url)
        if key in seen:
            return
        seen.add(key)
        items.append({"provider": prov, "url": url, "password": pw})

    # 网页弹窗里的完整线路（含移动云盘 yun.139.com / 光鸭等）
    for mm in re.finditer(r"openCardModal\(this,\s*'([^']*)',\s*'([^']*)'(?:,\s*'([^']*)')?", h):
        push(mm.group(1), mm.group(2), mm.group(3) or "")

    if m:
        text = m.group(1).replace("\\n", "\n")
        for line in text.splitlines():
            mm = re.match(r"^([^：:]{2,12})[：:]\s*(https?://\S+)", line.strip())
            if mm:
                pw = ""
                pm = re.search(r"提取码[：:]\s*([0-9A-Za-z]+)", line)
                if pm:
                    pw = pm.group(1)
                push(mm.group(1), mm.group(2), pw)
    # fallback/extra: modal anchors with provider text
    with lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    return items


def enrich_mcapks_web(store):
    """Attach channels that only exist on the MCAPKS info pages."""
    lock = __import__("threading").Lock()
    log("mcapks: web info pages")
    try:
        listing = http_json("https://mcapks.net/api/vs-list.php", cache_key="mcapks/vslist.json",
                            max_age=1800)
        items = listing.get("message") or []
    except Exception as e:  # noqa: BLE001
        log(f"mcapks: vs-list fail {str(e)[:80]}")
        return
    todo = []
    for it in items:
        ver = str(it.get("version") or "").strip()
        key = it.get("info_key") or ""
        if ver and key:
            todo.append((ver, key))
    log(f"mcapks: {len(todo)} info pages")
    added = 0
    done = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(mcapks_web_offers, v, k, lock): v for v, k in todo}
        for fut in as_completed(futs):
            ver = futs[fut]
            try:
                offers = fut.result()
            except Exception:
                offers = []
            v = store.get(ver)
            if v:
                for it in offers:
                    before = len(v["offers"])
                    add_offer(v, offer("mcapks", it["provider"], it["url"],
                                       kind=classify_url(it["url"])[1], scope="file",
                                       note="MCAPKS 网页渠道", password=it.get("password") or ""))
                    added += len(v["offers"]) - before
            done += 1
            if done % 150 == 0:
                log(f"mcapks: web {done}/{len(todo)}")
    log(f"mcapks: web added {added} offers")


# ---------------------------------------------------------------- MCAPPX
def sync_mcappx():
    log("mcappx: json")
    j = http_json("https://data.mcappx.com/v2/bedrock.json",
                  cache_key="mcappx/bedrock.json", max_age=1800)
    root = j.get("From_mcappx.com") or {}
    log(f"mcappx: {len(root)} versions")
    store = {}
    ch_map = {"Release": "release", "Beta": "beta", "Preview": "preview"}
    status_map = {3: "已归档（可下载）", 2: "已收录", 1: "未发布/待证实", 0: "已丢失"}
    for verkey, ent in root.items():
        if not isinstance(ent, dict):
            continue
        ch = ch_map.get(ent.get("Type"), "release")
        released = (ent.get("Date") or "")[:10]
        build = ent.get("BuildType") or ""
        d = major_of(norm_ver(verkey))
        page = f"https://www.mcappx.com/bedrock/{d}/{quote(str(verkey))}/"
        v = merge_version(store, "windows", verkey, ch, released, "mcappx",
                          meta={"build": build, "id": ent.get("ID"), "page": page})
        for var in ent.get("Variations") or []:
            arch = var.get("Arch") or ""
            md5 = var.get("MD5") or ""
            status = var.get("ArchivalStatus")
            urls, ids = [], []
            for md in var.get("MetaData") or []:
                if isinstance(md, str) and md.startswith("http"):
                    urls.append(md)
                elif md:
                    ids.append(str(md))
            for u in urls:
                add_offer(v, offer("mcappx", "Xbox CDN · MSIXVC" if build == "GDK" else "Xbox CDN",
                                   u, kind="direct", arch=arch, checksum_type="md5",
                                   checksum=md5, scope="package",
                                   note=f"{build} 原始包 · {status_map.get(status, status)}"))
            if ids:
                add_offer(v, offer("mcappx", "Microsoft Store · UpdateID", page, kind="updateid",
                                   arch=arch, scope="package",
                                   note=f"{build} UpdateID " + ", ".join(ids[:2])))
            if not urls and not ids:
                add_offer(v, offer("mcappx", "MCAPPX 版本页", page, kind="page",
                                   arch=arch, scope="package",
                                   note=f"{build} · {status_map.get(status, status)}"))
    log(f"mcappx: {len(store)} versions")
    enrich_mcappx_pages(store)
    return "mcappx", store


# ---------------------------------------------------------------- Spectrollay
PLATFORM_DEV = {"android": "android", "ios": "ios", "windows": "windows", "linux": "linux"}
SPECTRO_EDITIONS = ["bedrock", "java", "education", "trial", "server",
                    "story_mode", "earth", "dungeons", "legends"]
SPECTRO_BASE = "https://www.spectrollay.com/data/minecraft_repository"


def spectro_versions(text):
    """星月 release 字段是逗号分隔的版本列表，可能带 (+2) 修补号或写「暂未发布」。"""
    out = []
    for part in str(text or "").split(","):
        part = re.sub(r"\(.*?\)$", "", part.strip()).strip()
        if re.match(r"^\d+(\.\d+)*$", part):
            out.append(part)
    return out


def sync_spectro():
    log("spectro: jsonc")
    p = CACHE / "spectro/bedrock_versions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or time.time() - p.stat().st_mtime > 1800:
        r = S.get(f"{SPECTRO_BASE}/bedrock_versions.json", timeout=60)
        r.raise_for_status()
        p.write_text(r.text, encoding="utf-8")
    raw = strip_jsonc(p.read_text(encoding="utf-8"))
    data = json.loads(raw)
    dirs = {"android": [], "ios": [], "windows": []}
    blank = {}
    for grp in data:
        for mv in grp.get("major_versions") or []:
            for cv in mv.get("child_versions") or []:
                cid = str(cv.get("id") or cv.get("version_name") or "").strip()
                if not cid:
                    continue
                family = re.match(r"^(\d+)", cid)
                family = family.group(1) if family else ""
                for plat, pdata in (cv.get("platforms") or {}).items():
                    dev = PLATFORM_DEV.get(plat)
                    if not dev or not isinstance(pdata, dict):
                        continue
                    dirs.setdefault(dev, [])
                    offers = []
                    for ck, chd in (pdata.get("channels") or {}).items():
                        if not isinstance(chd, dict):
                            continue
                        pname = chd.get("name") or ck
                        for variant, url in chd.items():
                            if variant == "name":
                                continue
                            if not isinstance(url, str) or not url.strip() or not url.startswith("http"):
                                # 星月会先占位（如 Windows 全部分支），只记数量不生成链接
                                blank[dev] = blank.get(dev, 0) + 1
                                continue
                            provider, kind = classify_url(url)
                            offers.append(offer("spectro", pname, url,
                                                kind="directory" if "sharepoint" in url or "1drv" in url else kind,
                                                scope="directory", note=variant))
                    dirs[dev].append({
                        "v": cid,
                        "version_name": cv.get("version_name") or cid,
                        "major": major_of(cid),
                        "family": family,
                        "branch": cid,
                        "releases": spectro_versions(pdata.get("release")),
                        "release": pdata.get("release") or "",
                        "build": pdata.get("build") or "",
                        "support": pdata.get("support") or "",
                        "arch": pdata.get("arch") or "",
                        "type": pdata.get("type") or "",
                        "size": pdata.get("size") or "",
                        "note": pdata.get("note") or "",
                        "offers": offers,
                    })
    log("spectro: " + ", ".join(f"{k}={len(v)}({len([x for x in v if x['offers']])} 有链接)"
                                for k, v in dirs.items()))
    write_spectro_platforms(dirs, blank)
    return "spectro", dirs


def write_spectro_platforms(dirs, blank):
    """记录星月每个平台/版本库的可用性，供 /sources/ 页面展示（Windows 目前是占位空链）。"""
    editions = {}
    for ed in SPECTRO_EDITIONS:
        try:
            r = S.head(f"{SPECTRO_BASE}/{ed}_versions.json", timeout=20, allow_redirects=True)
            editions[ed] = r.status_code == 200
        except Exception as e:  # noqa: BLE001
            editions[ed] = None
            log(f"spectro: probe {ed} fail {str(e)[:80]}")
    payload = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": f"{SPECTRO_BASE}/bedrock_versions.json",
        "platforms": {
            dev: {
                "branches": len(arr),
                "branches_with_links": len([x for x in arr if x.get("offers")]),
                "links": sum(len(x.get("offers") or []) for x in arr),
                "blank_links": blank.get(dev, 0),
            }
            for dev, arr in dirs.items()
        },
        "editions": editions,
    }
    (DATA / "spectro_platforms.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")



# ---------------------------------------------------------------- 星露谷 Stardew Valley
STARDEW_VERSIONS = "https://data.stardewvalley.spectrollay.com/repository/database/versions.json"
STARDEW_HOME = "https://stardewvalley.spectrollay.com/repository/"


def _sv_date(s):
    parts = re.split(r"[.\-/]", str(s or "").strip())
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return ""
    return f"{int(parts[0]):04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"


def sync_stardew():
    """星露谷版本库（Spectrollay）：versions.json 的 originalPackage / repackedPackage 各带网盘链接。"""
    log("stardew: versions.json")
    p = CACHE / "stardew/versions.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or time.time() - p.stat().st_mtime > 3600:
        r = S.get(STARDEW_VERSIONS, timeout=60)
        r.raise_for_status()
        p.write_text(r.text, encoding="utf-8")
    data = json.loads(strip_jsonc(p.read_text(encoding="utf-8")))
    store = {}
    count = 0
    for idx, it in enumerate(data):
        ver = str(it.get("version") or "").strip()
        if not ver:
            continue
        released = _sv_date(it.get("date"))
        desc = re.sub(r"<[^>]+>", " ", it.get("description") or "")
        desc = re.sub(r"\s+", " ", __import__("html").unescape(desc)).strip()
        for key, label in (("originalPackage", "原版"), ("repackedPackage", "重打包")):
            pkg = it.get(key) or {}
            files = pkg.get("files") or []
            links = [x for x in (pkg.get("links") or []) if (x.get("url") or "").startswith("http")]
            if not files or not links:
                continue
            size = 0
            for f in files:
                try:
                    size += int(f.get("size") or 0)
                except (TypeError, ValueError):
                    pass
            archs = []
            for f in files:
                for a in f.get("architectures") or []:
                    if a not in archs:
                        archs.append(a)
            note = f"{label}包 {len(files)} 个文件（{'/'.join(archs) or '通用'}）"
            v = merge_version(store, "stardew", ver, "release", released, "stardew",
                              notes=desc[:160],
                              meta={"versionCode": it.get("versionCode"),
                                    "targetSdk": it.get("targetSdk"),
                                    "minSdk": it.get("minSdk"),
                                    "idx": idx, "size": size or None, "package": key})
            for x in links:
                provider, kind = classify_url(x["url"])
                code = str(x.get("code") or "").strip()
                add_offer(v, offer("stardew", f"{provider} · {label}", x["url"], kind=kind,
                                   size=size or None, scope="file",
                                   note=note + (f"，提取码 {code}" if code else "")))
                count += 1
    log(f"stardew: {len(store)} versions, {count} offers")
    return "stardew", store


# ---------------------------------------------------------------- 小布 xbu
XBU_CACHE = "xiaobu/data.json"


def sync_xiaobu():
    """小布のMC版本库 /api/data: stable+beta Bedrock versions with 123clouddisk CDN links."""
    log("xiaobu: api/data")
    j = http_json("https://mcdl.xbu.hk/api/data", cache_key=XBU_CACHE, max_age=1800)
    buckets = j.get("versions") or {}
    stores = {"android": {}, "windows": {}}
    count = 0
    for bucket, ch in (("stable", "release"), ("beta", "beta")):
        for it in buckets.get(bucket) or []:
            if not isinstance(it, dict):
                continue
            raw = str(it.get("version") or "").strip()
            if not raw:
                continue
            ver = norm_ver(raw)
            released = str(it.get("date") or "")[:10]
            wiki = it.get("wiki") or ""
            for key, arch, single in (("v7aFiles", "armeabi-v7a", "v7a"),
                                      ("v8aFiles", "arm64-v8a", "v8a")):
                files = it.get(key) or []
                if not files and it.get(single):
                    files = [{"label": "原版", "url": it.get(single)}]
                for f in files:
                    url = (f or {}).get("url") or ""
                    if not url:
                        continue
                    label = (f or {}).get("label") or "安装包"
                    v = merge_version(stores["android"], "android", ver, ch, released, "xiaobu",
                                      meta={"raw": raw, "wiki": wiki})
                    add_offer(v, offer("xiaobu", f"小布 · 123云盘直链 · {label}", url,
                                       kind="direct", arch=arch, scope="file",
                                       note="小布 CDN 直链"))
                    count += 1
            files = it.get("winFiles") or []
            if not files and it.get("win"):
                files = [{"label": "Windows 安装包", "url": it.get("win")}]
            for f in files:
                url = (f or {}).get("url") or ""
                if not url:
                    continue
                label = (f or {}).get("label") or "Windows 安装包"
                v = merge_version(stores["windows"], "windows", norm_ver(raw), ch, released, "xiaobu",
                                  meta={"raw": raw, "wiki": wiki})
                add_offer(v, offer("xiaobu", f"小布 · 123云盘直链 · {label}", url,
                                   kind="direct", scope="file", note="小布 CDN 直链"))
                count += 1
    log(f"xiaobu: {sum(len(v) for v in stores.values())} versions, {count} offers")
    return "xiaobu", stores


# ---------------------------------------------------------------- MCARC / bbk
MCARC_MAJORS = ["26.x", "1.21.x", "1.20.x", "1.19.x", "1.18.x", "1.17.x", "1.16.x",
                "1.15.x", "1.14.x", "1.13.x", "1.12.x", "1.11.x", "1.10.x", "1.9.x",
                "1.8.x", "1.7.x", "1.6.x", "1.5.x", "1.4.x", "1.3.x", "1.2.x", "1.1.x",
                "1.0.x", "0.16.x", "0.15.x", "0.14.x"]
ARCH_MAP = {"ARMv7": "armeabi-v7a", "ARMv8": "arm64-v8a", "x86": "x86", "x64": "x64", "ARM64": "arm"}


def sync_mcarc():
    """MCARC 动态版本库 (bbk.endyun.ltd) 公开接口：last_version/get_version。"""
    log("mcarc: get_version majors")
    store = {}
    count = 0
    for major in MCARC_MAJORS:
        try:
            j = http_json("http://bbk.endyun.ltd:9000/api/get_version",
                          cache_key=f"mcarc/{major}.json", max_age=1800,
                          method="POST", data={"v": major, "b": "2"})
        except Exception as e:  # noqa: BLE001
            log(f"mcarc: {major} fail {str(e)[:80]}")
            continue
        msgs = j.get("message") or []
        if not isinstance(msgs, list):
            continue
        for m in msgs:
            if not isinstance(m, dict):
                continue
            raw = str(m.get("version_all") or m.get("version") or "").strip()
            if not raw:
                continue
            ver = norm_ver(raw)
            ch = "beta" if int(m.get("is_beta") or 0) else "release"
            released = str(m.get("update_time") or "")[:10]
            v = merge_version(store, "android", ver, ch, released, "mcarc",
                              meta={"raw": raw, "version": m.get("version")})
            for prov, archs in (m.get("link") or {}).items():
                if isinstance(archs, dict):
                    for arch, url in archs.items():
                        if not url:
                            continue
                        kind = classify_url(url)[1]
                        add_offer(v, offer("mcarc", f"MCARC · {prov}", url, kind=kind,
                                           arch=ARCH_MAP.get(arch, ""), scope="file",
                                           note=f"MCARC 动态库 · {arch}"))
                        count += 1
                elif isinstance(archs, str) and archs.startswith("http"):
                    add_offer(v, offer("mcarc", f"MCARC · {prov}", archs,
                                       kind=classify_url(archs)[1], scope="file"))
                    count += 1
    log(f"mcarc: {len(store)} versions, {count} offers")
    return "mcarc", store


# ---------------------------------------------------------------- MCAPKS web
MCAPKS_INFO_CACHE = CACHE / "mcapks" / "info"


def mcapks_web_offers(ver, info_key, lock):
    """MCAPKS 开放 API 不含网页独有的渠道（光鸭/移动云盘等），从 info 页补齐。"""
    import base64
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", str(ver))
    p = MCAPKS_INFO_CACHE / f"{safe}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    b = base64.b64encode(str(ver).encode()).decode().rstrip("=")
    url = f"https://mcapks.net/info/{b}/{info_key}.html"
    try:
        r = S.get(url, timeout=40)
        if r.status_code != 200:
            return []
    except Exception:
        return []
    h = r.text
    m = re.search(r"copyDownloadLinks\(\)\s*\{\s*copyText\('(.*?)'\);", h, re.S)
    items = []
    seen = set()

    HOSTS = ("123pan", "quark", "baidu", "xunlei", "uc.cn", "guangya", "139.com",
             "aliyundrive", "alipan", "189.cn", "ctyun", "lanzou", "onedrive", "sharepoint",
             "1drv", "mega.nz", "mediafire")

    def push(prov, url, pw=""):
        prov = {"迅雷云盘": "迅雷网盘", "光鸭云盘": "光鸭网盘"}.get(prov.strip(), prov.strip())
        if not prov or not url.startswith("http"):
            return
        if not any(h in url.lower() for h in HOSTS):
            return
        key = (prov, url)
        if key in seen:
            return
        seen.add(key)
        items.append({"provider": prov, "url": url, "password": pw})

    # 网页弹窗里的完整线路（含移动云盘 yun.139.com / 光鸭等）
    for mm in re.finditer(r"openCardModal\(this,\s*'([^']*)',\s*'([^']*)'(?:,\s*'([^']*)')?", h):
        push(mm.group(1), mm.group(2), mm.group(3) or "")

    if m:
        text = m.group(1).replace("\\n", "\n")
        for line in text.splitlines():
            mm = re.match(r"^([^：:]{2,12})[：:]\s*(https?://\S+)", line.strip())
            if mm:
                pw = ""
                pm = re.search(r"提取码[：:]\s*([0-9A-Za-z]+)", line)
                if pm:
                    pw = pm.group(1)
                push(mm.group(1), mm.group(2), pw)
    # fallback/extra: modal anchors with provider text
    with lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    return items


def enrich_mcapks_web(store):
    """Attach channels that only exist on the MCAPKS info pages."""
    lock = __import__("threading").Lock()
    log("mcapks: web info pages")
    try:
        listing = http_json("https://mcapks.net/api/vs-list.php", cache_key="mcapks/vslist.json",
                            max_age=1800)
        items = listing.get("message") or []
    except Exception as e:  # noqa: BLE001
        log(f"mcapks: vs-list fail {str(e)[:80]}")
        return
    todo = []
    for it in items:
        ver = str(it.get("version") or "").strip()
        key = it.get("info_key") or ""
        if ver and key:
            todo.append((ver, key))
    log(f"mcapks: {len(todo)} info pages")
    added = 0
    done = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(mcapks_web_offers, v, k, lock): v for v, k in todo}
        for fut in as_completed(futs):
            ver = futs[fut]
            try:
                offers = fut.result()
            except Exception:
                offers = []
            v = store.get(ver)
            if v:
                for it in offers:
                    before = len(v["offers"])
                    add_offer(v, offer("mcapks", it["provider"], it["url"],
                                       kind=classify_url(it["url"])[1], scope="file",
                                       note="MCAPKS 网页渠道", password=it.get("password") or ""))
                    added += len(v["offers"]) - before
            done += 1
            if done % 150 == 0:
                log(f"mcapks: web {done}/{len(todo)}")
    log(f"mcapks: web added {added} offers")


# ---------------------------------------------------------------- MCAPPX pages
MCAPPX_PAGE_CACHE = CACHE / "mcappx" / "pages"


def parse_mcappx_page(html):
    """Extract 123pan + dl.mcappx.com accelerator links with the nearest arch label."""
    m1 = re.search(r"<h2[^>]*>\s*文件下载", html)
    m2 = re.search(r"<h2[^>]*>\s*常见问题", html)
    if not m1:
        return []
    sec = html[m1.end():m2.start()] if m2 else html[m1.end():]
    arch_marks = []
    for label in ("x64", "x86", "arm"):
        for mm in re.finditer(r">\s*" + label + r"\s*<", sec, re.I):
            arch_marks.append((mm.start(), {"x64": "x64", "x86": "x86", "arm": "arm"}[label]))
    arch_marks.sort()
    out = []
    seen = set()
    for mm in re.finditer(r'href="(https?://[^"]+)"', sec):
        url = mm.group(1)
        if "123pan" in url:
            prov = "MCAPPX · 123云盘"
        elif "dl.mcappx.com" in url:
            prov = "MCAPPX 加速节点 · 日本" if url.rstrip("/").endswith("-jp") else (
                "MCAPPX 加速节点 · 美国" if url.rstrip("/").endswith("-us") else "MCAPPX 加速节点")
        else:
            continue
        arch = ""
        for pos, a in arch_marks:
            if pos < mm.start():
                arch = a
            else:
                break
        key = (prov, url)
        if key in seen:
            continue
        seen.add(key)
        out.append({"provider": prov, "url": url, "arch": arch})
    return out


def mcappx_page_offers(verkey, page, lock):
    safe = re.sub(r"[^0-9A-Za-z._-]", "_", verkey)
    p = MCAPPX_PAGE_CACHE / f"{safe}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 30 * 86400:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    try:
        html = curl_html(page)
        if not html:
            return []
        items = parse_mcappx_page(html)
    except Exception:
        return []
    with lock:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    return items


def enrich_mcappx_pages(store):
    """Add version-page channels (123云盘 / JP-US accelerators) for MCAPPX versions."""
    lock = __import__("threading").Lock()
    jobs = []
    for key, v in store.items():
        page = (v.get("meta") or {}).get("page")
        if page:
            jobs.append((key, page))
    if not jobs:
        return
    log(f"mcappx: crawling {len(jobs)} version pages")
    done = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(mcappx_page_offers, k, pg, lock): k for k, pg in jobs}
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                items = fut.result()
            except Exception:
                items = []
            v = store.get(key)
            if v:
                for it in items:
                    add_offer(v, offer("mcappx", it["provider"], it["url"], kind="direct",
                                       arch=it.get("arch") or "", scope="package",
                                       note="MCAPPX 版本页渠道"))
            done += 1
            if done % 100 == 0:
                log(f"mcappx: pages {done}/{len(jobs)}")


# ---------------------------------------------------------------- 甘泉 mcsc
GANQUAN_API = "https://mc.mcsc.top/admin/modules/bedrock_versions/api/get_filtered_versions.php"
GANQUAN_PAGE = "https://mc.mcsc.top/resources/bedrock.php"


def sync_ganquan():
    """甘泉版本库：bedrock.php 只是壳，卡片由 AJAX 分页给出（实测 49 页 / 585 个版本）。

    分页接口：/admin/modules/bedrock_versions/api/get_filtered_versions.php?page=N
    返回 JSON {version_cards: HTML, pagination, page, total_pages, total_results}。
    老版本只抓页面首屏（12 个）会漏掉 97% 的内容。
    """
    log("ganquan: get_filtered_versions.php")
    cdir = CACHE / "ganquan"
    cdir.mkdir(parents=True, exist_ok=True)
    stores = {"android": {}, "windows": {}}
    count = 0

    def fetch_page(n):
        p = cdir / f"page-{n}.json"
        if p.exists() and time.time() - p.stat().st_mtime < 1800:
            return json.loads(p.read_text(encoding="utf-8"))
        r = S.get(GANQUAN_API, params={"page": n}, timeout=40,
                  headers={"Referer": GANQUAN_PAGE, "X-Requested-With": "XMLHttpRequest"})
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return data

    def html_cards(html):
        return re.split(r'<div class="version-card', html or "")[1:]

    pages = []
    try:
        first = fetch_page(1)
        total_pages = int(first.get("total_pages") or 1)
        log(f"ganquan: {first.get('total_results')} versions / {total_pages} pages")
        pages.append(html_cards(first.get("version_cards")))
        for n in range(2, total_pages + 1):
            try:
                pages.append(html_cards(fetch_page(n).get("version_cards")))
            except Exception as e:  # noqa: BLE001
                log(f"ganquan: page {n} FAIL {str(e)[:80]}")
            time.sleep(0.15)
    except Exception as e:  # noqa: BLE001
        log(f"ganquan: api FAIL {str(e)[:100]}，回落首屏 HTML")
        html = curl_html(GANQUAN_PAGE) or S.get(GANQUAN_PAGE, timeout=40).text
        pages = [html_cards(html)]

    def field(card, label):
        m = re.search(label + r"：\s*</div>\s*<div class=\"info-value\">\s*(.*?)\s*</div>", card, re.S)
        return re.sub(r"<[^>]+>", "", __import__("html").unescape(m.group(1))).strip() if m else ""

    for cards in pages:
        for card in cards:
            raw = field(card, "版本号")
            if not raw:
                continue
            ver = norm_ver(raw)
            typ = field(card, "类型")
            ch = "release" if "正式" in typ else "beta"
            released = field(card, "发布日期")[:10]
            size_s = field(card, "文件大小")
            size = None
            m = re.match(r"([\d.]+)\s*MB", size_s, re.I)
            if m:
                size = int(float(m.group(1)) * 1024 * 1024)
            for url, plat in re.findall(r'<a href="([^"]+)"[^>]*class="download-btn ([a-z]+)-btn"', card):
                dev = "android" if plat == "android" else ("windows" if plat == "windows" else "")
                if not dev:
                    continue
                v = merge_version(stores[dev], dev, ver, ch, released, "ganquan",
                                  meta={"raw": raw, "size": size})
                add_offer(v, offer("ganquan", "甘泉 · 123云盘", url,
                                   kind=classify_url(url)[1], size=size, scope="file",
                                   note="甘泉版本库"))
                count += 1
    log(f"ganquan: {sum(len(v) for v in stores.values())} versions, {count} offers")
    return "ganquan", stores


# ---------------------------------------------------------------- MCPELIFE
MCPELIFE_VERSION_RE = re.compile(r"https://mcpelife\.com/minecraft(?:-pe)?-\d+-\d+(?:-\d+)?/")


def parse_mcpelife_version(url):
    m = re.search(r"/minecraft(?:-pe)?-(\d+(?:-\d+)*)/?$", url)
    if not m:
        return ""
    return norm_ver(m.group(1).replace("-", "."))


MCPELIFE_HOST = "https://mcpelife.com"
MCPELIFE_LIST_RE = re.compile(
    r"https://mcpelife\.com/(?:download/)?minecraft(?:-pe)?-\d+(?:-\d+)+/")
MCPELIFE_INDEX_TTL = 3600
MCPELIFE_PAGE_TTL = 30 * 86400


def _ml_slug(path):
    return re.sub(r"[^0-9a-zA-Z]+", "_", path).strip("_")[:150]


def ml_page(cdir, path, ttl):
    """MCPELIFE 页面缓存（gzip 存盘：单页 ~200KB，几千页不压缩会吃掉近 1GB）。"""
    p = cdir / (_ml_slug(path) + ".html.gz")
    if p.exists() and time.time() - p.stat().st_mtime < ttl:
        try:
            return gzip.decompress(p.read_bytes()).decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            pass
    html = curl_html(MCPELIFE_HOST + "/" + path.lstrip("/"), compressed=True)
    if html:
        p.write_bytes(gzip.compress(html.encode("utf-8")))
    return html


def sync_mcpelife():
    """MCPELIFE：/download/ 索引分 37 页（实测 691 个版本页），版本页再分 download/N/ 子页，每页 1 个 dl 直链。"""
    log("mcpelife: index")
    cdir = CACHE / "mcpelife"
    cdir.mkdir(parents=True, exist_ok=True)
    first = ml_page(cdir, "download/", MCPELIFE_INDEX_TTL)
    urls = set(MCPELIFE_LIST_RE.findall(first))
    total_pages = max([int(n) for n in re.findall(r"/download/page/(\d+)/", first)] or [1])
    for n in range(2, total_pages + 1):
        urls |= set(MCPELIFE_LIST_RE.findall(ml_page(cdir, f"download/page/{n}/", MCPELIFE_INDEX_TTL)))
    log(f"mcpelife: {total_pages} index pages, {len(urls)} version pages")

    store = {}
    count = 0
    lock = __import__("threading").Lock()
    stats = {"subpages": 0, "empty": 0, "noapk": 0}

    def crawl(vurl):
        nonlocal count
        ver = parse_mcpelife_version(vurl)
        if not ver:
            return
        base = vurl[len(MCPELIFE_HOST):].lstrip("/")
        hub = ml_page(cdir, base, MCPELIFE_PAGE_TTL)
        subs = sorted({int(x) for x in re.findall(re.escape(vurl) + r"download/(\d+)/", hub)})
        found = []
        if subs:
            for n in subs[:20]:
                ph = ml_page(cdir, f"{base}download/{n}/", MCPELIFE_PAGE_TTL)
                with lock:
                    stats["subpages"] += 1
                found.extend(re.findall(r'https?://[^"\']+\.apk[^"\']*', ph))
        else:
            found = re.findall(r'https?://[^"\']+\.apk[^"\']*', hub)   # 老版本可能直接挂在正文
        found = sorted(set(found))
        if not found:
            with lock:
                stats["empty"] += 1
            return
        with lock:
            v = merge_version(store, "android", ver, "release", "", "mcpelife",
                              meta={"page": vurl})
            for u in found:
                low = u.lower()
                arch = ("arm64-v8a" if "arm64" in low or "v8a" in low else
                        "armeabi-v7a" if "v7a" in low or "armeabi" in low else
                        "x86" if "x86" in low else "")
                add_offer(v, offer("mcpelife", "MCPELIFE · APK 直链", u, kind="direct",
                                   arch=arch, scope="file", note="MCPELIFE"))
                count += 1

    with ThreadPoolExecutor(max_workers=5) as ex:
        list(ex.map(crawl, sorted(urls)))
    log(f"mcpelife: {len(store)} versions, {count} offers "
        f"({stats['subpages']} subpages, {stats['empty']} no-apk pages)")
    return "mcpelife", store


# ---------------------------------------------------------------- NullAtom
NULLATOM_API = "https://res.nullatom.com/api/fetch"
NULLATOM_REPO = "https://repo.res.nullatom.com"


def nullatom_list(path):
    return http_json(NULLATOM_API, cache_key="nullatom/" + path.strip("/").replace("/", "_") + ".json",
                     max_age=1800, method="POST", data=json.dumps({"path": path}),
                     headers={"Content-Type": "application/json"})


def sync_nullatom():
    """NullAtom 原子资源：/api/fetch 目录树 + repo.res.nullatom.com 直链（iOS IPA / UWP / Android）。"""
    log("nullatom: tree")
    stores = {"android": {}, "ios": {}, "windows": {}}
    count = 0
    specs = [
        ("/Minecraft/Android", "android", "", "release"),
        ("/Minecraft/Android/x64_v8a", "android", "x86_64", "release"),
        ("/Minecraft/iOS", "ios", "", "release"),
        ("/Minecraft/UWP", "windows", "", "release"),
        ("/Minecraft/UWP/Beta", "windows", "", "beta"),
    ]
    for path, dev, arch, ch in specs:
        try:
            j = nullatom_list(path)
        except Exception as e:  # noqa: BLE001
            log(f"nullatom: {path} fail {str(e)[:80]}")
            continue
        if j.get("code") != 200:
            log(f"nullatom: {path} code {j.get('code')}")
            continue
        for it in j.get("list") or []:
            name = str(it.get("name") or "")
            low = name.lower()
            if not low.endswith((".apk", ".ipa", ".appx", ".msix", ".msixvc")):
                continue
            try:
                size = int(it.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            if 0 < size < 1_000_000:      # placeholder stubs seen upstream
                continue
            fpath = str(it.get("path") or "")
            raw = re.sub(r"\.(apk|ipa|appx|msix|msixvc)$", "", name, flags=re.I)
            # /Android/x64_v8a 里的包名带 _64（26.20.4_64.apk = 26.20.4 的 64 位包），
            # 架构已由目录决定，版本号必须剥掉后缀，否则会多出 26.20.4_64 这种假版本
            variant = ""
            mvar = re.search(r"_(nomusic)$", raw, flags=re.I)
            if mvar:                      # 1.20.0.01_64_nomusic.apk = 1.20.0.01 的无音乐版
                variant = "无音乐版"
                raw = raw[:mvar.start()]
            raw = re.sub(r"_(?:64|32)$", "", raw)   # 先剥变体名再剥位宽后缀
            ver = norm_ver(game_ver(raw)) if dev == "windows" else norm_ver(raw)
            if not re.match(r"^\d", ver):
                continue
            v = merge_version(stores[dev], dev, ver, ch, "", "nullatom",
                              meta={"raw": raw, "page": "https://res.nullatom.com/Minecraft"})
            add_offer(v, offer("nullatom", "NullAtom · 直链", NULLATOM_REPO + fpath,
                               kind="direct", arch=arch, size=size or None, scope="file",
                               note="原子资源直链" + (f" · {variant}" if variant else "")))
            count += 1
    log(f"nullatom: {sum(len(v) for v in stores.values())} versions, {count} offers")
    return "nullatom", stores


# ---------------------------------------------------------------- BMCLAPI resources
BMCL = "https://bmclapi2.bangbang93.com"


def sync_bmcl_resources():
    """抓取 BMCLAPI 全部资源列表：Forge / NeoForge / OptiFine / LiteLoader。"""
    log("bmclapi: resources")
    out = {"generated": datetime.now(timezone.utc).isoformat(), "forge": [], "neoforge": [],
           "optifine": [], "liteloader": [], "java": {"ok": False, "note": "/java/list 返回 403"},
           "authlib": {"homepage": BMCL + "/mirrors/authlib-injector"}}
    # Forge: paginate 100/page
    offset = 0
    while offset < 20000:
        j = http_json(f"{BMCL}/forge/list/{offset}/100",
                      cache_key=f"bmcl/forge_{offset}.json", max_age=1800)
        if not isinstance(j, list) or not j:
            break
        for it in j:
            out["forge"].append({
                "mcversion": it.get("mcversion") or "",
                "version": it.get("version") or "",
                "build": it.get("build"),
                "url": f"{BMCL}/forge/download/{it.get('build')}",
            })
        offset += len(j)
        if len(j) < 100:
            break
    # NeoForge: iterate MC versions that have forge builds (NeoForge starts at 1.20.2)
    mc_versions = sorted({x["mcversion"] for x in out["forge"]})
    for mc in mc_versions:
        try:
            j = http_json(f"{BMCL}/neoforge/list/{quote(mc)}",
                          cache_key=f"bmcl/neoforge_{mc}.json", max_age=1800)
        except Exception:
            continue
        if not isinstance(j, list):
            continue
        for it in j:
            path = it.get("installerPath") or ""
            raw = it.get("rawVersion") or ""
            ver = it.get("version") or ""
            mc = it.get("mcversion") or mc
            if not path:
                if raw.startswith(mc + "-"):
                    path = f"/maven/net/neoforged/forge/{raw}/forge-{raw}-installer.jar"
                elif ver:
                    path = f"/maven/net/neoforged/neoforge/{ver}/neoforge-{ver}-installer.jar"
            out["neoforge"].append({
                "mcversion": mc,
                "version": ver,
                "raw": raw,
                "url": (BMCL + path) if path else "",
            })
    # OptiFine
    try:
        of = http_json(f"{BMCL}/optifine/versionList", cache_key="bmcl/optifine.json", max_age=1800)
        for it in of or []:
            mc, typ, patch = it.get("mcversion") or "", it.get("type") or "", it.get("patch") or ""
            out["optifine"].append({
                "mcversion": mc, "type": typ, "patch": patch,
                "filename": it.get("filename") or "",
                "forge": it.get("forge") or "",
                "url": f"{BMCL}/optifine/{quote(mc)}/{quote(typ)}/{quote(patch)}",
            })
    except Exception as e:  # noqa: BLE001
        log(f"bmclapi: optifine fail {str(e)[:80]}")
    # LiteLoader
    try:
        ll = http_json(f"{BMCL}/liteloader/list", cache_key="bmcl/liteloader.json", max_age=1800)
        for it in ll or []:
            b = it.get("build") or {}
            out["liteloader"].append({
                "mcversion": it.get("mcversion") or "",
                "version": b.get("version") or "",
                "file": b.get("file") or "",
                "url": f"{BMCL}/liteloader/download?version={quote(str(b.get('version') or ''))}"
                       f"&mcversion={quote(str(it.get('mcversion') or ''))}",
            })
    except Exception as e:  # noqa: BLE001
        log(f"bmclapi: liteloader fail {str(e)[:80]}")

    # Fabric / Quilt：官方 meta 给加载器与安装器，jar 走各自 maven（BMCLAPI 不镜像这两个）
    for key, meta, maven_base, name, home in (
        ("fabric", "https://meta.fabricmc.net/v2", "https://maven.fabricmc.net", "Fabric", "https://fabricmc.net/"),
        ("quilt", "https://meta.quiltmc.org/v3", "https://maven.quiltmc.org/repository/release", "Quilt", "https://quiltmc.org/"),
    ):
        def _mvn_url(mvn):
            try:
                g, a, v = str(mvn).split(":")
            except ValueError:
                return ""
            return f"{maven_base}/{g.replace('.', '/')}/{a}/{v}/{a}-{v}.jar"
        try:
            loaders = http_json(f"{meta}/versions/loader",
                                cache_key=f"modloaders/{key}_loader.json", max_age=6 * 3600) or []
            insts = http_json(f"{meta}/versions/installer",
                              cache_key=f"modloaders/{key}_installer.json", max_age=6 * 3600) or []
            out[key] = {
                "name": name, "homepage": home, "api": f"{meta}/versions/loader",
                "loader": [{"version": x.get("version") or "", "stable": bool(x.get("stable")),
                            "build": x.get("build"),
                            "url": _mvn_url(x.get("maven"))} for x in loaders if x.get("version")],
                "installer": [{"version": x.get("version") or "", "stable": bool(x.get("stable")),
                               "url": x.get("url") or _mvn_url(x.get("maven"))} for x in insts if x.get("version")],
            }
            log(f"{key}: loader={len(out[key]['loader'])} installer={len(out[key]['installer'])}")
        except Exception as e:  # noqa: BLE001
            log(f"{key}: FAIL {str(e)[:100]}")
            out[key] = {"name": name, "homepage": home, "loader": [], "installer": [], "error": str(e)[:200]}

    p = DATA / "resources.json"
    p.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log("bmclapi: forge=%d neoforge=%d optifine=%d liteloader=%d fabric=%d quilt=%d -> %s" %
        (len(out["forge"]), len(out["neoforge"]), len(out["optifine"]), len(out["liteloader"]),
         len((out.get("fabric") or {}).get("loader") or []), len((out.get("quilt") or {}).get("loader") or []), p))
    return out


# ---------------------------------------------------------------- MCARC static archive
MCARC_STATIC = "https://mcarc.github.io"


def mcarc_static_branches():
    h = curl_html(MCARC_STATIC + "/") or ""
    return sorted(set(re.findall(r'href="((?:\./|/)?(?:Android|Windows|iOS)[^"]*/)"', h)))


def parse_mcarc_branch(html):
    parts = re.split(r"<h2[^>]*>(.*?)</h2>", html, flags=re.S)
    out = []
    for title, body in zip(parts[1::2], parts[2::2]):
        ver = re.sub(r"<[^>]+>", "", __import__("html").unescape(title)).strip().rstrip("¶").strip()
        if not ver or not re.match(r"^\d", ver):
            continue
        items = []
        for u in re.findall(r'href="(https?://[^"]+)"', body):
            low = u.lower()
            if "sharepoint.com" in low and "0hqk5-my" in low:
                prov = "OneDrive（minecraftarchive）"
            elif "sharepoint.cn" in low:
                prov = "OneDrive 世纪互联"
            elif "1drv.ms" in low:
                prov = "OneDrive"
            elif "123pan" in low:
                prov = "123云盘"
            elif "pan.quark" in low:
                prov = "夸克网盘"
            elif "pan.baidu" in low:
                prov = "百度网盘"
            elif "guangya" in low:
                prov = "光鸭网盘"
            elif "xunlei" in low:
                prov = "迅雷网盘"
            else:
                continue
            items.append((prov, u))
        if items:
            out.append((ver, items))
    return out


def sync_mcarc_static():
    log("mcarc_static: index")
    branches = mcarc_static_branches()
    log(f"mcarc_static: {len(branches)} branches")
    stores = {"android": {}, "ios": {}, "windows": {}}
    lock = __import__("threading").Lock()
    count = 0

    def fetch(b):
        return b, curl_html(MCARC_STATIC + "/" + b.lstrip("./"))

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = [ex.submit(fetch, b) for b in branches]
        for fut in as_completed(futs):
            b, html = fut.result()
            if not html:
                continue
            dev = "windows" if b.startswith("Windows") else ("ios" if b.startswith("iOS") else "android")
            ch = "beta" if ("Trial" in b or "Demo" in b or "Education" in b) else "release"
            for ver, items in parse_mcarc_branch(html):
                v = merge_version(stores[dev], dev, norm_ver(ver), ch, "", "mcarc_static",
                                  meta={"page": MCARC_STATIC + "/" + b.lstrip("./")})
                for prov, url in items:
                    with lock:
                        add_offer(v, offer("mcarc_static", prov, url, kind="direct",
                                           scope="file", note="旧版归档"))
                        count += 1
    log(f"mcarc_static: {sum(len(x) for x in stores.values())} versions, {count} offers")
    return "mcarc_static", stores


# ---------------------------------------------------------------- merge & save
def load_existing(device):
    p = DATA / f"{device}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


WIKI_CHANNELS = {}


def load_wiki_channels():
    p = DATA / "wiki_channels.json"
    if not p.exists():
        return {}
    try:
        w = json.loads(p.read_text(encoding="utf-8")).get("bedrock") or {}
    except Exception:
        return {}
    test = w.get("test") or {}
    releases = set(w.get("release") or [])
    return {"test": test, "release": releases}


def wiki_channel(ver):
    """Minecraft Wiki 归类（Bedrock）：preview/beta/release，未知返回 ''。"""
    if not WIKI_CHANNELS:
        return ""
    test = WIKI_CHANNELS.get("test") or {}
    if ver in test:
        return test[ver]
    rel = WIKI_CHANNELS.get("release") or set()
    parts = str(ver).split(".")
    base = ".".join(parts[:2]) if len(parts) >= 2 else ver
    if ver in rel or base in rel:
        return "release"
    return ""


def apply_wiki_channels(doc):
    """Use the wiki record as the authority for release vs test, keeping upstream label."""
    if not WIKI_CHANNELS:
        return 0
    fixed = 0
    for v in doc.get("versions") or []:
        w = wiki_channel(v["v"])
        if not w:
            continue
        v.setdefault("meta", {})["wiki"] = w
        ours = v.get("channel")
        if (ours == "release") != (w == "release"):
            v["meta"]["upstream_channel"] = ours
            v["channel"] = w
            fixed += 1
    return fixed


def merge_previous(dev, doc, failed):
    """Keep last-good data for sources that failed this run."""
    if not failed:
        return doc
    old = load_existing(dev)
    if not old:
        return doc
    cur = {v["v"]: v for v in doc["versions"]}
    for ov in old.get("versions") or []:
        if not any(s in failed for s in ov.get("sources", [])):
            continue
        # 只搬本轮没跑/失败的来源，别把本轮重跑来源的旧链接一起带回来
        keep_offers = [o for o in (ov.get("offers") or []) if o.get("source") in failed]
        t = cur.get(ov["v"])
        if t is None:
            if not keep_offers:
                continue
            cp = dict(ov)
            cp["offers"] = keep_offers
            cp["offer_count"] = len(keep_offers)
            cp["providers"] = sorted({o["provider"] for o in keep_offers})
            cp["sources"] = sorted({o.get("source") for o in keep_offers if o.get("source")})
            cur[ov["v"]] = cp
            continue
        seen = {(o["source"], o["provider"], o["arch"], o["kind"], o["url"], o["note"])
                for o in t["offers"]}
        for o in ov.get("offers") or []:
            k = (o["source"], o["provider"], o["arch"], o["kind"], o["url"], o["note"])
            if o["source"] in failed and k not in seen:
                seen.add(k)
                t["offers"].append(o)
        t["sources"] = sorted(set(t["sources"]) | {o.get("source") for o in keep_offers if o.get("source")})
        t["offer_count"] = len(t["offers"])
        t["providers"] = sorted({o["provider"] for o in t["offers"]})
        if not t.get("notes") and ov.get("notes"):
            t["notes"] = ov["notes"]
    doc["versions"] = sorted(cur.values(), key=lambda x: (x.get("released") or "", sort_key(x["v"])), reverse=True)
    if "spectro" in failed and old.get("directories"):
        doc["directories"] = old["directories"]
    return doc


def _spectro_target(versions, d):
    """星月目录挂载目标：先按 release 列表里的精确版，再按分支号前缀（26 / 1.21.130）。

    不再退回「同大版本最新版」——那会把整个大版本几十个分支目录堆到一个版本上。
    """
    def vkey(x):
        return [int(n) for n in re.findall(r"\d+", x)[:5]]

    by_v = {v["v"]: v for v in versions}
    rel = [r for r in (d.get("releases") or []) if r in by_v]
    if rel:
        return by_v[max(rel, key=vkey)], "release"
    branch = str(d.get("branch") or d.get("v") or "").strip()
    if branch:
        cands = [x for x in versions if x["v"] == branch or x["v"].startswith(branch + ".")]
        if cands:
            return max(cands, key=lambda x: vkey(x["v"])), "branch"
    return None, None


def attach_spectro_dirs(doc, dirs):
    """星月分支目录挂到该分支最新精确版，标注为整分支目录。"""
    versions = doc.get("versions") or []
    for d in dirs or []:
        if not d.get("offers"):
            continue
        target, _how = _spectro_target(versions, d)
        if not target:
            continue
        rel = d.get("releases") or []
        branch = str(d.get("branch") or "").strip()
        if len(rel) > 1:
            note = f"整分支目录 {rel[0]}–{rel[-1]}（非单文件）"
        elif rel:
            note = f"整分支目录 {rel[0]}（非单文件）"
        elif branch:
            note = f"整分支目录 {branch} 系列（非单文件）"
        else:
            note = "整分支目录（非单文件）"
        for o in d.get("offers") or []:
            if not o.get("url"):
                continue
            if any(x.get("url") == o["url"] for x in target["offers"]):
                continue
            oo = dict(o)
            variant = (o.get("note") or "").strip()
            if variant:
                oo["provider"] = f'{o.get("provider") or "星月"} · {variant}'
            oo["note"] = note
            oo["kind"] = "directory"
            oo["scope"] = "directory"
            target["offers"].append(oo)
            if "spectro" not in target["sources"]:
                target["sources"].append("spectro")



def build_device(dev, store, dirs_extra, stats):
    versions = {}
    for _, v in store.items():
        ver = v["v"]
        if ver not in versions:
            versions[ver] = {
                "v": v["v"], "device": dev, "channel": v["channel"],
                "channels": list(v["channels"]), "released": v["released"],
                "notes": v["notes"], "meta": dict(v["meta"]),
                "sources": list(v["sources"]), "offers": [],
            }
        t = versions[ver]
        t["channels"] = sorted(set(t["channels"]) | set(v["channels"]))
        prio = {"release": 6, "beta": 5, "preview": 4, "snapshot": 3, "old": 2, "directory": 1}
        if prio.get(v["channel"], 0) > prio.get(t["channel"], 0):
            t["channel"] = v["channel"]
        if v["released"] > t["released"]:
            t["released"] = v["released"]
        if not t["notes"] and v["notes"]:
            t["notes"] = v["notes"]
        for s in v["sources"]:
            if s not in t["sources"]:
                t["sources"].append(s)
        for k, val in v["meta"].items():
            t["meta"].setdefault(k, val)
        seen = {(x["source"], x["provider"], x["arch"], x["kind"], x["url"], x["note"]) for x in t["offers"]}
        for o in v["offers"]:
            key = (o["source"], o["provider"], o["arch"], o["kind"], o["url"], o["note"])
            if key not in seen:
                seen.add(key)
                t["offers"].append(o)

    versions = {k: v for k, v in versions.items() if v["offers"]}
    for v in versions.values():
        v.pop("_seen", None)
        v["major"] = major_for(dev, v["v"])
        sizes = [o["size"] for o in v["offers"] if isinstance(o.get("size"), int) and o["size"] > 0]
        v["size"] = max(sizes) if sizes else None
        v["offer_count"] = len(v["offers"])
        v["providers"] = sorted({o["provider"] for o in v["offers"]})
        # newest source timestamp for source stats
        for o in v["offers"]:
            stats.setdefault("source_versions", {}).setdefault(o["source"], set()).add(dev + ":" + v["v"])

    arr = sorted(versions.values(), key=lambda x: (x["released"], sort_key(x["v"])), reverse=True)
    dirs = dirs_extra.get(dev, [])
    return {
        "device": dev,
        "name": DEVICES[dev],
        "generated": datetime.now(timezone.utc).isoformat(),
        "versions": arr,
        "directories": dirs,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="all")
    ap.add_argument("--limit", type=int, default=0, help="limit MCAPKS versions (test)")
    args = ap.parse_args()
    only = args.only
    t0 = time.time()

    catalog_path = DATA / "catalog.json"
    prev_sources = {}
    if catalog_path.exists():
        try:
            prev_sources = {s["key"]: s for s in
                            json.loads(catalog_path.read_text(encoding="utf-8")).get("sources", [])}
        except Exception:
            prev_sources = {}
    WIKI_CHANNELS.update(load_wiki_channels())

    stores = {"java": {}, "android": {}, "ios": {}, "windows": {}, "stardew": {}}
    dirs_extra = {"java": [], "android": [], "ios": [], "windows": [], "stardew": []}
    stats = {"source_status": {}}

    ran = set()

    def run(name, fn, dev_map=None):
        ran.add(name)
        try:
            res = fn()
            if name == "spectro":
                for d, arr in res[1].items():
                    dirs_extra[d].extend(arr)
                stats["source_status"][name] = {"ok": True, "items": sum(len(v) for v in res[1].values())}
            elif name == "mcdl":
                src, per_dev = res
                for dev in ("android", "ios", "windows"):
                    stores[dev].update({f"{name}:{k}": v for k, v in per_dev.get(dev, {}).items()})
                stats["source_status"][name] = {"ok": True,
                                                "items": sum(len(v) for v in per_dev.values())}
            elif name == "xiaobu":
                src, per_dev = res
                for dev in ("android", "windows"):
                    stores[dev].update({f"{name}:{k}": v for k, v in per_dev.get(dev, {}).items()})
                stats["source_status"][name] = {"ok": True,
                                                "items": sum(len(v) for v in per_dev.values())}
            elif name == "ganquan":
                src, per_dev = res
                for dev in ("android", "windows"):
                    stores[dev].update({f"{name}:{k}": v for k, v in per_dev.get(dev, {}).items()})
                stats["source_status"][name] = {"ok": True,
                                                "items": sum(len(v) for v in per_dev.values())}
            elif name == "mcarc_static":
                src, per_dev = res
                for dev in ("android", "ios", "windows"):
                    stores[dev].update({f"{name}:{k}": v for k, v in per_dev.get(dev, {}).items()})
                stats["source_status"][name] = {"ok": True,
                                                "items": sum(len(v) for v in per_dev.values())}
            elif name == "nullatom":
                src, per_dev = res
                for dev in ("android", "ios", "windows"):
                    stores[dev].update({f"{name}:{k}": v for k, v in per_dev.get(dev, {}).items()})
                stats["source_status"][name] = {"ok": True,
                                                "items": sum(len(v) for v in per_dev.values())}
            else:
                src, store = res
                dev = dev_map or src
                stores[dev].update({f"{name}:{k}": v for k, v in store.items()})
                stats["source_status"][name] = {"ok": True, "items": len(store)}
            log(f"{name}: OK")
        except Exception as e:  # noqa: BLE001
            log(f"{name}: FAIL {e}")
            traceback.print_exc()
            stats["source_status"][name] = {"ok": False, "error": str(e)[:300]}

    if only in ("all", "mojang"):
        run("mojang", sync_mojang, "java")
        # 无极镜像依赖 manifest（要拿官方 server.sha1 做完整性比对），随 mojang 一起跑
        if stores["java"]:
            try:
                n = attach_fastmirror(stores["java"])
                stats["source_status"]["fastmirror"] = {"ok": True, "items": n}
            except Exception as e:  # noqa: BLE001
                log(f"fastmirror: FAIL {e}")
                traceback.print_exc()
                stats["source_status"]["fastmirror"] = {"ok": False, "error": str(e)[:300]}
    if only in ("all", "mcdl"):
        run("mcdl", sync_mcdl)
    if only in ("all", "mcapks"):
        run("mcapks", lambda: sync_mcapks(args.limit), "android")
    if only in ("all", "mcappx"):
        run("mcappx", sync_mcappx, "windows")
    if only in ("all", "spectro"):
        run("spectro", sync_spectro)
    if only in ("all", "mcarc"):
        run("mcarc", sync_mcarc, "android")
    if only in ("all", "xiaobu"):
        run("xiaobu", sync_xiaobu)
    if only in ("all", "ganquan"):
        run("ganquan", sync_ganquan)
    if only in ("all", "mcpelife"):
        run("mcpelife", sync_mcpelife, "android")
    if only in ("all", "nullatom"):
        run("nullatom", sync_nullatom)
    if only in ("all", "stardew"):
        run("stardew", sync_stardew, "stardew")
    if only in ("all", "mcarc_static"):
        run("mcarc_static", sync_mcarc_static)
    if only in ("all", "bmclresources"):
        try:
            sync_bmcl_resources()
        except Exception as e:  # noqa: BLE001
            log(f"bmclapi: resources FAIL {str(e)[:200]}")

    catalog_devices = {}
    for dev in DEVICES:
        doc = build_device(dev, stores[dev], dirs_extra, stats)
        keep = {s for s in ALL_SOURCES
                if s not in ran or not stats["source_status"].get(s, {}).get("ok")}
        doc = merge_previous(dev, doc, keep)
        attach_spectro_dirs(doc, dirs_extra.get(dev, []))
        REAL = ("direct", "share", "link", "directory", "updateid")
        doc["versions"] = [v for v in doc["versions"]
                           if any(o.get("kind") in REAL for o in v["offers"])]
        fixed = apply_wiki_channels(doc) if dev in ("android", "ios", "windows") else 0
        if fixed:
            log(f"{dev}: wiki reclassified {fixed} versions")
        # Preserve previous data if this run produced nothing for that device
        if not doc["versions"] and not doc["directories"]:
            old = load_existing(dev)
            if old:
                log(f"{dev}: no new data, keeping previous snapshot")
                doc = old
        p = DATA / f"{dev}.json"
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        majors = {}
        for v in doc["versions"]:
            m = majors.setdefault(v["major"], {"major": v["major"], "count": 0,
                                               "latest": v["v"], "latest_release": "",
                                               "sources": set()})
            m["count"] += 1
            for s in v["sources"]:
                m["sources"].add(s)
            if v["channel"] == "release" and not m["latest_release"]:
                m["latest_release"] = v["v"]
        latest = next((v["v"] for v in doc["versions"] if v["channel"] == "release"), "")
        rel_pool = [v for v in doc["versions"] if v["channel"] == "release"]
        confirmed = [v for v in rel_pool if (v.get("meta") or {}).get("wiki") == "release"]
        if confirmed:
            rel_pool = confirmed
        real = [v for v in rel_pool
                if any(o.get("kind") in ("direct", "share", "link") for o in v.get("offers", []))]
        if real:
            rel_pool = real
        if rel_pool:
            latest = max(rel_pool, key=lambda v: ([int(n) for n in re.findall(r"\d+", v["v"])[:4]],
                                                  v.get("released") or ""))["v"]
        latest_any = doc["versions"][0]["v"] if doc["versions"] else ""
        catalog_devices[dev] = {
            "name": DEVICES[dev],
            "count": len(doc["versions"]),
            "directory_count": len(doc["directories"]),
            "latest_release": latest,
            "latest_any": latest_any,
            "majors": sorted(
                [{**m, "sources": sorted(m["sources"])} for m in majors.values()],
                key=lambda x: sort_key(x["major"]), reverse=True),
        }
        log(f"{dev}: {len(doc['versions'])} versions, {len(doc['directories'])} directories, "
            f"{len(majors)} majors -> {p}")

    # source stats
    src_counts = {}
    for dev in DEVICES:
        doc = json.loads((DATA / f"{dev}.json").read_text(encoding="utf-8"))
        for v in doc["versions"]:
            for o in v["offers"]:
                src_counts.setdefault(o["source"], set()).add(dev + ":" + v["v"])
        for d in doc["directories"]:
            for o in d["offers"]:
                src_counts.setdefault(o["source"], set()).add(dev + ":dir:" + d["v"])

    SOURCES = [
        ("mojang", "Mojang Piston", "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"),
        ("mcdl", "MineBBS · MCDL", "https://mc.minebbs.com"),
        ("mcapks", "MCAPKS（苦力怕论坛）", "https://mcapks.net"),
        ("mcappx", "MCAPPX", "https://www.mcappx.com"),
        ("spectro", "星月 Minecraft 版本库", "https://www.spectrollay.com/minecraft_repository"),
        ("mcarc", "MCARC / zihao_il 动态版本库", "https://bbk.endyun.ltd/main"),
        ("xiaobu", "小布のMC版本库", "https://mcdl.xbu.hk"),
        ("ganquan", "甘泉版本库", "https://mc.mcsc.top/resources/bedrock.php"),
        ("mcpelife", "MCPELIFE", "https://mcpelife.com"),
        ("nullatom", "NullAtom 原子资源", "https://res.nullatom.com/Minecraft"),
        ("bmclapi", "BMCLAPI 镜像", "https://bmclapi2.bangbang93.com"),
        ("fastmirror", "FastMirror 无极镜像", "https://www.fastmirror.net"),
        ("mcarc_static", "MCARC 旧版归档", "https://mcarc.github.io"),
        ("stardew", "星露谷版本库", STARDEW_HOME),
    ]
    sources = []
    for key, name, home in SOURCES:
        st = stats["source_status"].get(key)
        if key == "bmclapi":
            st = stats["source_status"].get("mojang")
        prev = prev_sources.get(key, {})
        if st is None:  # source not part of this partial run: carry last known status
            ok = prev.get("ok", False)
            err = prev.get("error", "")
            last = prev.get("last_sync") or datetime.now(timezone.utc).isoformat()
        else:
            ok = st.get("ok", False)
            err = st.get("error", "")
            last = datetime.now(timezone.utc).isoformat()
        sources.append({
            "key": key, "name": name, "homepage": home,
            "ok": ok, "error": err,
            "count": len(src_counts.get(key, set())),
            "last_sync": last,
        })
    external = [
        {"key": "mcsc", "name": "甘泉版本库", "homepage": "https://mc.mcsc.top",
         "note": "API 需合作伙伴令牌，未公开；页面已抓取（/resources/bedrock.php）。"},
        {"key": "mcversions", "name": "MCVersions", "homepage": "https://mcversions.net",
         "note": "Java 版本清单外链。"},
        {"key": "omniarchive", "name": "Omniarchive",
         "homepage": "https://omniarchive.net",
         "note": "Java 历史版本归档外链。"},
    ]
    catalog = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "duration_s": round(time.time() - t0, 1),
        "devices": catalog_devices,
        "sources": sources,
        "external": external,
    }
    (DATA / "catalog.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    log(f"catalog: {json.dumps({k: v['count'] for k, v in catalog_devices.items()})}")
    log(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
