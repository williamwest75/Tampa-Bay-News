#!/usr/bin/env python3
"""
Tampa Bay News Monitor — v2
============================
Source types (sources.json):
  rss        — outlet/agency feeds (also YouTube channel feeds, DVIDS)
  page       — press-release pages, diffed by headline links
  json_api   — CAD/active-call JSON endpoints (Pinellas EMS, PCSO, HFR...)
  html_table — CAD/incident pages rendered as HTML tables (FHP)
  imap       — PIO email inbox (press releases w/ photo attachments)

Per-source options:
  section          — news | pressrelease | cad | military | business
  site_only        — true: appears on site + RSS, never in email digest
  include_pattern  — regex; only titles matching are reported
  tag              — prefix like "[ACTIVE CALL]"
  enabled          — false to park a source

Outputs: state.json, items.json (rolling archive for the site),
feed.xml, email_digest.txt / email_subject.txt, docs/media/* (images).
"""

import argparse
import email
import email.policy
import hashlib
import html
import imaplib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime, parseaddr
from pathlib import Path
from urllib.parse import urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

BASE_DIR = Path(__file__).parent
SOURCES_FILE = BASE_DIR / "sources.json"
WATCHLIST_FILE = BASE_DIR / "watchlist.json"
STATE_FILE = BASE_DIR / "state.json"
ITEMS_FILE = BASE_DIR / "items.json"
DIGEST_FILE = BASE_DIR / "email_digest.txt"
SUBJECT_FILE = BASE_DIR / "email_subject.txt"
FEED_FILE = BASE_DIR / "feed.xml"
MEDIA_DIR = BASE_DIR / "docs" / "media"

USER_AGENT = ("Mozilla/5.0 (compatible; TampaBayMonitor/2.0; "
              "personal news-tracking script)")
TIMEOUT = 30
MAX_FEED_ITEMS = 200
MAX_ARCHIVE_ITEMS = 500
MAX_SEEN_PER_SOURCE = 800
BREAKING_PATTERNS = re.compile(
    r"\b(breaking|developing|urgent|amber alert|silver alert|"
    r"shelter in place|evacuat|tornado warning|hurricane warning|"
    r"officer.involved|structure fire|fatal|homicide|shooting)\b",
    re.IGNORECASE,
)
VALID_SECTIONS = {"news", "pressrelease", "cad", "military", "business"}


def now_utc():
    return datetime.now(timezone.utc)


def norm_link(url):
    if not url:
        return ""
    return re.sub(r"[?#].*$", "", url.strip()).rstrip("/").lower()


def item_key(basis_link, basis_text):
    basis = norm_link(basis_link) or basis_text.strip().lower()
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def fetch(url):
    resp = requests.get(url, headers={"User-Agent": USER_AGENT},
                        timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.text


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"[warn] could not parse {path.name}", file=sys.stderr)
    return default


def clean_text(s, limit=400):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    return re.sub(r"\s+", " ", s).strip()[:limit]


# ----------------------------------------------------------------------
# Source handlers — each returns [{key,title,link,summary,published,images}]
# ----------------------------------------------------------------------

def parse_rss(source, raw):
    parsed = feedparser.parse(raw)
    items = []
    for e in parsed.entries[:50]:
        title = clean_text(e.get("title", ""), 300)
        if not title:
            continue
        link = e.get("link", "")
        guid = e.get("id", "") or link
        images = []
        for enc in e.get("media_thumbnail", []) or []:
            if enc.get("url"):
                images.append(enc["url"])
        items.append({"key": item_key(guid, title), "title": title,
                      "link": link, "summary": clean_text(e.get("summary", "")),
                      "published": e.get("published", "") or e.get("updated", ""),
                      "images": images[:1]})
    return items


def parse_page(source, raw):
    soup = BeautifulSoup(raw, "html.parser")
    scope = soup
    if source.get("selector"):
        found = soup.select(source["selector"])
        if found:
            scope = BeautifulSoup("".join(str(f) for f in found),
                                  "html.parser")
    pattern = source.get("link_pattern")
    min_len = source.get("min_title_len", 20)
    base = source["url"]
    items, seen_here = [], set()
    for a in scope.find_all("a", href=True):
        title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
        href = a["href"].strip()
        if len(title) < min_len:
            continue
        if href.startswith(("javascript:", "mailto:", "#")):
            continue
        if pattern and not re.search(pattern, href):
            continue
        link = urljoin(base, href)
        if urlparse(link).netloc and \
           urlparse(link).netloc != urlparse(base).netloc:
            continue
        key = item_key(link, title)
        if key in seen_here:
            continue
        seen_here.add(key)
        items.append({"key": key, "title": title, "link": link,
                      "summary": "", "published": "", "images": []})
    return items[:60]


