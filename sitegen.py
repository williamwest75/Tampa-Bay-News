#!/usr/bin/env python3
"""
Site generator for The Tampa Bay Wire.
Front page: 3 latest items per section + "View all" buttons.
Category pages: flagged.html, cad.html, news.html, pressreleases.html,
military.html, business.html — full listings.
Reads items.json; writes docs/*.html; copies feed.xml into docs/.
"""

import html
import json
import shutil
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path

EASTERN = ZoneInfo("America/New_York")
BASE = Path(__file__).parent
ITEMS_FILE = BASE / "items.json"
FEED_FILE = BASE / "feed.xml"
DOCS = BASE / "docs"

FRONT_PAGE_COUNT = 3
CATEGORY_PAGE_COUNT = 120

SECTIONS = [
    ("cad", "Active Incidents", "cad.html",
     "Unverified dispatch data — initial call types are frequently "
     "revised. Not confirmed reporting."),
    ("news", "Latest News", "news.html", ""),
    ("pressrelease", "Press Releases & Official Statements",
     "pressreleases.html", ""),
    ("military", "MacDill · CENTCOM · SOCOM", "military.html", ""),
    ("business", "Business & Filings", "business.html", ""),
]

CSS = """
:root{
  --paper:#fbfaf7; --ink:#17242a; --dim:#5c6b70;
  --gulf:#0f5e63; --gulf-soft:#e3eeee;
  --flag:#e04e00; --flag-soft:#fcece2;
  --rule:#d9d5cc; --mono:'IBM Plex Mono',ui-monospace,Menlo,monospace;
  --sans:'Archivo',system-ui,-apple-system,sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--paper);color:var(--ink);font-family:var(--sans);
  line-height:1.45;-webkit-font-smoothing:antialiased}
a{color:inherit;text-decoration:none}
a:hover .t,a:focus .t{text-decoration:underline;
  text-decoration-color:var(--gulf);text-underline-offset:3px}
a:focus-visible{outline:2px solid var(--gulf);outline-offset:3px}
.wrap{max-width:780px;margin:0 auto;padding:0 20px 80px}
header{padding:34px 0 18px;border-bottom:3px solid var(--ink)}
.brand{font-weight:800;font-size:clamp(26px,5vw,40px);
  letter-spacing:-.02em;line-height:1.05}
.brand em{font-style:normal;color:var(--gulf)}
.meta{font-family:var(--mono);font-size:12px;color:var(--dim);
  margin-top:10px;display:flex;gap:18px;flex-wrap:wrap}
.meta .dot{color:var(--flag)}
.crumb{font-family:var(--mono);font-size:12px;margin-top:12px}
.crumb a{color:var(--gulf);text-decoration:underline}
.pinned{margin-top:26px}
.pinned h2, section h2{font-size:13px;font-weight:700;
  letter-spacing:.14em;text-transform:uppercase;color:var(--gulf);
  border-bottom:1px solid var(--rule);padding-bottom:7px;margin-bottom:2px}
.pinned h2{color:var(--flag)}
.note{font-family:var(--mono);font-size:11px;color:var(--dim);
  padding:6px 0 2px}
section{margin-top:30px}
.item{display:grid;grid-template-columns:118px 1fr;gap:14px;
  padding:13px 0;border-bottom:1px solid var(--rule)}
.ts{font-family:var(--mono);font-size:11.5px;color:var(--dim);
  padding-top:3px;white-space:nowrap}
.t{font-size:16.5px;font-weight:600;letter-spacing:-.01em}
.pinned .t{font-size:18px}
.s{font-size:14px;color:var(--dim);margin-top:3px}
.src{font-family:var(--mono);font-size:11px;color:var(--gulf);
  margin-top:5px;text-transform:uppercase;letter-spacing:.05em}
.badge{display:inline-block;font-family:var(--mono);font-size:10.5px;
  font-weight:600;letter-spacing:.06em;padding:1px 7px;margin-right:7px;
  border-radius:3px;vertical-align:2px}
.badge.brk{background:var(--flag);color:#fff}
.badge.wl{background:var(--flag-soft);color:var(--flag);
  border:1px solid var(--flag)}
.badge.tag{background:var(--gulf-soft);color:var(--gulf)}
.imgs{display:flex;gap:8px;margin-top:9px;flex-wrap:wrap}
.imgs img{max-height:130px;border-radius:4px;border:1px solid var(--rule)}
.empty{font-family:var(--mono);font-size:12.5px;color:var(--dim);
  padding:14px 0}
.more{display:block;width:100%;text-align:center;margin-top:2px;
  padding:11px 0;font-family:var(--mono);font-size:12px;font-weight:600;
  letter-spacing:.1em;text-transform:uppercase;color:var(--gulf);
  border:1px solid var(--rule);border-top:none;background:transparent;
  cursor:pointer}
.more:hover{background:var(--gulf-soft)}
footer{margin-top:56px;padding-top:16px;border-top:3px solid var(--ink);
  font-family:var(--mono);font-size:11.5px;color:var(--dim)}
footer a{color:var(--gulf);text-decoration:underline}
@media(max-width:540px){.item{grid-template-columns:1fr;gap:3px}
  .ts{padding-top:0}}
@media(prefers-reduced-motion:no-preference){
  .pinned .item{animation:in .4s ease both}
  @keyframes in{from{opacity:0;transform:translateY(4px)}to{opacity:1}}}
"""


def esc(s):
    return html.escape(s or "")


def fmt_ts(iso):
    try:
        dt = datetime.fromisoformat(iso).astimezone(EASTERN)
        out = dt.strftime("%b %d · %I:%M %p")
        head, _, tail = out.partition(" · ")
        return head + " · " + tail.lstrip("0")
    except (ValueError, TypeError):
        return ""


