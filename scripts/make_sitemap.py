#!/usr/bin/env python3
"""Generate sitemap.xml + robots.txt for the built MkDocs site."""
import datetime
import os
from pathlib import Path
from xml.sax.saxutils import escape

SITE = Path(os.environ.get("SITE_DIR", Path(__file__).resolve().parent.parent / "site"))
HOST = os.environ.get("SITE_URL", "https://example.com").rstrip("/")
SITE_URL = HOST + "/"


def url_for(p: Path) -> str:
    rel = p.relative_to(SITE).as_posix()
    if rel.endswith("index.html"):
        rel = rel[: -len("index.html")]
    return SITE_URL + rel


def main():
    pages = sorted(SITE.rglob("*.html"))
    rows = []
    for p in pages:
        rel = p.relative_to(SITE).as_posix()
        if rel == "404.html":
            continue
        loc = url_for(p)
        lastmod = datetime.datetime.utcfromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d")
        rows.append(f"  <url><loc>{escape(loc)}</loc><lastmod>{lastmod}</lastmod></url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows)
        + "\n</urlset>\n"
    )
    (SITE / "sitemap.xml").write_text(xml, encoding="utf-8")
    (SITE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {HOST}/sitemap.xml\n", encoding="utf-8"
    )
    print(f"sitemap: {len(rows)} urls")


if __name__ == "__main__":
    main()
