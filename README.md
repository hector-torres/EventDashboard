# Event Trading Terminal — v1.5
**KLTT Holdings** | Internal research tool

Real-time event detection and market intelligence terminal. Monitors Bluesky for breaking news, detects developing events through NLP and velocity analysis, surfaces relevant Kalshi prediction markets, and provides a live market detail view with Polymarket comparison.

---

## Table of Contents

1. [Overview](#overview)
2. [Pages & Navigation](#pages--navigation)
3. [Architecture](#architecture)
4. [Module Reference](#module-reference)
5. [Noise Scoring System](#noise-scoring-system)
6. [Event Detection Engine](#event-detection-engine)
7. [NLP Enhancement Layer](#nlp-enhancement-layer)
8. [Semantic Market Matcher](#semantic-market-matcher)
9. [Market Dashboard — All Markets](#market-dashboard--all-markets)
10. [Market Dashboard — Event Matches](#market-dashboard--event-matches)
11. [Market Dashboard — Expiry & Price Strategies](#market-dashboard--expiry--price-strategies)
12. [Market Detail Page](#market-detail-page)
13. [Market Indices Bar](#market-indices-bar)
14. [Gas Prices (AAA)](#gas-prices-aaa)
15. [API Reference](#api-reference)
16. [Setup & Running](#setup--running)
17. [Configuration & Tuning](#configuration--tuning)
18. [Known Limitations / Future Work](#known-limitations--future-work)
19. [Version History](#version-history)

---

## Overview

Polls a curated list of Bluesky news accounts every 30 seconds, runs multiple detection strategies, and continuously matches the resulting event corpus against open Kalshi prediction markets. A keyword sweep feed surfaces posts from the wider Bluesky public with configurable noise filtering. Clicking any market title opens a live detail page with pricing, orderbook, price history, position sizing, and Polymarket comparison.

**Four pages:** Home (`/`) · Event Dashboard (`/dashboard`) · Market Dashboard (`/markets`) · Market Detail (`/market_detail?ticker=TICKER`)

---

## Pages & Navigation

Title bar on all pages: **Event Trading Terminal** brand (16px bold) → nav links → right side. Market Dashboard right side has Syncing pill, Pause, Refresh, and KLTT Holdings.

### Event Dashboard (`/dashboard`)

| Panel | Contents |
|-------|---------|
| **News Accounts** | Live feed from tracked priority accounts. Collapsible **Tracked Accounts** bar (add/remove, persists to `accounts.txt`). |
| **Detected Events** | Events CRITICAL→HIGH→MEDIUM with lifecycle badges and semantic WHO — WHAT — WHERE titles. Collapsible **Spikes** bar. |
| **Keyword Sweep** | Broad keyword search feed. Collapsible **Keywords** bar (add/pause/remove, persists to `custom_feeds.json`). Noise Filter slider. |

All collapsible bars use the shared `.cbar` CSS system: `.cbar` → `.cbar-header` → `.cbar-arrow` + `.cbar-label` + `.cbar-count`, and `.cbar-body`. Collapse rotates arrow; count always visible.

The loading overlay on both Event Dashboard and Market Dashboard shows live Kalshi fetch progress: page count, running market count, elapsed time, and a step-by-step status indicator (Flask server → Bluesky feed → Kalshi API fetch → Market indexing → Ready).

### Market Dashboard (`/markets`)

Three columns, aligned via spacers: header → [spacers or strat-tabs] → **Category pills (all 13, always shown)** → column controls → paginated body → footer.

| Column | Controls |
|--------|---------|
| **All Markets** | Spacers + Category pills + Search + Price/Days sliders + Sort strip + Series list + Market cards |
| **Event Matches** | Spacer + Confidence slider (default ≥0.60) + Sort + Category pills + Results |
| **Expiry & Price Strategies** | Sub-tabs (default: **Extreme**) + Filter strip + Category pills + Results |

Market title text in all three columns is a hyperlink that opens the Market Detail page in a new tab.

Title bar right side includes a **Syncing pill** (blue `↻ Syncing` indicator) that appears non-blocking during hourly background Kalshi refreshes and manual Refresh clicks, disappearing when the pull completes.

Key layout: `overflow-y: hidden` on all paginated bodies (no internal scroll). `calibratePerPage()` measures actual card heights after first render and sets items-per-page dynamically. Sub-header removed; Pause/Refresh in title bar.

### Market Detail (`/market_detail?ticker=TICKER`)

Standalone page opened in a new tab from any market card. Makes three parallel live API calls on load (refreshable via header button):

1. `GET /api/kalshi/market/<ticker>` — live market data, orderbook, 30-day candlesticks
2. `GET /api/kalshi/match_detail` — semantic match breakdown (events + Bluesky posts)
3. `GET /api/polymarket/match` — fuzzy-matched Polymarket markets for comparison

**Sections (top to bottom):**
- **Hero card** — market title, ticker, category, time remaining, live dot, Kalshi deep link
- **All Outcomes** — for multi-outcome events (e.g. "Who will be X?"), shows all sibling markets sorted by YES ask with probability bars and links to their own detail pages
- **Polymarket Comparison** — top 5 fuzzy-matched Polymarket markets by similarity score, with odds, volume, end date, and similarity explanation
- **Context callouts** — break-even probability, price drift vs last trade, time value note
- **Position Sizer** — dollar input ($1–$100k) + log-scale slider; shows contracts, total cost, gross profit, total fees (correct per-contract formula), net profit, return %, and fill feasibility (depth check with price impact warning). Synced to YES/NO toggle.
- **YES / NO toggle** — switches pricing panels and sizer calculations between sides. Each panel shows: ask, bid, gross profit per contract, fee (~3% of profit, min $0.01/contract), net profit, return %.
- **Shared spread + volume row** — spread in `$0.XX` format with color coding (green ≤$0.01, amber $0.02–$0.03, red ≥$0.04), volume, liquidity, last trade
- **Order Book** — live bid depth for YES and NO sides, quantity bars clipped to row bounds
- **Price Chart** — 30-day YES ask history as SVG line chart with fill gradient, y-axis in `$0.XX` format
- **Matched Events** — events that drove the semantic match score
- **Matched Bluesky Posts** — individual posts that matched this market

All price values displayed as `$0.XX` (dollar format), not raw cents.

Collapsible `ⓘ` glossaries on Pricing, Order Book, Price Chart, Position Sizer, and Polymarket sections explain each data point in plain language.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Flask Server (port 5001)                       │
│  bluesky_feed ──poll 30s──► event_detector ──► nlp_enhancer      │
│       │                                             │ events      │
│  post_scorer (noise)                      kalshi_feed (hourly)    │
│  market_indices (Yahoo 60s) + gas_prices (AAA 3x/day)            │
│  market_detail ──live──► Kalshi API + Polymarket Gamma API        │
└──────────────────────────────────────────────────────────────────┘
```

---

## Module Reference

| File | Role |
|------|------|
| `app.py` | Flask routes, poll thread, corpus builder, live market + Polymarket routes |
| `bluesky_feed.py` | AT Protocol client, feed management, persistence |
| `post_scorer.py` | Modular noise scoring (10 active filters) |
| `event_detector.py` | Event detection strategies, quality gates v2.1, semantic title generation |
| `nlp_enhancer.py` | NER, negation, semantic dedup, zero-shot, historical reference detection |
| `kalshi_feed.py` | Kalshi API, market cache, semantic scoring, hourly refresh, fetch progress |
| `market_indices.py` | Yahoo Finance + AAA index polling |
| `gas_prices.py` | AAA gas price scraper |
| `dashboard.html` | Event Dashboard UI |
| `markets.html` | Market Dashboard UI |
| `market_detail.html` | Live market detail page |
| `index.html` | Landing page |

**Persistence files:**

| File | Contents |
|------|---------|
| `accounts.txt` | Tracked Bluesky handles (one per line, `#` comments preserved) |
| `custom_feeds.json` | User-added keyword feeds + disabled states for built-in feeds |

---

## Noise Scoring System

**File:** `post_scorer.py` | Priority account posts bypass scoring entirely.

| ID | Filter | Signal | Points |
|----|--------|---------|--------|
| F1 | Content length | < 20 chars | +5 |
| F2 | Account age | < 3d / 3–7d | +5 / +2 |
| F3 | Hashtag count | > 3 / == 3 | +4 / +2 |
| F4 | Engagement | zero / ≥3 (bonus) | +1 / −1 |
| F5 | Solicitation | follow-farming phrases | +5 |
| F6 | Reply | is_reply | +3 |
| F7 | Language | non-English | +3 |
| F11 | URL-only | < 3 real words | +4 |
| F12 | Mentions | > 3 / > 5 @mentions | +2 / +4 |
| F13 | Repeated handle | 3×+ in batch | +2–3 |

Score buckets (default threshold 5): **clean** 0–2, **dim** 3–4 (42% opacity), **hide** ≥5. Threshold adjustable live via slider in Keyword Sweep panel.

Pending (disabled): F8 follower count, F9 post count, F10 bio — require `getProfile` API calls.

---

## Event Detection Engine

**File:** `event_detector.py` | Pluggable strategy pattern.

**KeywordClusterStrategy** — Groups posts by keywords in a 10-minute window. Source tier weights (Reuters/AP=5 … generic=1). Fires at CLUSTER_THRESHOLD=3 (higher for ambiguous words — see quality gates below). Includes negation check, historical reference filter, entity requirement, coherence check, and semantic dedup.

**VelocitySpikeStrategy** — Detects volume surges. Drives the Spikes bar.

**ZeroShotStrategy** — Cosine similarity against 7 category descriptions. Fires MEDIUM events at confidence ≥0.32 (requires sentence-transformers).

**Lifecycle:** breaking (<30min), developing (30min–4h), stale (>4h, dropped). Max 50 events.

### Event Quality Gates (v2.1)

Four filters applied before a cluster fires as an event, all fail-open (never suppress when evidence is absent):

**#1 — Cluster coherence** (`_check_cluster_coherence`): For clusters of 3+ posts, at least 50% of posts must share at least one named entity with another post in the cluster. Prevents unrelated posts that happen to share a trigger word (e.g. "attack" in a Tehran news story + "attack" in a 2023 street art post) from forming a false event. Wire alerts and geo/entity-keyed clusters are exempt.

**#2 — Entity requirement for ambiguous words** (`ENTITY_REQUIRED_WORDS`): 20 high-ambiguity single-word triggers require at least one named entity in each post before that post is counted toward a cluster. Words include: `attack`, `crisis`, `shooting`, `crash`, `explosion`, `protest`, `arrested`, `invasion`, `sanctions`, `collision`, `outbreak`, `wildfire`, `floods`, `missing`, `election`, `coup`. Falls back to capitalised proper noun regex when spaCy is unavailable.

**#3 — Historical reference filter** (`is_historical_reference` in `nlp_enhancer.py`): Posts referencing a past year (2000–2023) within 80 characters of the trigger keyword, or using retrospective framing phrases ("after the attack", "since the shooting", "anniversary of", "looking back"), are excluded from clusters. Priority accounts and news accounts are exempt.

**#4 — Per-word threshold overrides** (`CLUSTER_THRESHOLD_OVERRIDES`): Ambiguous single-word triggers require higher cluster weight before firing. `attack`, `crisis`, `shooting`, `crash`, `explosion`, `protest`, `arrested`, `missing`, `election` → weight 5 (vs. global default of 3). `sanctions`, `collision`, `outbreak`, `wildfire`, `floods`, `coup` → weight 4.

### Semantic Event Titles

`_generate_title` now calls `_extract_semantic_title` for standard keyword matches, building a descriptive **WHO — WHAT [— WHERE]** title from the highest-weight post's content.

Logic (in priority order):
1. **geo:/person:/ent: prefixed keywords** — structured key used directly
2. **wire_alert** — wire header parsed and truncated at colon/em-dash
3. **All other keywords** — entities extracted, demonyms normalised, action modifiers detected, object prepositional phrases extracted, WHO/WHERE deduplicated by root

**`_NORP_TO_COUNTRY`** (module-level dict, 30 entries) — maps demonym/nationality forms to canonical country names: "Iranian" → "Iran", "Qatari" → "Qatar", "Ukrainian" → "Ukraine", etc.

**`COUNTRY_NAMES`** expanded with 30+ key cities, territories, and regions: Gaza, Kyiv, Tehran, Baghdad, Red Sea, Taiwan Strait, Donbas, Strait of Hormuz, etc.

Example: `"BREAKING An Iranian missile attack has damaged Qatar's main gas facility"` → **"Iran — Missile Attack — Qatar"**

**Impact on Event Matches column:** Richer event titles feed better tokens into the Kalshi semantic match corpus. Markets about "Iran", "missile", "Qatar", and "gas facility" score higher than they would against the bare token "attack". The quality gates also reduce false-positive events, cleaning up the corpus overall.

---

## NLP Enhancement Layer

**File:** `nlp_enhancer.py` | All features degrade gracefully.

- **Phase 1** — NER (spaCy or 5-pass regex) + negation detection
- **Phase 1 (v1.5)** — `is_historical_reference(text, keyword)`: detects past-year proximity and retrospective framing phrases to filter historical references from event clusters
- **Phase 3** — Semantic dedup (cosine ≥0.75, or TF-IDF ≥0.40 fallback)
- **Phase 4** — Zero-shot classification (7 categories)

```bash
pip install spacy && python -m spacy download en_core_web_sm  # Phase 1
pip install sentence-transformers                              # Phase 3+4
```

### `is_historical_reference(text, keyword)`

Two conservative checks (fail-open):

1. **Past year proximity** — text contains a year in 2000–2023 within 80 chars of the keyword. Catches "the October **2023** Hamas **attack** on Israel".
2. **Retrospective framing** — keyword appears inside/after phrases like "after the", "since the", "anniversary of", "looking back", "on this day". Catches "street art painted **after the attack**".

Priority/news account posts are exempt.

---

## Semantic Market Matcher

**File:** `kalshi_feed.py` + `app.py`

Markets pre-indexed as word bigram frozensets (`_tok` key, stripped before cache write). Background thread scores all markets against corpus. Results cached; `get_match_results()` returns instantly.

Sort modes: **Confidence** (score desc), **Prob ↑↓** (yes_ask), **Value** (score × price uncertainty).

Event dedup: same `event_ticker` grouped under one card with expandable siblings.

**Pre-open market filter:** Markets with `open_time` in the future are excluded at ingest time. The number of dropped markets is logged per pull.

**Fetch progress:** `_fetch_pages` and `_fetch_running` updated after each API page via a progress callback. Both exposed on `/api/kalshi/status` and displayed in the loading overlay.

---

## Market Dashboard — All Markets

- Category pills from `CANONICAL_CATS` constant (13 categories, always all shown). Empty = dimmed, `cursor:default`. Click delegates to `mpSelectCat(cat, null)`.
- Series list: `overflow-y: auto` (only scroll element in column 1)
- Market body: `overflow-y: hidden` — paginated via `mpChangePage()`
- `MP_PER_PAGE` starts at 14, calibrated dynamically after first render
- Price/days sliders: dual-handle, fill bar updates live
- Sort: Default / Price ↑ / Price ↓ / Closing ↑ / Closing ↓
- **Market titles are hyperlinks** opening `/market_detail?ticker=TICKER` in a new tab

---

## Market Dashboard — Event Matches

- Confidence default: **≥0.60** (slider value 60, `mpConfThreshold = 0.60`)
- Sort: Conf / Prob ↓ / Prob ↑ / Value
- `mpExcludedCats` preserved across background refreshes — `mpBuildMatchCatRow()` never clears it
- `mpFetchMatch(resetPage=false)` called by interval (preserves page); `resetPage=true` on confidence change or init
- `MP_PER_PAGE` calibrated dynamically
- **Market titles are hyperlinks** opening `/market_detail?ticker=TICKER` in a new tab
- Match quality benefits indirectly from v2.1 event quality improvements: fewer false-positive events and richer semantic titles ("Iran — Missile Attack — Qatar" vs "Attack") produce better token overlap with relevant Kalshi markets

---

## Market Dashboard — Expiry & Price Strategies

Default tab: **Extreme** (HTML `active` class on `strat-tab-extreme`; JS `stratActiveTab = 'extreme'`). All tabs auto-refresh every 60s. `STRAT_DISPLAY_PER` calibrated dynamically (starts 11). Category exclusions preserved across refreshes; only cleared on explicit tab switch.

### Extreme Tab — Full State Defaults

| Variable | Default | Notes |
|----------|---------|-------|
| `stratActiveTab` | `'extreme'` | Opens on Extreme |
| `stratExtremeThresh` | `10` | Edge ≤10¢/≥90¢ |
| `stratExtremeDays` | `1` | Within 1 day |
| `stratExtremeSort` | `'close'` | Closing ↑ (soonest first) |
| `stratHideClosed` | `true` | Hide closed markets |
| `stratExtremeMinVol` | `0` | Any volume |

### Extreme Tab — Controls

Within (1d/**3d**/7d/All) · Edge (≤5¢/≥95¢, **≤10¢/≥90¢**, ≤15¢/≥85¢, ≤20¢/≥80¢) · Sort (Extremity, Spread ↑, **Closing ↑**) · Min Vol (Any/100+/500+) · **Hide closed** (default active)

### Extreme Tab — Card Display

Each card shows (beyond title + YES/NO prices):
- **Time** — `Xh Ym` for sub-day (e.g. `4h 22m`, `45m`); full days for longer. Label: `mins` / `hrs` / `days` / `closed`
- **Urgency bar** — red (<6h/0.25d), amber (<1d), green (>1d); uses price extremity when Within=All
- **FAVOURITE/UNDERDOG badge** with price
- **Spread N¢ (X%)** — spread as % of underdog position cost; green ≤1¢, amber 2–3¢, red 4¢+
- **Fee ~N¢ · net N¢** — Kalshi ~3% fee and net profit per contract
- **↗ kalshi** — deep link
- **Market title** — hyperlink to Market Detail page

### Filter Preservation (both Match and Strat)

`stratBuildCatPills()` rebuilds visuals without touching `stratExcludedCats`. Only `stratSwitchTab()` clears exclusions (intentional). Same pattern for `mpBuildMatchCatRow()` / `mpExcludedCats`.

---

## Market Detail Page

**Route:** `GET /market_detail?ticker=TICKER` | **File:** `market_detail.html`

Opens in a new tab from any market card title in the dashboard. All data is fetched live on open — not from cache — and can be refreshed via the header button.

### Live API Calls (parallel on load)

| Endpoint | Data |
|----------|------|
| `GET /api/kalshi/market/<ticker>` | Market object, orderbook, 30-day candlesticks, sibling markets in same event |
| `GET /api/kalshi/match_detail?ticker=TICKER&threshold=0.05` | Per-source semantic match breakdown |
| `GET /api/polymarket/match?ticker=TICKER` | Top 5 fuzzy-matched Polymarket markets |

### `/api/kalshi/market/<ticker>` — Response Shape

```json
{
  "market":      { ...full Kalshi market object... },
  "orderbook":   { "yes": [[price, qty], ...], "no": [[price, qty], ...] },
  "candlesticks": [ { "end_period_ts": 0, "yes_ask": { "close": "0.60" }, ... } ],
  "siblings":    [ { "ticker", "subtitle", "yes_ask_dollars", "no_ask_dollars", "volume" } ]
}
```

Candlesticks use `period_interval=1440` (1-day candles), last 30 days. Siblings are all markets sharing the same `event_ticker`, sorted by YES ask descending (most likely first), excluding the current market.

### Position Sizer — Fee Formula

Kalshi charges ~3% of profit per contract, minimum $0.01 per contract:

```
feePerContract = max($0.01, (1 - askDollars) * 0.03)
totalFee       = contracts × feePerContract
netProfit      = grossProfit − totalFee
returnPct      = netProfit / totalCost × 100
```

### Polymarket Matching — `/api/polymarket/match`

1. Extracts meaningful keywords from Kalshi market title + subtitle (stops removed)
2. Fires up to 2 searches against `gamma-api.polymarket.com/markets?q=...&active=true`
3. Scores each result with bidirectional token-overlap: `(forward + reverse) / 2`
4. Returns top 5 above 5% similarity threshold with: score, question, YES%, NO%, volume, end date, direct URL

`outcomePrices` from Polymarket is a JSON-encoded string — parsed before scoring. Multi-outcome markets (>2 outcomes) display all outcomes with individual probability bars.

Score color coding: blue ≥35% (strong match), amber 18–34% (plausible), grey <18% (weak).

---

## Market Indices Bar

**File:** `market_indices.py` | Yahoo Finance, 60s poll. Event Dashboard only.

Row 1: S&P 500, NASDAQ, DOW, DAX, FTSE 100, CAC 40
Row 2: VIX, Brent Crude, WTI Crude, Nat Gas, Gasoline (AAA), Bitcoin

---

## Gas Prices (AAA)

**File:** `gas_prices.py` | Scrapes `gasprices.aaa.com`. Refreshes at 00:00, 08:00, 16:00 UTC.

---

## API Reference

```
# Feed & Events
GET  /api/posts              Cached posts (150 max) with noise scores + media fields
GET  /api/events             Detected events
GET  /api/status             System health + module status
GET  /api/feeds              Active feed list
POST /api/feeds/refresh      Force immediate fetch

# Keyword Management
GET  /api/feeds/keywords                All feeds with id, query, enabled, custom flag
POST /api/feeds/keywords                Add keyword {query, limit?} — persisted
DELETE /api/feeds/keywords/<id>         Remove — persisted
POST /api/feeds/keywords/<id>/toggle    Toggle enabled/disabled — persisted

# Account Management
GET  /api/accounts                      Tracked handles (sorted)
POST /api/accounts                      Add {handle} — persisted to accounts.txt
DELETE /api/accounts/<handle>           Remove — persisted
POST /api/accounts/reload               Reload from accounts.txt

# Market Data
GET  /api/markets            Yahoo Finance + AAA indices
GET  /api/gas                AAA gas prices (all grades + direction)
GET  /api/kalshi/status      Cache status, market count, last updated, refresh state,
                               fetch_pages + fetch_running (live during pull)
GET  /api/kalshi/series      Series list (params: category, q)
GET  /api/kalshi/markets     Markets (params: category, series_ticker, q,
                               min/max_price, min/max_days, sort, page, per_page)
GET  /api/kalshi/match       Semantic match results (instant, background-computed)
GET  /api/kalshi/match_detail Per-source breakdown for one market
                               (params: ticker, threshold — default 0.05)
GET  /api/kalshi/market/<ticker>  Live single-market fetch: market object + orderbook
                               + 30-day candlesticks + sibling markets
POST /api/kalshi/refresh     Trigger Kalshi re-fetch (runs in background thread)

# Polymarket
GET  /api/polymarket/match   Fuzzy-match Kalshi ticker against Polymarket Gamma API
                               (param: ticker) — returns top 5 matches with similarity
                               scores, odds, volume, end date

# Pages
GET  /                       Landing page
GET  /dashboard              Event Dashboard
GET  /markets                Market Dashboard
GET  /market_detail          Market Detail (param: ticker)
GET  /match_detail           Legacy semantic match detail (param: ticker)
```

---

## Setup & Running

```bash
# Core
pip install flask flask-cors requests beautifulsoup4 scikit-learn numpy scipy python-dotenv

# NLP Phase 1
pip install spacy && python -m spacy download en_core_web_sm

# NLP Phase 3+4
pip install sentence-transformers

# .env in project root
BSKY_HANDLE=yourhandle.bsky.social
BSKY_PASSWORD=xxxx-xxxx-xxxx-xxxx   # Bluesky App Password

python app.py   # → http://localhost:5001
```

Kalshi data loads in background. Loading overlays on both dashboards show live progress: page count, running market count, elapsed time, and step indicators. Dismisses automatically at ≥1000 markets. Kalshi cache refreshes automatically at the top of each UTC hour.

**File changes require:**
- `.py` files → Flask restart (`Ctrl+C`, `python app.py`)
- `.html` files → browser hard-refresh (`Cmd+Shift+R`) only

---

## Configuration & Tuning

### `bluesky_feed.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `ACCOUNTS_FILE` | `accounts.txt` | Tracked handles |
| `CUSTOM_FEEDS_FILE` | `custom_feeds.json` | Persisted keyword feeds |
| `MAX_CACHED_POSTS` | 150 | Posts in memory |

**Default FEED_CONFIG (8 feeds):**
`breaking`(30) · `just in`(20) · `developing story`(20) · `flash alert`(15) · `explosion attack strike`(20) · `earthquake hurricane tornado wildfire`(20) · `market crash rate hike fed reserve`(20) · `missile launches troops invasion sanctions`(20)

### `event_detector.py`

`CLUSTER_THRESHOLD=3` · `CLUSTER_WINDOW_MINUTES=10` · `MAX_EVENTS=50` · `AGE_BREAKING_MAX=30min` · `AGE_DEVELOPING_MAX=240min`

**Quality gate tuning:**

`ENTITY_REQUIRED_WORDS` — set of single-word triggers requiring a named entity per post. Add words to tighten; remove to loosen.

`CLUSTER_THRESHOLD_OVERRIDES` — dict mapping ambiguous keywords to higher weight thresholds. Defaults: `attack/crisis/shooting/crash/explosion/protest/arrested/missing/election → 5`, `sanctions/collision/outbreak/wildfire/floods/coup → 4`. Tune per-word if legitimate events are suppressed.

`_NORP_TO_COUNTRY` (module-level dict) — demonym → canonical country name map for title generation. Add entries for any demonym not resolving correctly.

`COUNTRY_NAMES` — set of country names, demonyms, cities, territories, and regions for entity extraction fallback. Includes Gaza, Kyiv, Tehran, Red Sea, Taiwan Strait, and others.

### `kalshi_feed.py`

`THRESHOLD_LOW=0.15` · `_BLOCKED_SERIES_PREFIXES=('KXMVE',)` · `PAGE_LIMIT=1000`

**Refresh cadence:** Hourly at the top of each UTC hour (`_hourly_loop`). Cache considered fresh if written within the current UTC hour boundary (`_cache_is_fresh`). Pre-open markets (future `open_time`) filtered at ingest.

**Fetch progress:** `_fetch_pages` and `_fetch_running` updated after each API page via `progress_cb`. Both exposed on `/api/kalshi/status`. Loading overlays poll every 2 seconds to display live pull progress.

### `nlp_enhancer.py`

`DEDUP_THRESHOLD_SEMANTIC=0.75` · `DEDUP_THRESHOLD_TFIDF=0.40` · `DEDUP_WINDOW=50`

`_HISTORICAL_YEAR_RE` — regex matching years 2000–2023. Update the upper bound each year to keep the current year from being treated as historical.

`_HISTORICAL_PHRASES` — frozenset of retrospective framing phrases. Add phrases to catch more historical references; be conservative to avoid false suppressions.

### `gas_prices.py`

`REFRESH_HOURS_UTC={0,8,16}`

---

## Known Limitations / Future Work

- **Kalshi volume data:** `volume` and `volume_fp` fields are often `null` in the API response. The Min Vol filter in the Extreme tab and the Position Sizer depth check have limited effectiveness until this is reliably populated.
- **Bluesky profile filters (F8/F9/F10):** Follower count, post count, and bio filters are implemented but disabled — they require `getProfile` API calls. A profile cache would enable them without rate limit issues.
- **Kalshi semantic matching:** Uses token overlap (unigrams + word bigrams), not embedding-based similarity. Embedding-based matching would improve quality but requires indexing ~35k markets as vectors.
- **Polymarket search:** The Gamma API `?q=` search is keyword-based. Fuzzy matching relies on token overlap against keyword search results. Low-similarity results (<18%) should be treated as coincidental.
- **Event title actor/target ordering:** `_extract_semantic_title` picks the first matching entity as WHO, which can invert actor and target in some posts (e.g. "Russia launches drone attack on Ukrainian..." → WHO=Ukraine because "Ukrainian" appears first in the text). spaCy dependency parsing would fix this correctly.
- **Event quality gate #5 (planned):** Source diversity requirement — a cluster should come from at least 2 distinct source domains. Would go in the cluster acceptance gate alongside #1–#4.
- **`_HISTORICAL_YEAR_RE` upper bound:** Currently matches 2000–2023. Update the upper bound annually.
- **markets.html size:** At ~3200 lines, consider splitting JS into a separate file if it grows further.
- **market_detail.html candlestick granularity:** Uses `period_interval=1440` (daily). Sub-day charts are available via the Kalshi API but not currently exposed.

---

## Version History

| Version | Summary |
|---------|---------|
| v1.0 | Core pipeline: Bluesky feed, event detection, Kalshi browser, market indices bar |
| v1.1 | NLP (Phases 1/3/4), noise scoring (F1–F7), AAA gas prices, velocity spikes, semantic matching, strategy panel |
| v1.2 | Keyword Sweep column, noise scoring expanded (F11–F13), `.cbar` collapsible bars, live account/keyword management, full persistence, media badges, full post text, noise filter slider, UI readability pass, title 16px |
| v1.3 | **Market Dashboard redesign**: 3-column alignment with spacers; `CANONICAL_CATS` (13 always shown, empty dimmed); category filter/page preservation; `overflow-y:hidden` on paginated bodies; `calibratePerPage()`; sub-header removed, Pause/Refresh in title bar. **Extreme tab**: Sort (default Closing ↑), Min Vol, Hide Closed (default ON), ≤5¢/≥95¢ edge, spread% + fee on cards, precise `Xh Ym` time. **Event Matches**: confidence default 0.60, page preserved on refresh. Default tab: Extreme. |
| v1.4 | **Kalshi refresh**: hourly cadence; pre-open market filter; Syncing pill. **Market Detail page**: live pricing, orderbook, 30-day chart, sibling outcomes, YES/NO toggle, position sizer, context callouts, `$0.XX` prices, collapsible glossaries. **Polymarket comparison**: fuzzy matching, top 5 results, arbitrage callout. **Market title hyperlinks** in all columns. Fee formula corrected to per-contract basis. |
| v1.5 | **Event quality gates (v2.1)**: #1 cluster coherence (entity overlap); #2 entity requirement for 20 ambiguous words (`ENTITY_REQUIRED_WORDS`); #3 historical reference filter (`is_historical_reference` in nlp_enhancer — past-year proximity + retrospective framing); #4 per-word threshold overrides (`CLUSTER_THRESHOLD_OVERRIDES`). **Semantic event titles**: `_extract_semantic_title` builds WHO — WHAT — WHERE from post content using NER, `_NORP_TO_COUNTRY` demonym normalisation (30 entries), action modifier detection, and object phrase extraction (e.g. "BREAKING An Iranian missile attack has damaged Qatar's gas facility" → "Iran — Missile Attack — Qatar"). `COUNTRY_NAMES` expanded with 30+ cities/territories/regions. **Loading overlays**: both dashboards show live Kalshi fetch progress (page count, running market count, elapsed time, step indicators) via `fetch_pages`/`fetch_running` on `/api/kalshi/status`, updated per API page via progress callback. |