def dig(data, path):
    """Walk a dot-path into nested dicts/lists ('' returns data as-is)."""
    for part in [p for p in path.split(".") if p]:
        data = data[int(part)] if isinstance(data, list) else data[part]
    return data


def parse_json_api(source, raw):
    """Generic CAD/JSON adapter.

    Config:
      items_path    — dot path to the list of records ("" if top-level)
      field_map     — {"id": key, "type": key, "location": key,
                       "time": key, "agency": key}  (missing keys ok)
      title_template— e.g. "{type} — {location}" (default)
    """
    data = json.loads(raw)
    records = dig(data, source.get("items_path", ""))
    fmap = source.get("field_map", {})
    template = source.get("title_template", "{type} — {location}")
    items = []
    for rec in records[:150]:
        vals = {name: str(rec.get(key, "")).strip()
                for name, key in fmap.items()}
        title = clean_text(
            template.format(**{k: vals.get(k, "") for k in
                               ("id", "type", "location", "time", "agency")}),
            300).strip(" —-")
        if not title:
            continue
        basis = vals.get("id") or title + vals.get("time", "")
        items.append({"key": item_key("", f"{source['id']}|{basis}"),
                      "title": title, "link": source.get("public_url",
                                                         source["url"]),
                      "summary": vals.get("time", ""), "published": "",
                      "images": []})
    return items


def parse_html_table(source, raw):
    """CAD/incident pages rendered as HTML tables (e.g. FHP).

    Config:
      selector    — CSS selector for the table (default: first <table>)
      title_cols  — column indices joined into the title (default: all)
      id_cols     — column indices forming the stable identity
                    (default: same as title_cols)
      skip_rows   — header rows to skip (default 1)
    """
    soup = BeautifulSoup(raw, "html.parser")
    table = (soup.select_one(source["selector"])
             if source.get("selector") else soup.find("table"))
    if table is None:
        return []
    title_cols = source.get("title_cols")
    id_cols = source.get("id_cols", title_cols)
    items = []
    rows = table.find_all("tr")[source.get("skip_rows", 1):]
    for row in rows[:150]:
        cells = [re.sub(r"\s+", " ", td.get_text(" ", strip=True))
                 for td in row.find_all(["td", "th"])]
        if not cells or not any(cells):
            continue
        pick = (lambda idxs: [cells[i] for i in idxs if i < len(cells)])
        title = clean_text(" — ".join(
            pick(title_cols) if title_cols else cells), 300)
        if len(title) < 5:
            continue
        ident = "|".join(pick(id_cols) if id_cols else cells)
        items.append({"key": item_key("", f"{source['id']}|{ident}"),
                      "title": title,
                      "link": source.get("public_url", source["url"]),
                      "summary": clean_text(" · ".join(c for c in cells
                                                       if c), 200),
                      "published": "", "images": []})
    return items


