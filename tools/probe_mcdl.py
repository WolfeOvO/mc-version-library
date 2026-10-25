#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCDL (mc.minebbs.com) 分页与下载渠道探针。

复现站点「下载按钮 → 下载通道 ①..④」背后的真实数据：

  * 列表接口 pageSize 上限 10，按 totalPages 翻页
  * downloadlinks 每条都是 /api/v1/redirect?url=<AES 密文>&id=<id>
  * 解析跳转必须带 Referer: https://mc.minebbs.com/，用 GET 且不跟随
  * 并发批量会被软限流（302 回首页），因此串行并保持 >=0.9s 间隔

用法：
    python3 tools/probe_mcdl.py --pages
    python3 tools/probe_mcdl.py --pages --resolve
"""
import argparse
import collections
import json
import pathlib
import time

import requests

BASE = "https://api.mc.minebbs.com/api/v1/versions/"
HOME = "https://mc.minebbs.com/"
OUT = pathlib.Path(__file__).resolve().parent.parent / "cache" / "mcdl-probe"

S = requests.Session()
S.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Referer": HOME,
    "Accept": "application/json",
})


def fetch(page, tries=5):
    for a in range(tries):
        try:
            r = S.get(BASE, params={"page": page, "pageSize": 10}, timeout=45)
            if "json" in r.headers.get("content-type", ""):
                return r.json()
        except Exception:
            pass
        time.sleep(3 * (a + 1))
    raise RuntimeError(f"page {page} failed")


def crawl():
    first = fetch(1)
    total = int(first.get("totalPages") or 1)
    pages = {1: first}
    print(f"totalPages={total}")
    for n in range(2, total + 1):
        time.sleep(0.35)
        pages[n] = fetch(n)
        if n % 10 == 0:
            print(f"  page {n}/{total}", flush=True)
    items = [i for p in pages.values() for i in (p.get("data") or [])]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pages.json").write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    print(f"items={len(items)} unique_ids={len({i['id'] for i in items})}")
    print("platform:", dict(collections.Counter(i.get("platform") for i in items)))
    print("environment:", dict(collections.Counter(i.get("environment") for i in items)))
    dist = collections.Counter(len(i.get("downloadlinks") or []) for i in items)
    print("downloadlinks per version:", dict(sorted(dist.items())))
    print("→ 下拉里的『下载通道 ①..n』= 链接数 - 1")
    return items


def resolve(items, pace=0.9):
    links = []
    for it in items:
        for l in it.get("downloadlinks") or []:
            if l not in links:
                links.append(l)
    print(f"resolving {len(links)} links at {pace}s/link (~{len(links)*pace/60:.0f} min)")
    res, hosts = {}, collections.Counter()
    for n, link in enumerate(links, 1):
        url = "https:" + link if link.startswith("//") else link
        time.sleep(pace)
        loc = ""
        try:
            r = S.get(url, allow_redirects=False, timeout=25)
            loc = r.headers.get("Location", "")
        except Exception as e:  # noqa: BLE001
            loc = f"ERR {type(e).__name__}"
        res[link] = loc
        home = not loc or "mc.minebbs.com" in loc
        hosts["(dead/throttled→home)" if home else loc.split("/")[2]] += 1
        if n % 25 == 0:
            print(f"  {n}/{len(links)}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "links.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print("\n跳转目标分布：")
    for h, c in hosts.most_common(20):
        print(f"  {c:5d}  {h}")
    print("\n注意：(dead/throttled→home) 多半是限流；隔几秒重试单条即可区分真死链")
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", action="store_true", help="抓全部分页并统计")
    ap.add_argument("--resolve", action="store_true", help="逐条解析 302 目标（慢）")
    ap.add_argument("--pace", type=float, default=0.9, help="解析间隔秒数")
    a = ap.parse_args()
    items = crawl() if a.pages or a.resolve else []
    if a.resolve:
        resolve(items, a.pace)


if __name__ == "__main__":
    main()