def now_stamp():
    return datetime.now(timezone.utc).astimezone(EASTERN) \
        .strftime("%b %d, %Y · %I:%M %p").replace(" · 0", " · ") + " ET"


def render_item(it, big=False):
    badges = ""
    if it.get("breaking"):
        badges += '<span class="badge brk">BREAKING</span>'
    for kw in it.get("watchlist_hits", []):
        badges += f'<span class="badge wl">WATCH: {esc(kw.upper())}</span>'
    if it.get("tag"):
        badges += (f'<span class="badge tag">'
                   f'{esc(it["tag"].strip("[]"))}</span>')
    imgs = ""
    if it.get("images"):
        pics = "".join(f'<img src="{esc(u)}" alt="" loading="lazy">'
                       for u in it["images"][:4])
        imgs = f'<div class="imgs">{pics}</div>'
    summary = (f'<div class="s">{esc(it["summary"])}</div>'
               if it.get("summary") else "")
    src = f'<div class="src">{esc(it.get("source", ""))}</div>'
    inner = (f'<div class="ts">{fmt_ts(it.get("first_seen", ""))}</div>'
             f'<div>{badges}<span class="t">{esc(it["title"])}</span>'
             f'{summary}{src}{imgs}</div>')
    if it.get("link"):
        return (f'<a class="item" href="{esc(it["link"])}" '
                f'target="_blank" rel="noopener">{inner}</a>')
    return f'<div class="item">{inner}</div>'


def page_shell(title, body, crumb=""):
    crumb_html = (f'<div class="crumb">'
                  f'<a href="index.html">← The Tampa Bay Wire front page'
                  f'</a></div>') if crumb else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="300">
<title>{esc(title)}</title>
<link rel="alternate" type="application/rss+xml" href="feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;600;800&family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><div class="wrap">
<header>
  <div class="brand">The Tampa Bay <em>Wire</em></div>
  <div class="meta">
    <span><span class="dot">●</span> LIVE MONITOR</span>
    <span>UPDATED {now_stamp()}</span>
    <span>8 COUNTIES</span>
    <span><a href="feed.xml">RSS</a></span>
  </div>
  {crumb_html}
</header>
{body}
<footer>Automated monitor of official sources across Citrus, Hernando,
Pasco, Pinellas, Hillsborough, Polk, Manatee &amp; Sarasota counties.
Active-incident entries are unverified dispatch data.
Headlines link to their original sources.
Subscribe: <a href="feed.xml">RSS feed</a>.</footer>
</div></body></html>"""


def category_page(title, items, note=""):
    note_html = f'<div class="note">{esc(note)}</div>' if note else ""
    rows = ("".join(render_item(it) for it in items) if items
            else '<div class="empty">Nothing logged yet.</div>')
    body = (f'<section><h2>{esc(title)} — full log</h2>{note_html}'
            f'{rows}</section>')
    return page_shell(f"{title} — The Tampa Bay Wire", body, crumb="back")


def build():
    items = json.loads(ITEMS_FILE.read_text(encoding="utf-8")) \
        if ITEMS_FILE.exists() else []
    DOCS.mkdir(parents=True, exist_ok=True)

    flagged = [it for it in items
               if it.get("breaking") or it.get("watchlist_hits")]

    # ---- front page ----
    parts = []
    front_flagged = flagged[:FRONT_PAGE_COUNT]
    if front_flagged:
        rows = "".join(render_item(it) for it in front_flagged)
        more = ('<a class="more" href="flagged.html">'
                'View all flagged →</a>') \
            if len(flagged) > FRONT_PAGE_COUNT else ""
        parts.append(f'<div class="pinned"><h2>Flagged</h2>{rows}{more}'
                     f'</div>')

    front_flagged_keys = {it["key"] for it in front_flagged}
    for sec_id, sec_title, sec_file, sec_note in SECTIONS:
        sec_items = [it for it in items if it.get("section") == sec_id]
        front = [it for it in sec_items
                 if it["key"] not in front_flagged_keys][:FRONT_PAGE_COUNT]
        note = f'<div class="note">{esc(sec_note)}</div>' if sec_note else ""
        rows = ("".join(render_item(it) for it in front) if front
                else '<div class="empty">Nothing new logged.</div>')
        more = (f'<a class="more" href="{sec_file}">View all '
                f'{esc(sec_title)} →</a>') if sec_items else ""
        parts.append(f'<section><h2>{esc(sec_title)}</h2>{note}{rows}'
                     f'{more}</section>')

    (DOCS / "index.html").write_text(
        page_shell("The Tampa Bay Wire — breaking news & press releases",
                   "".join(parts)), encoding="utf-8")

    # ---- category pages ----
    (DOCS / "flagged.html").write_text(
        category_page("Flagged", flagged[:CATEGORY_PAGE_COUNT]),
        encoding="utf-8")
    for sec_id, sec_title, sec_file, sec_note in SECTIONS:
        sec_items = [it for it in items
                     if it.get("section") == sec_id][:CATEGORY_PAGE_COUNT]
        (DOCS / sec_file).write_text(
            category_page(sec_title, sec_items, sec_note),
            encoding="utf-8")

    (DOCS / ".nojekyll").write_text("", encoding="utf-8")
    if FEED_FILE.exists():
        shutil.copy(FEED_FILE, DOCS / "feed.xml")
    print(f"[info] site built: {len(items)} items, "
          f"{len(flagged)} flagged, {1 + 1 + len(SECTIONS)} pages")


if __name__ == "__main__":
    build()
