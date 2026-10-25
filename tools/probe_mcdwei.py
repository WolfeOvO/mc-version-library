#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcd.wei.qzz.io (MCDownload) 探针：全量版本列表 + 每个 (版本, 架构) 的镜像渠道。

接口：apiBase = https://a.p.z.ruanhor.dpdns.org
  GET /mcdownload/download/mcbe/list[?major=1.21]      基岩 Android（26.xx 会折叠成 26.x）
  GET /mcdownload/download/mcpc/list                    基岩 Windows（带 archs）
  GET /mcdownload/download/mcbe/download_link?version=&type=arm64-v8a|armeabi-v7a
  GET /mcdownload/download/mcpc/download_link?version=&arch=x64|x86|arm
"""
import json, time, collections, pathlib, threading
from concurrent.futures import ThreadPoolExecutor
import requests

API = "https://a.p.z.ruanhor.dpdns.org"
OUT = pathlib.Path(__file__).resolve().parent.parent / "cache" / "mcdwei"
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36",
                  "Origin": "https://mcd.wei.qzz.io", "Referer": "https://mcd.wei.qzz.io/"})
lock = threading.Lock()


def jget(path, tries=4):
    for a in range(tries):
        try:
            r = S.get(API + path, timeout=40)
            if r.status_code == 429:
                time.sleep(2 * (a + 1)); continue
            if r.status_code == 200:
                return r.json()
            return {"code": r.status_code, "data": None}
        except Exception:
            time.sleep(1.5 * (a + 1))
    return {"code": 0, "data": None}


t0 = time.time()
be = jget("/mcdownload/download/mcbe/list")["data"]
majors = be["majors"]
be_versions = {}
for m in majors:
    j = jget(f"/mcdownload/download/mcbe/list?major={m}")
    d = j.get("data") or {}
    for v in d.get("versions") or []:
        be_versions.setdefault(v["version"], {"beta": v.get("beta"), "date": v.get("date"), "size": v.get("size")})
print(f"mcbe majors={len(majors)} unique_versions={len(be_versions)} [{time.time()-t0:.0f}s]", flush=True)

pc = jget("/mcdownload/download/mcpc/list")["data"]
pc_versions = {v["version"]: {"beta": v.get("beta"), "date": v.get("date"),
                              "archs": v.get("archs") or ["x64"]} for v in pc["versions"]}
print(f"mcpc versions={len(pc_versions)} [{time.time()-t0:.0f}s]", flush=True)

jobs = [("mcbe", v, a) for v in be_versions for a in ("arm64-v8a", "armeabi-v7a")]
jobs += [("mcpc", v, a) for v, meta in pc_versions.items() for a in meta["archs"]]
print(f"link jobs={len(jobs)}", flush=True)

links = {}


def work(job):
    kind, ver, arch = job
    if kind == "mcbe":
        path = f"/mcdownload/download/mcbe/download_link?version={ver}&type={arch}"
    else:
        path = f"/mcdownload/download/mcpc/download_link?version={ver}&arch={arch}"
    time.sleep(0.08)
    j = jget(path)
    dl = ((j.get("data") or {}).get("data") or {}).get("downloads") or []
    with lock:
        links[f"{kind}|{ver}|{arch}"] = dl


with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(work, jobs))
print(f"resolved {len(links)} link sets [{time.time()-t0:.0f}s]", flush=True)

prov = collections.Counter()
empty = 0
for k, dl in links.items():
    if not dl:
        empty += 1
    for x in dl:
        prov[(x.get("type") or "?", x.get("name"))] += 1
print("empty sets:", empty)
print("channels:")
for (t, n), c in prov.most_common(20):
    print(f"  {c:5d}  {t:16s} {n}")

archs = collections.Counter(len(dl) for dl in links.values())
print("downloads per (version,arch):", dict(sorted(archs.items())))

(OUT / "data.json").write_text(json.dumps({"mcbe": be_versions, "mcpc": pc_versions, "links": links},
                                          ensure_ascii=False), encoding="utf-8")
print("saved", OUT / "data.json", f"total {time.time()-t0:.0f}s")
