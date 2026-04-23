# Event Trading Terminal — v3.0.2
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
15. [Measles Tracker](#measles-tracker)
16. [Priority Events](#priority-events)
17. [Production Logging](#production-logging)
18. [API Reference](#api-reference)
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

Title bar on all pages: **Event Trading Terminal** brand (16px bold) → nav links → right side. All pages show the KLTT Holdings logo (`/static/kltt-logo.png`, 22px height) in the title bar right side. Market Dashboard right side additionally has Syncing pill, Pause, and Refresh.

### Event Dashboard (`/dashboard`)

| Panel | Contents |
|-------|---------|
| **News Accounts** | Live feed from tracked priority accounts. Collapsible **Tracked Accounts** bar (add/remove, persists to `accounts.txt`). |
| **Detected Events** | Events CRITICAL→HIGH→MEDIUM. Each card shows a two-line header: `[what] <event>` on line 1, `[who]` · `[where]` · `[when]` on line 2 (bracket labels always shown; "unknown" when not extractable). Collapsible **Priority Events** bar (add keywords/phrases to highlight and pin matching events). Latest matching event always pinned as a full card between the bar and the event list. |
| **Keyword Sweep** | Broad keyword search feed. Collapsible **Keywords** bar (add/pause/remove, persists to `keywords.json`). Noise Filter slider. |

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
| `app.py` | Flask routes, poll thread, corpus builder, live market + Polymarket routes; production logging config |
| `bluesky_feed.py` | AT Protocol client, feed management, `keywords.json` persistence |
| `post_scorer.py` | Modular noise scoring (11 active filters, incl. F14 topical relevance) |
| `event_detector.py` | Event detection strategies, quality gates v2.1, semantic title generation, structured event components |
| `nlp_enhancer.py` | NER, negation, semantic dedup, zero-shot, historical reference detection |
| `kalshi_feed.py` | Kalshi API, market cache, semantic scoring, hourly refresh, fetch progress |
| `market_indices.py` | Yahoo Finance + AAA index polling; Measles tile; CME Globex weekend awareness |
| `gas_prices.py` | AAA gas price scraper + rolling 7-day daily history |
| `dashboard.html` | Event Dashboard UI |
| `markets.html` | Market Dashboard UI |
| `market_detail.html` | Live market detail page — two-column layout, three prediction market comparisons, paginated siblings |
| `index.html` | Landing page |
| `measles_tracker.py` | CDC measles YTD case count scraper + rolling 7-week weekly history |
| `static/kltt-logo.png` | Brand logo — served at `/static/kltt-logo.png`; drop file in `static/` subfolder |

**Persistence files:**

| File | Contents |
|------|---------|
| `accounts.txt` | Tracked Bluesky handles (one per line, `#` comments preserved) |
| `keywords.json` | All keyword search feeds (built-in + custom) — authoritative source. FEED_CONFIG in `bluesky_feed.py` is used only to bootstrap this file on first run. |
| `data/priorities.json` | Priority event keywords, saved by Priority Events bar UI |
| `data/gasoline_history.json` | Rolling 7-day daily gas prices (one entry per UTC date) |
| `data/measles_history.json` | Rolling 7-week CDC measles case counts (one entry per ISO week) |

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
| F14 | Topical relevance | zero signal words in post | +3 (hide) |

Score buckets (default threshold **3**): **clean** 0–2, **dim** never reached at threshold 3 (hide fires first), **hide** ≥3. Threshold adjustable live via slider in Keyword Sweep panel (default lowered from 5 → 3 in v1.6).

Pending (disabled): F8 follower count, F9 post count, F10 bio — require `getProfile` API calls.

---

## Event Detection Engine

**File:** `event_detector.py` | Pluggable strategy pattern.

**KeywordClusterStrategy** — Groups posts by keywords in a 10-minute window. Source tier weights (Reuters/AP=5 … `fintwitter`=3 … generic=1; 16 accounts from `accounts.txt` now explicitly weighted). Fires at CLUSTER_THRESHOLD=3 (higher for ambiguous words and format-label phrases — see quality gates below). Includes dim/hide skip, negation check, historical reference filter, entity requirement, coherence check, and semantic dedup. Format-label phrases (`breaking:`, `breaking --`) removed from CRITICAL triggers — were matching any post starting with BREAKING: regardless of content.

**VelocitySpikeStrategy** — Detects volume surges. Drives the Spikes bar. Now applies the same quality gates as `KeywordClusterStrategy`: dim/hide post skip at ingest; historical reference filter per-word at ingest; `_spike_entity_coherent` gate (requires ≥2 posts sharing a named entity near the spiking word, or 1 news account post with a nearby entity); semantic titles via `_generate_spike_title` (reuses `_extract_semantic_title`).

**ZeroShotStrategy** — Cosine similarity against 7 category descriptions. Fires MEDIUM events at confidence ≥0.32 (requires sentence-transformers).

**Lifecycle:** breaking (<30min), developing (30min–4h), stale (>4h, dropped). Max 50 events.

### Event Quality Gates (v2.1)

Four filters applied before a cluster fires as an event, all fail-open (never suppress when evidence is absent):

**#1 — Cluster coherence** (`_check_cluster_coherence`): For clusters of 3+ posts, at least 50% of posts must share at least one named entity with another post in the cluster. Prevents unrelated posts that happen to share a trigger word (e.g. "attack" in a Tehran news story + "attack" in a 2023 street art post) from forming a false event. Wire alerts and geo/entity-keyed clusters are exempt.

**#2 — Entity requirement for ambiguous words and format-label phrases** (`ENTITY_REQUIRED_WORDS` + `ENTITY_REQUIRED_PHRASES`): High-ambiguity single-word triggers (including `breaking`) and format-label phrases (`breaking news`, `just in`, `developing story`, `flash alert`, `developing situation`) require at least one named entity per post. Falls back to capitalised proper noun regex when spaCy is unavailable.

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

**Structured event components:** `_extract_semantic_title` now returns a `{who, what, where}` dict instead of a concatenated string. These components are stored directly on the event object and used by the event card UI to render a two-line header: line 1 is `[what] <event text>` (bracket label + value on same line), line 2 shows `[who]`, `[where]`, and `[when]` with their labels. Missing components display as "unknown" in muted italic.

**Impact on Event Matches column:** Richer event titles feed better tokens into the Kalshi semantic match corpus. The quality gates also reduce false-positive events, cleaning up the corpus overall.

---

## NLP Enhancement Layer

**File:** `nlp_enhancer.py` | All features degrade gracefully.

- **Phase 1** — NER (spaCy or 5-pass regex) + negation detection
- **Phase 1 (v1.5)** — `is_historical_reference(text, keyword)`: detects past-year proximity and retrospective framing phrases to filter historical references from event clusters
- **Phase 1 (v1.6)** — `extract_entities` used by `_spike_entity_coherent` in VelocitySpikeStrategy for entity coherence gating
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
| `GET /api/manifold/match?ticker=TICKER` | Top 5 fuzzy-matched Manifold Markets questions (new in v1.7) |
| `GET /api/metaculus/match?ticker=TICKER` | Top 5 fuzzy-matched Metaculus questions (new in v1.7) |

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

### CME Globex Weekend Closure

`_is_cme_globex_open()` detects when CME Globex is closed (Friday 17:00 → Sunday 18:00 ET). During this window:
- **Equity futures** (ES=F, NQ=F, YM=F): `futures: null` returned — tile shows `CLOSED` instead of stale 0.00% data
- **Always-futures markets** (Brent, WTI, NatGas): `is_open: false`; badge changes to `CLOSED`; `next_open` shows countdown to Sun 18:00 ET
- `_cme_globex_next_open()` computes the time until Globex reopens for the countdown display

`hide_countdown` on always-futures markets is overridden to allow `next_open` to show when closed — prior to v2.0 the countdown was permanently suppressed by this flag.

### Sparkline Fallback

`_fetch_index` now uses a two-pass approach for sparklines:
1. Try `period='1d', interval='5m'` (today's intraday bars)
2. If empty (weekend/holiday), fall back to `period='5d', interval='5m'` and slice out only the most recent trading day's bars

This means closed markets (S&P on Saturday, Brent on Sunday, etc.) still show Friday's full intraday chart rather than a blank area.

### Gasoline Sparkline

`_fetch_aaa` builds a 4-point trend sparkline from AAA data: `[month_ago, week_ago, yesterday, current]`. This gives a meaningful price direction even though AAA only provides daily data points.

---

## Gas Prices (AAA)

**File:** `gas_prices.py` | Scrapes `gasprices.aaa.com`. Refreshes at 00:00, 08:00, 16:00 UTC.

---

## Measles Tracker

**File:** `measles_tracker.py` | Updates weekly — Thursday 14:00 UTC (2h after CDC noon update).

Scrapes the CDC measles data page at `https://www.cdc.gov/measles/data-research/index.html`. The data table cells are JavaScript-rendered and blank in raw HTTP responses; the cumulative case count is instead extracted from prose text using `_CASE_PATTERNS` regex (pattern: "As of [date], X confirmed measles cases were reported in the United States in [year]").

| Field | Description |
|-------|-------------|
| `cases` | Cumulative YTD confirmed cases (int) |
| `change` | Week-over-week new cases |
| `change_pct` | Week-over-week % change |
| `direction` | `up` / `down` / `flat` |
| `as_of` | Date string from CDC prose (e.g. "March 5, 2026") |
| `prior_cases` | Full-year total for prior year (for tile meta row) |
| `history` | Rolling 7-week list `[{week, date, cases}, ...]` (for sparkline) |

The tile in the market bar displays: current YTD count (no `$`), week-over-week change with "cases" suffix, sparkline from weekly history, and a meta row showing "YTD start:" (first history entry) and "2025 total:" (prior year total).

---

## Priority Events

**Persistence:** `data/priorities.json` — flat list of lowercase keyword/phrase strings, written on every add/remove via the UI.

**Matching:** `prioHighlightAll()` runs after every event render and keyword change. For each `.event-item` it builds a haystack from `data-who`, `data-what`, `data-where`, `data-keyword`, and the card's title text, then checks if any priority keyword appears as a substring. Matches receive an amber background tint (`.event-item.prio-match`) and a `★ priority` badge.

**Priority pin (`#prio-pin`):** Sits between the Priority Events cbar and the main `events-body`. Hidden when no priorities are set or no events match. When a match exists, `updatePrioPin()` walks DOM-order events (newest first), preferring visible events but falling back to MEDIUM-hidden ones, and clones the full matching event card — including sample post text, source handles, age, and the `▼ expand` button — into the pin. Updates on every render cycle and on every keyword add/remove.

**API routes:**
```
GET    /api/priorities              List all priority keywords
POST   /api/priorities              Add keyword — body: {keyword: str}
DELETE /api/priorities/<keyword>    Remove keyword
```

---

## Production Logging

**File:** `app.py` | Controlled by `PRODUCTION` environment variable.

Set `PRODUCTION=true` in your `.env` file (project root) to suppress all `INFO`-level log messages. Only `WARNING` and `ERROR` messages will appear. Remove or set `PRODUCTION=false` to restore full logging.

```
# .env
PRODUCTION=true
```

All modules use Python's `logging` module with a shared root logger configured at startup by `app.py`. Third-party loggers (`werkzeug`, `urllib3`, `requests`, `httpx`) are suppressed to `WARNING` regardless of the `PRODUCTION` flag — Flask's per-request logs do not appear in either mode. The two startup banner `print()` calls always appear regardless of log level.

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
GET  /api/measles            CDC measles YTD cases + weekly history

# Priority Events
GET    /api/priorities           List all priority keywords
POST   /api/priorities           Add keyword {keyword: str}
DELETE /api/priorities/<keyword> Remove keyword
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

# Prediction Market Comparisons
GET  /api/polymarket/match   Fuzzy-match against Polymarket Gamma API
                               (param: ticker) — top 5, similarity score, odds, volume, end date
GET  /api/manifold/match     Fuzzy-match against Manifold Markets
                               (param: ticker) — top 5, probability, volume (Mana), close date
GET  /api/metaculus/match    Fuzzy-match against Metaculus
                               (param: ticker) — top 5, community probability, forecaster count

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

Kalshi data loads in background from SQLite (`data/kalshi.db`), created automatically on first run. Loading overlays show live fetch progress. Kalshi cache refreshes automatically at the top of each UTC hour. `sqlite3` is Python stdlib — no extra install needed.

**File changes require:**
- `.py` files → Flask restart (`Ctrl+C`, `python app.py`)
- `.html` files → browser hard-refresh (`Cmd+Shift+R`) only

---

## Configuration & Tuning

### `bluesky_feed.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `ACCOUNTS_FILE` | `accounts.txt` | Tracked handles |
| `KEYWORDS_FILE` | `keywords.json` | All keyword feeds — authoritative source |
| `PRODUCTION` (env) | `false` | Set `true` in `.env` to suppress INFO logs |
| `MAX_CACHED_POSTS` | 150 | Posts in memory |

**Default feeds (11) — bootstrapped into `keywords.json` on first run:**
Format labels (`just in`, `developing story`, `flash alert`) removed — spam-dominated; covered defensively by `ENTITY_REQUIRED_PHRASES`. Multi-word queries split into atomic feeds; ambiguous terms quoted. Once `keywords.json` exists, `FEED_CONFIG` is ignored — edit feeds via the UI or edit `keywords.json` directly.

| ID | Query | Limit |
|----|-------|-------|
| `breaking` | `breaking` | 30 |
| `breaking_news_phrase` | `"breaking news"` (quoted) | 20 |
| `earthquake` | `earthquake magnitude` | 20 |
| `weather_disaster` | `hurricane tornado wildfire` | 20 |
| `disaster_response` | `evacuated "declared emergency"` | 15 |
| `explosion_bombing` | `explosion bombing` | 20 |
| `airstrike_missile` | `airstrike missile rockets` | 20 |
| `military_movement` | `invasion troops offensive` | 15 |
| `diplomacy` | `ceasefire sanctions embargo` | 15 |
| `markets_economy` | `"market crash" recession unemployment` | 20 |
| `fed_rates` | `"rate hike" "fed reserve" "interest rate"` (all quoted) | 20 |

### `event_detector.py`

`CLUSTER_THRESHOLD=3` · `CLUSTER_WINDOW_MINUTES=10` · `MAX_EVENTS=50` · `AGE_BREAKING_MAX=30min` · `AGE_DEVELOPING_MAX=240min`

**Quality gate tuning:**

`ENTITY_REQUIRED_WORDS` — set of single-word triggers requiring a named entity per post. Add words to tighten; remove to loosen.

`CLUSTER_THRESHOLD_OVERRIDES` — dict mapping ambiguous keywords to higher weight thresholds. Defaults: `attack/crisis/shooting/crash/explosion/protest/arrested/missing/election → 5`, `sanctions/collision/outbreak/wildfire/floods/coup → 4`. Tune per-word if legitimate events are suppressed.

`_NORP_TO_COUNTRY` (module-level dict) — demonym → canonical country name map for title generation. Add entries for any demonym not resolving correctly.

`COUNTRY_NAMES` — set of country names, demonyms, cities, territories, and regions for entity extraction fallback. Includes Gaza, Kyiv, Tehran, Red Sea, Taiwan Strait, and others.

### `kalshi_feed.py`

`THRESHOLD_LOW=0.15` · `_BLOCKED_SERIES_PREFIXES=('KXMVE',)` · `PAGE_LIMIT=1000`

**Storage:** Markets and series are persisted to `data/kalshi.db` (SQLite, WAL mode). `filter_markets` queries indexed SQL columns directly — no full RAM scan. `self._markets` is kept in RAM solely for the token scoring engine. On first run, legacy `kalshi_markets.json` / `kalshi_series.json` are automatically migrated to SQLite and can be deleted afterward.

**Refresh cadence:** Hourly at the top of each UTC hour (`_hourly_loop`). Cache freshness checked via `meta.last_updated` row in SQLite (replaces file mtime check). Pre-open markets (future `open_time`) filtered at ingest.

**Fetch progress:** `_fetch_pages` and `_fetch_running` updated after each API page via `progress_cb`. Both exposed on `/api/kalshi/status`. Loading overlays poll every 2 seconds to display live pull progress.

**Dual API domains:** Fetches from both `api.elections.kalshi.com` and `trading-api.kalshi.com`, merges by ticker. Election/political markets live on the elections domain; weather/temperature/sports markets live on the trading domain. Results deduplicated by `ticker` field.

### `post_scorer.py`

`SCORE_HIDE=5` · `SCORE_DIM=3` (thresholds; UI default now 3)

`_SIGNAL_VOCAB` — frozenset of ~120 news/event signal words used by F14. Add words to expand topical coverage (e.g. adding a new topic area to your feeds). A post with zero matching tokens receives +3 (hide). Priority/news account posts are exempt.

### `nlp_enhancer.py`

`DEDUP_THRESHOLD_SEMANTIC=0.75` · `DEDUP_THRESHOLD_TFIDF=0.40` · `DEDUP_WINDOW=50`

`_HISTORICAL_YEAR_RE` — regex matching years 2000–2023. Update the upper bound each year to keep the current year from being treated as historical.

`_HISTORICAL_PHRASES` — frozenset of retrospective framing phrases. Add phrases to catch more historical references; be conservative to avoid false suppressions.

### `app.py`

**`keywords.json` (authoritative feed list):**
All keyword feeds — both built-in and user-added — are stored as a flat JSON array in `keywords.json`. Each entry is a feed dict with `id`, `name`, `type`, `query`, `limit`, `enabled`, and optionally `custom: true`. On first run, this file is bootstrapped from `FEED_CONFIG` in `bluesky_feed.py`. Every add, remove, and toggle via the UI writes the full list immediately. To edit feeds without the UI: stop Flask, edit `keywords.json` directly, restart Flask.

**Migration from `custom_feeds.json`:** On first startup after upgrading, `_load_keywords()` detects the old `{custom, disabled_ids}` format, merges it with `FEED_CONFIG`, and writes `keywords.json` automatically. The old `custom_feeds.json` is left in place (harmless — ignored from then on).

### `app.py`

`_LOCATION_ALIASES` — dict mapping city names to Kalshi location codes (e.g. `'san antonio' → ['satx']`). Extend when new weather/temperature markets for unlisted cities are added. Search automatically expands city name queries to their corresponding codes and vice versa.

### `market_indices.py`

`_is_cme_globex_open()` — checks ET weekday/time against CME Globex schedule. Update `CLOSE_T` (Fri 17:00) and `REOPEN_T` (Sun 18:00) if CME ever changes its maintenance window.

`_cme_globex_next_open()` — walks forward to next Sunday 18:00 ET. The ±0 case (it's already Sunday before 18:00) is handled separately.

**`always_futures` tiles:** These markets (`brent`, `wti`, `natgas`) have `hide_countdown=True` and `always_futures=True` in `INDICES_CONFIG`. The `next_open` field is passed through despite `hide_countdown` when `always_futures` is set — don't remove that exception or the countdown disappears again.

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
- **Manifold/Metaculus matching:** Same token-overlap approach as Polymarket — semantic similarity not used. Manifold volume is in Mana (M), not dollars. Metaculus shows forecaster count rather than volume.
- **Kalshi dual-domain coverage:** Markets are fetched from both `api.elections.kalshi.com` and `trading-api.kalshi.com` and merged by ticker. If Kalshi adds a third domain or restructures their APIs, `KALSHI_TRADING_BASE` in `kalshi_feed.py` would need updating.
- **SQLite token columns:** The `_tok` frozenset is not stored in SQLite (not serialisable). It is rebuilt in RAM at load time via `_index_market_tokens`. If the scoring formula changes, the RAM index rebuilds automatically on next startup.
- **Event card [who]/[where] accuracy:** `_extract_semantic_title` uses word-list entity extraction when spaCy is unavailable. Actor/target order can be inverted in posts where a demonym appears before the country name. spaCy dependency parsing would improve this.
- **F14 `_SIGNAL_VOCAB` coverage:** The vocabulary is manually curated. New feed topics outside the current set (entertainment, sports, etc.) would need vocab additions to avoid false hides.
- **Sparkline 5d fallback — daily maintenance break:** CME Globex has a ~1h maintenance break every day around 17:00–18:00 ET. During this window, both the 1d and 5d fetches may return empty/partial data. The sparkline will be blank for that hour on weekdays.
- **Gasoline sparkline resolution:** The sparkline uses daily closing prices (up to 7 days). AAA updates 3×/day so each day's entry is the last value fetched that day. No intraday resolution.

---

## Version History

| Version | Summary |
|---------|---------|
| v1.0 | Core pipeline: Bluesky feed, event detection, Kalshi browser, market indices bar |
| v1.1 | NLP (Phases 1/3/4), noise scoring (F1–F7), AAA gas prices, velocity spikes, semantic matching, strategy panel |
| v1.2 | Keyword Sweep column, noise scoring expanded (F11–F13), `.cbar` collapsible bars, live account/keyword management, full persistence, media badges, full post text, noise filter slider, UI readability pass, title 16px |
| v1.3 | **Market Dashboard redesign**: 3-column alignment with spacers; `CANONICAL_CATS` (13 always shown, empty dimmed); category filter/page preservation; `overflow-y:hidden` on paginated bodies; `calibratePerPage()`; sub-header removed, Pause/Refresh in title bar. **Extreme tab**: Sort (default Closing ↑), Min Vol, Hide Closed (default ON), ≤5¢/≥95¢ edge, spread% + fee on cards, precise `Xh Ym` time. **Event Matches**: confidence default 0.60, page preserved on refresh. Default tab: Extreme. |
| v1.4 | **Kalshi refresh**: hourly cadence; pre-open market filter; Syncing pill. **Market Detail page**: live pricing, orderbook, 30-day chart, sibling outcomes, YES/NO toggle, position sizer, context callouts, `$0.XX` prices, collapsible glossaries. **Polymarket comparison**: fuzzy matching, top 5 results, arbitrage callout. **Market title hyperlinks** in all columns. Fee formula corrected to per-contract basis. |
| v1.5 | **Event quality gates (v2.1)**: #1 cluster coherence (entity overlap); #2 entity requirement for 20 ambiguous words (`ENTITY_REQUIRED_WORDS`); #3 historical reference filter (`is_historical_reference` in nlp_enhancer — past-year proximity + retrospective framing); #4 per-word threshold overrides (`CLUSTER_THRESHOLD_OVERRIDES`). **Semantic event titles**: `_extract_semantic_title` builds WHO — WHAT — WHERE from post content using NER, `_NORP_TO_COUNTRY` demonym normalisation (30 entries), action modifier detection, and object phrase extraction. `COUNTRY_NAMES` expanded with 30+ cities/territories/regions. **Loading overlays**: both dashboards show live Kalshi fetch progress (page count, running market count, elapsed time, step indicators). |
| v1.8 | **SQLite migration**: `kalshi_feed.py` persistence layer replaced — markets and series stored in `data/kalshi.db` (SQLite, WAL mode) instead of `kalshi_markets.json`. `filter_markets` now runs indexed SQL queries (`category`, `series_ticker`, `yes_price_cents`, `close_ts` all indexed) instead of full RAM scans. `self._markets` retained in RAM for token scoring only. One-time automatic migration from legacy JSON on first run. `_db_connect`, `_db_is_fresh`, `_db_load_markets/series`, `_db_save_markets/series`, `_db_filter_markets` added; `_cache_is_fresh`/`_load_cache`/`_save_cache` retained as legacy-only helpers. |
| v1.7 | **Market Detail page redesign**: two-column layout (`ctx-sizer-cols` for context+sizer side-by-side, `inner-cols` for chart+orderbook side-by-side); three prediction market comparison panels side-by-side (`comparisons-cols` — Polymarket, Manifold, Metaculus); Matched Events + Posts in `matches-cols` full-width grid; siblings table paginated (10 per page, Prev/Next nav); price chart fixed (`close_dollars` field, was silently returning null); Order Book header moved inside bordered box. **Manifold + Metaculus**: new `/api/manifold/match` and `/api/metaculus/match` routes; `buildManifold` + `buildMetaculus` JS functions; all three comparisons fetched in parallel on page load. **Polymarket**: closed markets filtered from comparison results. **Dual Kalshi API**: `kalshi_feed.py` now fetches from both `api.elections.kalshi.com` and `trading-api.kalshi.com` and merges by ticker — covers weather/temperature and other non-election markets previously missing. `/api/kalshi/market/<ticker>` also tries both domains. **Category name fix**: `_infer_category` corrected `'Climate'` → `'Climate and Weather'` and `'Tech & Science'` → `'Science and Technology'` to match `CANONICAL_CATS`. **All Markets search redesign**: search bar moved to top of column (aligns with Confidence slider in col 2); filter priority enforced — category primary, series secondary, search tertiary; `mpBuildSearchSeriesList` fetches all matching series in one background call (stable across pagination); `mpSelectCat` preserves search query and re-scopes it; `mpSelectSeries` clears search on drill-down. **Search expanded**: matches `series_ticker` + `event_ticker` in addition to title/subtitle/ticker. **`_LOCATION_ALIASES`**: 40+ city-name → Kalshi location-code mappings so searching "san antonio" finds `KXHIGHTSATX` markets. |
| v1.6 | **Feed enabled fix**: `bluesky_feed.py` fetch loop now respects the `enabled` flag — disabled feeds are skipped at fetch time (was only persisted, not enforced). **FEED_CONFIG restructured**: 8 → 11 atomic feeds; format-label feeds removed (`just in`, `developing story`, `flash alert`); ambiguous queries split and quoted (`"rate hike"`, `"market crash"`, `"breaking news"`, `"declared emergency"`, `"interest rate"`, `"fed reserve"`). **Source weights**: 16 accounts from `accounts.txt` now explicitly weighted in `SOURCE_TIER_WEIGHTS`; `fintwitter` promoted to 3; `ms.now` (MSNBC) added at 2. **Breaking: phrases removed**: `breaking:`, `breaking --`, `breaking —` removed from CRITICAL triggers — were matching any post opening with "BREAKING:" regardless of content. `breaking` bare word added to `ENTITY_REQUIRED_WORDS` + threshold 5. `ENTITY_REQUIRED_PHRASES` added for format-label phrases. **VelocitySpikeStrategy improvements**: dim/hide skip at ingest; per-word historical reference filter at ingest; `_spike_entity_coherent` gate (entity coherence across posts); semantic titles via `_generate_spike_title` reusing `_extract_semantic_title`. **F14 TopicalRelevanceFilter**: new `post_scorer.py` filter; posts with zero words from `_SIGNAL_VOCAB` (~120 news/event terms) receive +3 (hide). **Structured event components**: `_extract_semantic_title` returns `{who, what, where}` dict; all three strategies store components on event objects. **Event card redesign**: two-line header — `[what]` on line 1; `[who]` · `[where]` · `[when]` on line 2 with bracket labels; "unknown" shown in muted italic when unavailable. **Noise filter default**: keyword sweep hide threshold lowered 5 → 3. **Logo**: KLTT Holdings text replaced with `kltt-logo.png` on all three pages (drop in `EventDashboard/static/`). **Updated panel**: market bar "Updated" text now vertical, inward-facing, single line showing "Updated · Xm ago". **Collapsible bars**: Tracked Accounts, Spikes, Keywords bars now default to collapsed on startup. **Dashboard overlay**: Event Dashboard loading overlay replaced with full step-indicator + page-counter version matching Market Dashboard. **Bug fixes**: overlay `pollTimer` race fixed on both dashboards (both `resolvedImmediately` + `shown` guards); `_generate_spike_title` dict handling; `ZeroShotStrategy` stale variable references (`pseudo_cluster`, `sorted_c`); `self._extract_semantic_title` called on wrong class in spike/zero-shot strategies. |
| v2.0 | **`[what]` label on event cards**: event card line 1 now shows `[what]` bracket label inline with the event text, consistent with `[who]`/`[where]`/`[when]` on line 2. **`keywords.json` authoritative feed source**: `bluesky_feed.py` now uses `keywords.json` (flat list of all feeds) instead of the old `custom_feeds.json` (which only stored deltas). `FEED_CONFIG` is used only to bootstrap the file on first run; all subsequent reads and writes go through `keywords.json`. Automatic migration from `custom_feeds.json` on first startup. **Market indices overhaul** (`market_indices.py`): CME Globex weekend closure detection (`_is_cme_globex_open`, `_cme_globex_next_open`) — equity futures return `null` during Fri 17:00→Sun 18:00 ET window instead of stale 0.00% data; always-futures tiles (Brent/WTI/NatGas) show `CLOSED` badge + countdown during the same window; `hide_countdown` overridden for always-futures when closed. **Sparkline fallback**: 5-day fallback fetch when 1d returns empty (weekend/holiday) — shows previous trading day's full chart. **Gasoline sparkline**: 4-point trend built from AAA data (month_ago → week_ago → yesterday → current). **Dashboard hardening**: logo `<img>` restored on all three pages after regression; noise filter default confirmed at 3; `spikeCollapsed`/`acctCollapsed`/`sf3KwCollapsed` confirmed defaulting to `true` with `collapsed` class baked into HTML. **Event card who/what/where subheaders**: `renderEvent` in `dashboard.html` now renders a two-line header using the structured `who`/`what`/`where` fields from the event object — `[what]` on line 1, `[who]`·`[where]`·`[when]` on line 2 with bracket labels and muted "unknown" fallback. **Regression fixes**: overlay `pollTimer` and `shown` guards re-verified; `market-updated-combined` vertical panel size corrected to match sidebar label weight; `always_futures` `next_open` unblocked from `hide_countdown` suppression. |
| v3.0 | **Priority Events bar**: replaces Spikes bar in Detected Events panel. Add keywords/phrases to highlight matching events (amber tint + `★ priority` badge). Keywords persist to `data/priorities.json` via `/api/priorities`. **Priority pin**: full latest-match event card (with sample posts, meta row, expand button) pinned between the Priority Events bar and the event list — always shows most recent priority hit even when MEDIUM events are hidden. **Measles tracker** (`measles_tracker.py`, new): replaces BTC-USD tile; scrapes CDC measles page weekly; rolling 7-week history in `data/measles_history.json`; tile shows YTD case count + wow change + weekly sparkline. **Gasoline 7-day sparkline**: `gas_prices.py` now records one daily snapshot to `data/gasoline_history.json`; `market_indices._fetch_aaa()` uses 7-day history for sparkline when ≥2 points available. **Production logging**: `PRODUCTION=true` in `.env` sets root logger to WARNING-only; all `print()` calls converted to `logger.info/warning` across `app.py`, `bluesky_feed.py`, `market_indices.py`. **Badge sizing**: all event card badges locked to `height: 15px; display: inline-flex`; emojis removed from status badges. **Panel header alignment**: `.panel-header` fixed at `height: 45px` so all three panels align perfectly. |
| v3.0.1 | **Pushover push notifications**: CRITICAL/HIGH priority events trigger real-time push via Pushover API. Credentials in `.env` (`PUSHOVER_USER_KEY`, `PUSHOVER_API_TOKEN`). Emergency priority (ack required) for CRITICAL; high-priority for HIGH. Server-side dedup via `_notified_ids` set with rollback on send failure. `/api/notify` POST route + `/api/notify/status` GET route in `app.py`. Client-side dedup via `_lastNotifiedId` in `dashboard.html`. Notification body includes structured `[WHAT]`/`[WHO]`/`[WHERE]`/`[WHEN]` labels (uppercase, values as-is) + up to 2 sample post excerpts. **`keywords.json` startup fix** (`bluesky_feed.py`): `__init__` no longer seeds from hardcoded `FEED_CONFIG` — `active_feeds` starts empty and is populated entirely by `_load_keywords()`. Fixes bug where all 8 built-in feeds (including `breaking`, `just_in`, etc.) were always active on startup regardless of `keywords.json` contents. **urllib3 connection pool warning fixed**: `requests.Session` now mounts an `HTTPAdapter(pool_maxsize=16)` to match `ThreadPoolExecutor(max_workers=12)` — eliminates "Connection pool is full, discarding connection" warnings. **Timestamp display**: `relTime()` now shows local timestamp + timezone offset (e.g. `Apr 18, 02:47 PM -0500`) for events older than 1 hour; `tzOffset()` helper shared by both `relTime` and notification builder. `[WHEN]` in event cards always shows local timestamp (not relative time). **Measles tile**: badge changed from `24/7` → `CDC`; meta row changed from "YTD start / 2025" → `YTD / Wk / Mo` matching gasoline layout; sparkline sliced to 5 most recent weekly points. **spaCy auto-download** (`nlp_enhancer.py`): on startup, if spaCy is installed but no model is found, `en_core_web_sm` is downloaded automatically via `subprocess` and retried — eliminates manual `python -m spacy download` step for new installs. |
| v3.0.2 | **`event_detector.py` logging fix**: all `print()` calls converted to `logger.info/warning` via a new `import logging` + `logger = logging.getLogger(__name__)`. Fixes `[EventDetector]` lines appearing in the console even when `PRODUCTION=true`. |