def ingest_imap(source):
    """PIO press-release inbox. Credentials via environment:
    MONITOR_IMAP_HOST / MONITOR_IMAP_USER / MONITOR_IMAP_PASS

    Config: allowed_senders — list of substrings matched against the
    From address (e.g. "@pinellassheriff.gov"). Others are left unread
    but skipped. Image attachments are saved to docs/media/.
    """
    host = os.environ.get("MONITOR_IMAP_HOST")
    user = os.environ.get("MONITOR_IMAP_USER")
    pw = os.environ.get("MONITOR_IMAP_PASS")
    if not (host and user and pw):
        print("[info] imap: credentials not set, skipping",
              file=sys.stderr)
        return []
    allowed = [s.lower() for s in source.get("allowed_senders", [])]
    items = []
    box = imaplib.IMAP4_SSL(host)
    try:
        box.login(user, pw)
        box.select(source.get("folder", "INBOX"))
        _, data = box.search(None, "UNSEEN")
        for num in (data[0].split() if data and data[0] else [])[:25]:
            _, msg_data = box.fetch(num, "(RFC822)")
            msg = email.message_from_bytes(msg_data[0][1],
                                           policy=email.policy.default)
            sender = parseaddr(msg.get("From", ""))[1].lower()
            if allowed and not any(a in sender for a in allowed):
                continue  # stays marked seen; not from a PIO list
            subject = clean_text(msg.get("Subject", ""), 300)
            body_part = msg.get_body(preferencelist=("plain", "html"))
            body = clean_text(body_part.get_content() if body_part else "",
                              600)
            images = []
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            for part in msg.iter_attachments():
                ctype = part.get_content_type()
                if ctype in ("image/jpeg", "image/png", "image/webp"):
                    ext = ctype.split("/")[1].replace("jpeg", "jpg")
                    fname = (f"{now_utc().strftime('%Y%m%d%H%M%S')}_"
                             f"{item_key('', subject)[:8]}_"
                             f"{len(images)}.{ext}")
                    (MEDIA_DIR / fname).write_bytes(
                        part.get_payload(decode=True))
                    images.append(f"media/{fname}")
            items.append({"key": item_key("", f"{sender}|{subject}|"
                                              f"{msg.get('Date','')}"),
                          "title": f"{subject}",
                          "link": "", "summary": body,
                          "published": msg.get("Date", ""),
                          "images": images[:4]})
    finally:
        try:
            box.logout()
        except Exception:
            pass
    return items


HANDLERS = {"rss": parse_rss, "page": parse_page,
            "json_api": parse_json_api, "html_table": parse_html_table}


# ----------------------------------------------------------------------
# Watchlist / outputs
# ----------------------------------------------------------------------

def load_watchlist():
    wl = load_json(WATCHLIST_FILE, {})
    return {"keywords": [k.lower() for k in wl.get("keywords", [])],
            "keyword_alert_only": bool(wl.get("keyword_alert_only", False))}


def write_digest(new_by_source, run_time, any_watch, any_break):
    lines = ["TAMPA BAY NEWS MONITOR",
             run_time.strftime("Run: %A, %B %d, %Y at %I:%M %p UTC"), ""]
    total = 0
    for src_name, its in new_by_source.items():
        if not its:
            continue
        lines.append(f"=== {src_name} ({len(its)} new) ===")
        for it in its:
            flags = ""
            if it["watchlist_hits"]:
                flags += f" [WATCHLIST: {', '.join(it['watchlist_hits'])}]"
            if it["breaking"]:
                flags += " [BREAKING]"
            lines.append(f"* {it['title']}{flags}")
            if it["summary"]:
                lines.append(f"  {it['summary']}")
            if it["link"]:
                lines.append(f"  {it['link']}")
            lines.append("")
        total += len(its)
    DIGEST_FILE.write_text("\n".join(lines), encoding="utf-8")
    subject = f"TB Monitor: {total} new item{'s' if total != 1 else ''}"
    if any_break:
        subject = "[BREAKING] " + subject
    if any_watch:
        subject = "[WATCHLIST] " + subject
    SUBJECT_FILE.write_text(subject, encoding="utf-8")
    return subject


def rebuild_feed(new_flat, run_time):
    existing = []
    if FEED_FILE.exists():
        try:
            old = feedparser.parse(FEED_FILE.read_text(encoding="utf-8"))
            existing = [{"title": e.get("title", ""),
                         "link": e.get("link", ""),
                         "summary": e.get("summary", ""),
                         "pubdate": e.get("published", ""),
                         "guid": e.get("id", "") or e.get("link", "")}
                        for e in old.entries]
        except Exception:
            pass
    stamp = format_datetime(run_time)
    fresh = []
    for it in new_flat:
        prefix = ""
        if it["watchlist_hits"]:
            prefix += f"[WATCHLIST: {', '.join(it['watchlist_hits'])}] "
        if it["breaking"]:
            prefix += "[BREAKING] "
        if it.get("tag"):
            prefix += it["tag"] + " "
        fresh.append({"title": prefix + f"[{it['source']}] " + it["title"],
                      "link": it["link"], "summary": it["summary"],
                      "pubdate": it["published"] or stamp,
                      "guid": it["key"]})
    combined = (fresh + existing)[:MAX_FEED_ITEMS]

    def esc(s):
        return html.escape(s or "", quote=False)
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<rss version="2.0"><channel>',
           "<title>Tampa Bay News Monitor</title>",
           "<link>https://github.com</link>",
           "<description>Press releases and breaking news, Tampa Bay"
           "</description>", f"<lastBuildDate>{stamp}</lastBuildDate>"]
    for it in combined:
        out += ["<item>", f"<title>{esc(it['title'])}</title>"]
        if it["link"]:
            out.append(f"<link>{esc(it['link'])}</link>")
        if it["summary"]:
            out.append(f"<description>{esc(it['summary'])}</description>")
        out += [f'<guid isPermaLink="false">{esc(it["guid"])}</guid>',
                f"<pubDate>{esc(it['pubdate'])}</pubDate>", "</item>"]
    out.append("</channel></rss>")
    FEED_FILE.write_text("\n".join(out), encoding="utf-8")


