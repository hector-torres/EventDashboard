# Event Trading Terminal — v1.4
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
18. [Version History](#version-history)

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
| **Detected Events** | Events CRITICAL→HIGH→MEDIUM with lifecycle badges. Collapsible **Spikes** bar. |
| **Keyword Sweep** | Broad keyword search feed. Collapsible **Keywords** bar (add/pause/remove, persists to `custom_feeds.json`). Noise Filter slider. |

All collapsible bars use the shared `.cbar` CSS system: `.cbar` → `.cbar-header` → `.cbar-arrow` + `.cbar-label` + `.cbar-count`, and `.cbar-body`. Collapse rotates arrow; count always visible.

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
| `event_detector.py` | Event detection strategies |
| `nlp_enhancer.py` | NER, negation, semantic dedup, zero-shot |
| `kalshi_feed.py` | Kalshi API, market cache, semantic scoring, hourly refresh |
| `market_indices.py` | Yahoo Finance + AAA index polling |
| `gas_prices.py` | AAA gas price scraper |
| `dashboard.html` | Event Dashboard UI |
| `markets.html` | Market Dashboard UI |
| `market_detail.html` | Live market detail page (new in v1.4) |
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

**KeywordClusterStrategy** — Groups posts by keywords in a 10-minute window. Source tier weights (Reuters/AP=5 … generic=1). Fires at CLUSTER_THRESHOLD=3. Includes negation check and semantic dedup.

**VelocitySpikeStrategy** — Detects volume surges. Drives the Spikes bar.

**ZeroShotStrategy** — Cosine similarity against 7 category descriptions. Fires MEDIUM events at confidence ≥0.32 (requires sentence-transformers).

**Lifecycle:** breaking (<30min), developing (30min–4h), stale (>4h, dropped). Max 50 events.

---

## NLP Enhancement Layer

**File:** `nlp_enhancer.py` | All features degrade gracefully.

- **Phase 1** — NER (spaCy or 5-pass regex) + negation detection
- **Phase 3** — Semantic dedup (cosine ≥0.75, or TF-IDF ≥0.40 fallback)
- **Phase 4** — Zero-shot classification (7 categories)

```bash
pip install spacy && python -m spacy download en_core_web_sm  # Phase 1
pip install sentence-transformers                              # Phase 3+4
```

---

## Semantic Market Matcher

**File:** `kalshi_feed.py` + `app.py`

Markets pre-indexed as word bigram frozensets (`_tok` key, stripped before cache write). Background thread scores all markets against corpus. Results cached; `get_match_results()` returns instantly.

Sort modes: **Confidence** (score desc), **Prob ↑↓** (yes_ask), **Value** (score × price uncertainty).

Event dedup: same `event_ticker` grouped under one card with expandable siblings.

**Pre-open market filter:** Markets with `open_time` in the future are excluded at ingest time — these are markets Kalshi creates in advance that show "Begins in N days" and cannot yet be traded. The number of dropped markets is logged per pull.

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
3. Scores each result with bidirectional token-overlap: `(forward + reverse) / 2` where forward = Kalshi tokens in PM question, reverse = PM tokens in Kalshi title
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
GET  /api/kalshi/status      Cache status, market count, last updated, refresh state
GET  /api/kalshi/series      Series list (params: category, q)
GET  /api/kalshi/markets     Markets (params: category, series_ticker, q,
                               min/max_price, min/max_days, sort, page, per_page)
GET  /api/kalshi/match       Semantic match results (instant, background-computed)
GET  /api/kalshi/match_detail Per-source breakdown for one market
                               (params: ticker, threshold — default 0.05)
GET  /api/kalshi/market/<ticker>  Live single-market fetch: market object + orderbook
                               + 30-day candlesticks + sibling markets (new in v1.4)
POST /api/kalshi/refresh     Trigger Kalshi re-fetch (runs in background thread)

# Polymarket
GET  /api/polymarket/match   Fuzzy-match Kalshi ticker against Polymarket Gamma API
                               (param: ticker) — returns top 5 matches with similarity
                               scores, odds, volume, end date (new in v1.4)

# Pages
GET  /                       Landing page
GET  /dashboard              Event Dashboard
GET  /markets                Market Dashboard
GET  /market_detail          Market Detail (param: ticker) — new in v1.4
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

Kalshi data loads in background (~30–60s). Loading overlay dismisses automatically at ≥1000 markets. Kalshi cache refreshes automatically at the top of each UTC hour.

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

### `kalshi_feed.py`

`THRESHOLD_LOW=0.15` · `_BLOCKED_SERIES_PREFIXES=('KXMVE',)` · `PAGE_LIMIT=1000`

**Refresh cadence:** Hourly at the top of each UTC hour (`_hourly_loop`). Cache considered fresh if written within the current UTC hour boundary (`_cache_is_fresh`). Pre-open markets (future `open_time`) filtered at ingest — never enter cache or scoring.

### `gas_prices.py`

`REFRESH_HOURS_UTC={0,8,16}`

---

## Known Limitations / Future Work

- **Kalshi volume data:** `volume` and `volume_fp` fields are often `null` in the API response. The Min Vol filter in the Extreme tab and the Position Sizer depth check have limited effectiveness until this is reliably populated.
- **Bluesky profile filters (F8/F9/F10):** Follower count, post count, and bio filters are implemented but disabled — they require `getProfile` API calls. A profile cache would enable them without rate limit issues.
- **Kalshi semantic matching:** Uses token overlap (unigrams + word bigrams), not embedding-based similarity. Embedding-based matching would improve quality but requires indexing ~35k markets as vectors.
- **Polymarket search:** The Gamma API `?q=` search is keyword-based. There is no semantic/embedding search available, so fuzzy matching relies on token overlap against whatever the keyword search returns. Low-similarity results (<18%) should be treated as coincidental.
- **markets.html size:** At ~3100 lines, consider splitting JS into a separate file if it grows further.
- **market_detail.html candlestick endpoint:** Uses `period_interval=1440` (daily). Sub-day charts are available via the Kalshi API but not currently exposed.

---

## Version History

| Version | Summary |
|---------|---------|
| v1.0 | Core pipeline: Bluesky feed, event detection, Kalshi browser, market indices bar |
| v1.1 | NLP (Phases 1/3/4), noise scoring (F1–F7), AAA gas prices, velocity spikes, semantic matching, strategy panel |
| v1.2 | Keyword Sweep column, noise scoring expanded (F11–F13), `.cbar` collapsible bars, live account/keyword management, full persistence, media badges, full post text, noise filter slider, UI readability pass, title 16px |
| v1.3 | **Market Dashboard redesign**: 3-column alignment with spacers; `CANONICAL_CATS` (13 always shown, empty dimmed); category filter/page preservation across background refreshes; `overflow-y:hidden` on paginated bodies; `calibratePerPage()` dynamic items-per-page; sub-header removed, Pause/Refresh in title bar. **Extreme tab**: Sort (default Closing ↑), Min Vol, Hide Closed (default ON), ≤5¢/≥95¢ edge, spread% + fee estimate on cards, precise `Xh Ym` time display. **Event Matches**: confidence default 0.60, page preserved on background refresh. Default tab: Extreme. |
| v1.4 | **Kalshi refresh**: hourly cadence (was daily); pre-open market filter (drops markets with `open_time` in future); Syncing pill in title bar during refresh. **Market Detail page** (`market_detail.html` + `/api/kalshi/market/<ticker>`): live pricing, orderbook, 30-day price chart, sibling outcomes table for multi-outcome markets, YES/NO position toggle with per-side economics, position sizer ($1–$100k log-scale, contracts/fees/net/return/fill check), context callouts (break-even probability, price drift, time value), all prices in `$0.XX` format, collapsible glossaries on all sections. **Polymarket comparison** (`/api/polymarket/match`): fuzzy token-overlap matching against Gamma API, top 5 results with similarity score, odds, volume, end date, arbitrage callout. **Market title hyperlinks** in all three dashboard columns. Fee formula corrected to per-contract basis. |