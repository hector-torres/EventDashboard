# Event Trading Terminal

**Real-time breaking news monitor with Kalshi prediction market matching and trading strategy signals.**  
Detects emerging events from Bluesky feeds, surfaces relevant prediction markets, and flags trading opportunities.

---

## Table of Contents

1. [Overview](#overview)
2. [Pages & Navigation](#pages--navigation)
3. [Architecture](#architecture)
4. [Module Reference](#module-reference)
5. [Event Detection Engine](#event-detection-engine)
6. [Semantic Market Matcher](#semantic-market-matcher)
7. [Market Dashboard Page](#market-dashboard-paget-page)
8. [Trading Strategy Panel](#trading-strategy-panel)
9. [Market Indices Bar](#market-indices-bar)
10. [API Reference](#api-reference)
11. [Running the Server](#running-the-server)
12. [Configuration & Tuning](#configuration--tuning)

---

## Overview

Event Trading Terminal polls a curated list of Bluesky news accounts every 30 seconds, runs two detection strategies on incoming posts, and continuously matches the resulting event corpus against ~30,000 open Kalshi prediction markets using a background scoring engine.

**Three pages:**
- **Home** (`/`) — landing page with links to both views
- **Event Trading Terminal** (`/dashboard`) — live Bluesky feed + event detection + Kalshi panel
- **Market Dashboard** (`/markets`) — dedicated market browser with browse, semantic match, and trading strategy columns

---

## Pages & Navigation

All pages share a consistent title bar: brand name → nav links (Home · Event Dashboard · Market Dashboard) → KLTT Holdings. A ⏸ Pause button stops all auto-refresh intervals for 5 minutes with a countdown timer; clicking again during the countdown resets to +5 minutes.

### Home (`/`)
Landing page. Two cards linking to Event Trading Terminal and Market Dashboard.

### Event Trading Terminal (`/dashboard`)
Three-panel layout:

| Panel | Contents |
|-------|---------|
| **Feed** | Split: News Accounts feed (top) + Search feed (bottom). Live Bluesky posts from tracked accounts, color-coded by breaking status. |
| **Events** | Detected events ranked CRITICAL → HIGH → MEDIUM. Expandable cards with sample posts. Keyword spike chips for velocity events. |
| **Market Dashboard** | Browse (category → series → markets) and Semantic Match tabs. |

### Market Dashboard (`/markets`)
Three-column layout:

| Column | Contents |
|--------|---------|
| **Browse** | Category pill filters → series list → market cards. Price/days sliders, sort strip, search bar. |
| **Semantic Match** | Live matched markets from the event corpus. Confidence slider, sort modes (Confidence / Prob / Value), category filter pills, event deduplication with expandable siblings. |
| **Trading Strategy** | Four strategy tabs (Near Expiry / Extreme / Signal / Tension) with live signals, category filters, and urgency indicators. |

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                     Flask Server (port 5001)                  │
│                                                               │
│  ┌────────────────┐   poll every 30s   ┌───────────────────┐ │
│  │  bluesky_feed  │──────────────────▶ │  event_detector   │ │
│  │  (FeedManager) │                    │  (EventDetector)  │ │
│  └────────────────┘                    └─────────┬─────────┘ │
│                                                  │ events     │
│  ┌──────────────────────────────────────────────▼─────────┐  │
│  │                      kalshi_feed                        │  │
│  │  (KalshiManager)                                        │  │
│  │                                                         │  │
│  │  ┌──────────────┐  update_match   ┌─────────────────┐  │  │
│  │  │ market cache │──────────────▶  │ scoring thread  │  │  │
│  │  │ ~30k markets │  _corpus()      │ market-coverage │  │  │
│  │  │ pre-indexed  │                 │ scoring         │  │  │
│  │  └──────────────┘                 └─────────────────┘  │  │
│  └─────────────────────────────────────────────────────────┘  │
│                                                               │
│  ┌────────────────┐   poll every 60s                          │
│  │ market_indices │   (Yahoo Finance)                         │
│  └────────────────┘                                           │
└──────────────────────────────────────────────────────────────┘
```

**Data flow:**
1. `bluesky_feed.py` fetches posts from tracked accounts via the Bluesky AT Protocol API
2. `event_detector.py` runs detection strategies, maintaining a rolling 50-event window
3. `app.py`'s `/api/kalshi/match` builds a text corpus from current events + recent posts
4. The background scoring thread scores all markets against the corpus and caches results
5. Pages poll the APIs every 30–60 seconds; all heavy computation is off the request thread

---

## Module Reference

| File | Role |
|------|------|
| `app.py` | Flask routes, background poll threads, corpus builder |
| `bluesky_feed.py` | Bluesky API polling, post caching (`FeedManager`) |
| `event_detector.py` | Breaking news detection strategies (`EventDetector`) |
| `kalshi_feed.py` | Kalshi API, market cache, semantic scoring (`KalshiManager`) |
| `market_indices.py` | Yahoo Finance index/commodity polling (`MarketIndicesManager`) |
| `dashboard.html` | Event Trading Terminal page |
| `markets.html` | Market Dashboard page |
| `index.html` | Landing page |
| `accounts.txt` | Tracked Bluesky handles (one per line) |

---

## Event Detection Engine

**File:** `event_detector.py` | **Class:** `EventDetector`

Uses a pluggable strategy pattern — add new strategies via `detector.add_strategy(strategy)`.

### Detection Strategies

#### KeywordClusterStrategy
Groups posts by shared keywords within a 10-minute sliding window.

1. Each post is matched against `BREAKING_KEYWORDS` — tiered vocabulary (CRITICAL / HIGH / MEDIUM)
2. Named-entity co-occurrence: a **country name** + **geo-action verb** in the same sentence generates a synthetic keyword (e.g., `geo:iran+strikes`)
3. Wire-format detection (`_detect_wire_caps`) catches all-caps wire service headlines
4. Posts sharing a keyword within the window are grouped into one event
5. Threshold: `CLUSTER_THRESHOLD = 3` posts, or 1 post from a tier-5 source (Reuters, AP, etc.)
6. Events get a `weighted_count` — major news orgs count up to 5× a generic account

**Source tier weights:**

| Weight | Sources |
|--------|---------|
| 5 | Reuters, AP, AFP, BBC |
| 4 | NYT, WSJ, FT, Bloomberg, The Economist |
| 3 | Guardian, WaPo, Nikkei, Haaretz, DW |
| 2 | CNBC, CNN, unusual_whales, fintwitter |
| 1 | All others |

#### VelocitySpikeStrategy
Detects sudden volume surges independent of keyword matching. Tokens exceeding `SPIKE_THRESHOLD` (weight ≥ 10) in a sliding window are promoted to MEDIUM events, after filtering `VELOCITY_NOISE_WORDS`.

### Event Lifecycle

| State | Age |
|-------|-----|
| `breaking` | < 30 minutes |
| `developing` | 30 min – 4 hours |
| `stale` | > 4 hours (dropped on next pass) |

Maximum 50 events in window. New events displace oldest stale events. Deduplicated by keyword overlap (>50% shared keywords = suppressed).

---

## Semantic Market Matcher

**File:** `kalshi_feed.py` + `app.py`

### Corpus Construction
On each 30-second poll, `app.py` builds a text corpus:
- CRITICAL/HIGH events: use `sample_posts` sentences (richer than titles alone)
- MEDIUM events: skipped if keyword is in `_CORPUS_STOP` (generic English words)
- Appends up to 60 recent Bluesky posts

### Token Indexing
Each market is pre-indexed at load time using word bigrams:
```
_expand_tokens(text) → frozenset(unigrams | word_bigrams)
```
Word bigrams (e.g., `"iran war"`, `"oil prices"`) provide phrase-level matching without false positives from character-level bigrams.

### Scoring Formula
**Market-side coverage:** `intersection / len(market_tokens)`

What fraction of the market's content words appear in today's news. Stable regardless of corpus size (unlike union-Jaccard, which collapses as corpus grows).

| Score | Meaning |
|-------|---------|
| ≥ 0.40 | Market is substantially about the current event |
| 0.20–0.40 | Strong topical overlap |
| 0.10–0.20 | Loose relevance |
| 0.0 | No overlap |

### Sort Modes

| Mode | Formula | Use case |
|------|---------|---------|
| **Confidence** | `_score` desc | Default — highest semantic overlap first |
| **Prob ↓ / ↑** | `yes_ask` price | Find over/under-priced markets |
| **Value** | `_score × (1 − \|yes_ask−50\| / 50)` | High confidence + price uncertainty |

### Event Deduplication
Markets in the same `event_ticker` are grouped under one card (highest-scoring market shown). A collapsed expander shows "+ N more outcomes in this event ↓" — click to expand siblings inline.

### Background Thread Architecture
```
/api/kalshi/match request
    ├─ build corpus from events + posts
    ├─ update_match_corpus(texts) → returns instantly
    │     ├─ hash unchanged? → skip
    │     └─ spawn daemon → score all markets → cache results
    └─ get_match_results() → returns last cached results instantly
```

### "Why Matched" Detail View
`GET /api/kalshi/match_detail?ticker=TICKER` scores the market against each event and post **individually**, showing exactly which sources drove the match. Uses `_expand_tokens` and market-side coverage (same formula as main scorer).

---

## Market Dashboard Page

### Browse Column
- **Category pills** at top — click to filter series to a category, "All" shows every series
- **Series pane** — scrollable list of series in the selected category
- **Market cards** — paginated, with price/days sliders and sort strip
- **Search bar** — title/subtitle/ticker text search, debounced 300ms
- **Days filter** — 0–1500 day range; params only sent when not at defaults (avoids filtering long-dated markets)

### Kalshi API Resilience
- Pagination retries up to 3× with 60-second timeouts and exponential backoff
- Safety guard: new pull must return ≥ `max(1000, existing_count/2)` markets before overwriting cache
- **Parlay filter:** `KXMVE*` series excluded (~546k sports parlay markets)
- **Series fallback:** if series metadata fetch fails, synthesizes series from market `series_ticker` fields so browse still works

### Loading Overlay
On page load, polls `/api/kalshi/status` every 3 seconds. If `count < 1000`, shows a full-screen overlay with a live count ("12,450 markets loaded…"). When count ≥ 1000, dismisses overlay and calls `mpLoadSeries()` / `mpFetchMatch()` directly — no page reload, no race condition.

---

## Trading Strategy Panel

All four strategy tabs auto-refresh every 60 seconds. Category filter pills appear below each tab's controls, derived from the current result set.

### Near Expiry
Markets closing within N days (3d/7d/14d) with price in undecided range (default 25–75¢). Sorted by days ascending. Urgency bar fills red/amber/green as expiry approaches.

### Extreme
Markets closing within N days (1d/3d/7d) priced near 0¢ or 100¢ (default ≤10¢ / ≥90¢). Sorted by distance from 50¢ (most extreme first). "Underdog" badge (blue) for low-priced YES; "Favourite" badge (green) for high-priced YES.

### Signal (Strategy 5 — Semantic Match + Undecided Price)
Filters `mpMatchAll` (live semantic match data, already in memory) for:
- Match score ≥ threshold (default 0.30)
- Yes price 20–80¢

Sorted by score desc, tie-broken by price uncertainty. Badge shows `"0.67 match · 42¢ Leans NO"`. Auto-updates whenever the Semantic Match column refreshes.

### Tension (Strategy 6 — Semantic Match + Near-Expiry Tension)
Highest urgency combination: filters `mpMatchAll` for:
- Match score ≥ threshold (default 0.30)
- Price 30–70¢
- Days left ≤ N days (default 7)

Sorted by days ascending (most urgent first), score as tiebreaker. Days tag highlights amber when ≤7 days. The event is happening now, the market expires soon, the price hasn't moved.

### Card Display
- **Near Expiry / Extreme:** large days-remaining number (red/amber/green urgency)
- **Signal / Tension:** large match percentage (e.g., "75%") instead of days; includes days-remaining tag and match bar in metadata
- All tabs: spread warning if `yes_ask + no_ask > 100¢` (wide spread = lower liquidity)

---

## Market Indices Bar

**File:** `market_indices.py` | **Class:** `MarketIndicesManager`

Polls Yahoo Finance every 60 seconds. Present on Event Trading Terminal only (removed from Market Dashboard and Home pages).

**Row 1 — Equities:** S&P 500, NASDAQ, DOW, DAX, FTSE 100, CAC 40  
**Row 2 — Commodities:** VIX, Brent Crude, WTI Crude, Natural Gas, Gold, Bitcoin

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Landing page |
| `/dashboard` | GET | Event Trading Terminal |
| `/markets` | GET | Market Dashboard page |
| `/api/posts` | GET | Recent Bluesky posts |
| `/api/events` | GET | Current detected events |
| `/api/status` | GET | Feed + detector status |
| `/api/markets` | GET | Market indices data (Yahoo Finance) |
| `/api/kalshi/status` | GET | Market cache status |
| `/api/kalshi/series` | GET | Series list for browse UI |
| `/api/kalshi/markets` | GET | Filtered/paginated markets |
| `/api/kalshi/match` | GET | Semantic match results (instant) |
| `/api/kalshi/match_detail` | GET | Per-source score breakdown for one market |
| `/api/kalshi/refresh` | POST | Force immediate market re-fetch |

**`/api/kalshi/markets` key params:** `category`, `series_ticker`, `event_ticker`, `q` (text search), `min_price`, `max_price`, `min_days`, `max_days`, `sort`, `page`, `per_page`

**`/api/kalshi/match` key params:** `threshold`, `page`, `per_page`, `top_n`

---

## Running the Server

```bash
pip install flask flask-cors requests
python app.py
# → http://localhost:5001
```

On first run, the Market Dashboard page shows a loading overlay while ~30,000 markets download (2–5 minutes). Subsequent starts load from the disk cache in under a second.

---

## Configuration & Tuning

**`event_detector.py`**

| Constant | Default | Effect |
|----------|---------|--------|
| `CLUSTER_THRESHOLD` | 3 | Min posts to form a cluster event |
| `CLUSTER_WINDOW_MINUTES` | 10 | Sliding window for clustering |
| `MAX_EVENTS` | 50 | Max events in rolling window |
| `AGE_BREAKING_MAX` | 30 min | "breaking" status duration |
| `AGE_DEVELOPING_MAX` | 240 min | "developing" status duration |

**`kalshi_feed.py`**

| Constant | Default | Effect |
|----------|---------|--------|
| `THRESHOLD_LOW` | 0.15 | Pre-filter for background scorer |
| `_BLOCKED_SERIES_PREFIXES` | `('KXMVE',)` | Series excluded from corpus |
| `PAGE_LIMIT` | 1000 | Markets per Kalshi API page |

### Adding Tracked Accounts
Edit `accounts.txt` — one handle per line. Restart the server or use the feed refresh button.

### Adding a Detection Strategy
```python
from event_detector import DetectionStrategy

class MyStrategy(DetectionStrategy):
    def analyze(self, posts, existing_events):
        return []  # return list of new event dicts

detector.add_strategy(MyStrategy())
```