def update_archive(new_flat, run_time):
    archive = load_json(ITEMS_FILE, [])
    stamp = run_time.isoformat()
    for it in new_flat:
        archive.insert(0, {
            "key": it["key"], "title": it["title"], "link": it["link"],
            "summary": it["summary"], "source": it["source"],
            "section": it["section"], "tag": it.get("tag", ""),
            "watchlist_hits": it["watchlist_hits"],
            "breaking": it["breaking"], "images": it.get("images", []),
            "first_seen": stamp, "published": it["published"]})
    ITEMS_FILE.write_text(
        json.dumps(archive[:MAX_ARCHIVE_ITEMS], indent=1),
        encoding="utf-8")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sources = load_json(SOURCES_FILE, [])
    if not sources:
        sys.exit("No sources configured in sources.json")
    watchlist = load_watchlist()
    state = load_json(STATE_FILE, {})
    run_time = now_utc()

    new_by_source, flat = {}, []

    for source in sources:
        if not source.get("enabled", True):
            continue
        sid, name = source["id"], source["name"]
        stype = source["type"]
        section = source.get("section", "news")
        if section not in VALID_SECTIONS:
            section = "news"
        try:
            if stype == "imap":
                items = ingest_imap(source)
            elif stype in HANDLERS:
                items = HANDLERS[stype](source, fetch(source["url"]))
            else:
                print(f"[warn] unknown type for {sid}", file=sys.stderr)
                continue
        except Exception as exc:
            print(f"[warn] {sid}: {exc}", file=sys.stderr)
            continue

        inc = source.get("include_pattern")
        if inc:
            items = [it for it in items
                     if re.search(inc, f"{it['title']} {it['summary']}",
                                  re.IGNORECASE)]

        seen = set(state.get(sid, {}).get("seen", []))
        is_first_run = sid not in state
        new_items = []
        for it in items:
            if it["key"] in seen:
                continue
            hits = [kw for kw in watchlist["keywords"]
                    if kw in f"{it['title']} {it['summary']}".lower()]
            it.update(watchlist_hits=hits, source=name, section=section,
                      tag=source.get("tag", ""),
                      breaking=bool(BREAKING_PATTERNS.search(it["title"])))
            if watchlist["keyword_alert_only"] and not hits:
                pass
            elif not is_first_run:
                new_items.append(it)

        all_keys = [it["key"] for it in items] + list(seen)
        deduped, keep = [], set()
        for k in all_keys:
            if k not in keep:
                keep.add(k)
                deduped.append(k)
        state[sid] = {"seen": deduped[:MAX_SEEN_PER_SOURCE],
                      "last_checked": run_time.isoformat()}

        if new_items:
            flat.extend(new_items)
            if not source.get("site_only", False):
                new_by_source[name] = new_items
            print(f"[info] {name}: {len(new_items)} new"
                  f"{' (site only)' if source.get('site_only') else ''}")
        else:
            note = " (state primed)" if is_first_run else ""
            print(f"[info] {name}: no new items{note}")
        if stype != "imap":
            time.sleep(1)

    emailable = [it for its in new_by_source.values() for it in its]
    any_watch = any(it["watchlist_hits"] for it in emailable)
    any_break = any(it["breaking"] for it in emailable)

    if args.dry_run:
        print(f"[dry-run] {len(flat)} new ({len(emailable)} emailable); "
              "state not saved")
        return

    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    if flat:
        update_archive(flat, run_time)
        rebuild_feed(flat, run_time)
    if emailable:
        print(f"[info] digest: "
              f"{write_digest(new_by_source, run_time, any_watch, any_break)}")
    else:
        for f in (DIGEST_FILE, SUBJECT_FILE):
            if f.exists():
                f.unlink()
        print("[info] nothing emailable this run")


if __name__ == "__main__":
    main()
