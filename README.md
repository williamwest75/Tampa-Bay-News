# The Tampa Bay Wire — News Monitor & Self-Publishing Site

One repo, four jobs: monitor sources → email digest → RSS feed → static
news site (GitHub Pages). Runs every 30 minutes on GitHub Actions, $0.

## Architecture

```
sources.json ──► tampabay_monitor.py ──► state.json      (diff memory)
                        │                items.json      (site archive)
                        │                feed.xml        (RSS)
                        │                email_digest.txt (when news)
                        └── docs/media/  (photos from PIO emails)
                 sitegen.py ──► docs/index.html  (GitHub Pages site)
```

## Source types
- **rss** — outlet feeds, YouTube channels (`https://www.youtube.com/feeds/videos.xml?channel_id=...`), DVIDS, MacDill
- **page** — newsroom pages diffed by headline links (sheriffs, cities, counties, CENTCOM, SOCOM)
- **json_api** — CAD/active-call JSON endpoints (Pinellas EMS, PCSO, Hillsborough Fire Rescue)
- **html_table** — incident pages rendered as tables (FHP)
- **imap** — the PIO press-list inbox (see below)

Per-source options: `section` (news / pressrelease / cad / military /
business), `site_only` (site+RSS but never email — use for noisy CAD),
`include_pattern` (regex vs. title+row text — e.g. your 8 counties on
FHP), `tag`, `enabled`.

## Setup
1. Push this repo. Add secrets: `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_TO`.
2. **Enable GitHub Pages:** repo Settings → Pages → Deploy from branch →
   `main`, folder `/docs`. Your site appears at
   `https://<user>.github.io/<repo>/` within a few minutes of the first run.
3. Run locally first: `pip install requests beautifulsoup4 feedparser`
   then `python3 tampabay_monitor.py --dry-run`. Fix/disable any source
   that errors.

## Configuring the CAD sources (one-time, ~10 min each)
The EMS/Sheriff/Fire Rescue active-call pages are JavaScript apps that
load their data from a JSON endpoint. To wire one up:
1. Open the page in Chrome → F12 → **Network** tab → filter **Fetch/XHR** → reload.
2. Click through the requests until you see one whose Response is a JSON
   list of calls. Right-click → Copy URL.
3. Paste it as the source's `url` in sources.json, set `items_path` to
   the dot-path of the list inside the JSON (empty string if the response
   IS the list), and map `field_map` to the real key names you see
   (incident number, call type, address, time).
4. Set `"enabled": true`, run `--dry-run`, confirm sane titles.
If an endpoint requires headers/tokens the plain fetch can't provide,
tell Claude what you see in DevTools and we'll extend the adapter.

FHP: the `html_table` source points at the incident page; if the table
lives in an iframe, point `url` at the iframe's own address instead.
The `include_pattern` keeps only your 8 counties.

## The PIO inbox (the social-media workaround)
1. Create a dedicated Gmail (e.g. tbwire.desk@gmail.com) + app password.
2. Join every PIO/media distribution list with that address; subscribe to
   CENTCOM/SOCOM email releases (GovDelivery) with it too.
3. Add secrets `MONITOR_IMAP_HOST` (imap.gmail.com), `MONITOR_IMAP_USER`,
   `MONITOR_IMAP_PASS`.
4. Keep `allowed_senders` in sources.json updated as you join lists —
   anything not matching is ignored (spam can't inject items).
Attached JPG/PNGs are saved to `docs/media/` and shown on the site with
the release. Agency handout images are distributed for media use;
military/DVIDS imagery is public domain.

## Editorial guardrails baked in
- CAD entries are `site_only` and the site labels them
  **unverified dispatch data** — they're your tip sheet, not reporting.
- Headlines link to the original source; summaries are short. Your
  original reporting is what goes beyond this.
- First run of any source primes state silently (no alert flood).

## Costs & scaling
~24 sources ≈ 1 min/run ≈ 1,450 Actions-min/month: unlimited on a public
repo, most of the 2,000 free minutes on a private one. Phase in the
remaining ~20 newsrooms from the employer audit gradually and watch the
signal-to-noise before tightening the cron to */20.
