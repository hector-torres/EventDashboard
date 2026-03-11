# Event Dashboard

**Real-time breaking news monitor with Kalshi prediction market matching.**  
Detects emerging events from Bluesky feeds and surfaces relevant Kalshi markets for short-term event-based trading.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Module Reference](#module-reference)
4. [Event Detection Engine](#event-detection-engine)
5. [Semantic Market Matcher](#semantic-market-matcher)
6. [Kalshi Feed & Browse](#kalshi-feed--browse)
7. [Market Indices Bar](#market-indices-bar)
8. [API Reference](#api-reference)
9. [Running the Server](#running-the-server)
10. [Configuration & Tuning](#configuration--tuning)

---

## Overview

Event Dashboard polls a curated list of Bluesky news accounts every 30 seconds, runs two detection strategies (keyword clustering and velocity spiking) on incoming posts, and continuously matches the resulting event corpus against ~100k open Kalshi markets using a background scoring engine. The result is a live three-panel dashboard:

- **Panel 1 — Bluesky Feed:** Raw posts from tracked accounts plus a search feed
- **Panel 2 — Detected Events:** Ranked event cards (CRITICAL → HIGH → MEDIUM) with expandable post previews
- **Panel 3 — Kalshi Markets:** Browse all open markets by category/series, or view a semantic match tab showing which markets are most relevant to today's news

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
│  │  │ ~100k markets│  _corpus()      │ market-coverage │  │  │
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
2. `event_detector.py` runs detection strategies on the post batch, maintaining a rolling 50-event window
3. `app.py`'s `/api/kalshi/match` route builds a text corpus from current events + recent posts and calls `kalshi_manager.update_match_corpus()`
4. The background scoring thread in `kalshi_feed.py` scores all ~100k markets against the corpus and caches results
5. The dashboard polls `/api/kalshi/match` every 30 seconds and renders results instantly (scoring is never on the request thread)

---

## Module Reference

| File | Role | Key Class |
|------|------|-----------|
| `app.py` | Flask routes, background poll threads | — |
| `bluesky_feed.py` | Bluesky API polling, post caching | `FeedManager` |
| `event_detector.py` | Breaking news detection strategies | `EventDetector` |
| `kalshi_feed.py` | Kalshi API, market cache, semantic scoring | `KalshiManager` |
| `market_indices.py` | Yahoo Finance index/commodity polling | `MarketIndicesManager` |
| `dashboard.html` | Single-page dashboard UI | — |
| `index.html` | Landing page with dashboard links | — |
| `accounts.txt` | Tracked Bluesky account handles (one per line) | — |

---

## Event Detection Engine

**File:** `event_detector.py` | **Class:** `EventDetector`

The detector uses a **pluggable strategy pattern** — new detection strategies can be added via `detector.add_strategy(strategy)` without modifying existing code.

### Detection Strategies

#### 1. KeywordClusterStrategy

Groups posts by shared keywords/topic clusters within a sliding 10-minute window.

**Pipeline:**

1. Each incoming post is matched against `BREAKING_KEYWORDS` — a tiered vocabulary of CRITICAL / HIGH / MEDIUM trigger phrases and words
2. Posts are also checked against **named-entity co-occurrence patterns**: a country name from `COUNTRY_NAMES` co-occurring with a geo-action verb from `GEO_ACTION_VERBS_HIGH/MEDIUM` in the same sentence generates a synthetic cluster keyword like `geo:iran+strikes`
3. **Wire-format detection** (`_detect_wire_caps`) catches all-caps headlines from wire services that don't contain BREAKING_KEYWORDS but look like wire alerts
4. Posts sharing the same keyword within the cluster window are grouped into one event
5. Events require `CLUSTER_THRESHOLD = 3` posts to surface, or just 1 post from a tier-5 source (Reuters, AP, AFP, BBC)
6. Each event gets a **weighted count** — posts from major news organizations count up to 5× a generic account

**Severity tiers:**

| Severity | Example Triggers |
|----------|-----------------|
| CRITICAL | "breaking:", "missile launch", "confirmed dead", "state of emergency", "drone strike" |
| HIGH | "just in", "under attack", "has been arrested", geo country + HIGH action verb |
| MEDIUM | Generic breaking words, geo + MEDIUM verb, wire-format caps detection |

**Source tier weights** (used in `weighted_count`):

| Weight | Sources |
|--------|---------|
| 5 | Reuters, AP, AFP, BBC |
| 4 | NYT, WSJ, FT, Bloomberg, The Economist |
| 3 | Guardian, WaPo, Nikkei, Haaretz, DW |
| 2 | CNBC, CNN, unusual_whales, fintwitter |
| 1 | All other accounts |

#### 2. VelocitySpikeStrategy

Detects sudden volume surges on a topic, independent of keyword matching.

**Pipeline:**

1. All tokens in incoming posts are counted in a sliding window
2. A token is flagged if its count exceeds `SPIKE_THRESHOLD` with a weighted count ≥ 10
3. Tokens in `VELOCITY_NOISE_WORDS` (common English words) are filtered out
4. Surviving spikes not already covered by a cluster event are promoted to MEDIUM events

### Event Lifecycle

Events have three states managed by `_compute_event_status()`:

| State | Age |
|-------|-----|
| `breaking` | < 30 minutes |
| `developing` | 30 min – 4 hours |
| `stale` | > 4 hours (dropped on next pass) |

The detector retains a maximum of `MAX_EVENTS = 50`. New events displace the oldest stale events when the window fills. Events are also **deduplicated** by keyword overlap — a new candidate sharing >50% keywords with an existing event is suppressed.

---

## Semantic Market Matcher

**File:** `kalshi_feed.py` (scoring engine) + `app.py` (corpus builder)

### How It Works

The matcher answers: *"Given what's in the news right now, which open Kalshi markets are most relevant?"*

It runs continuously in a background daemon thread, scoring all ~100k markets against a text corpus derived from current events and recent posts. The Flask request thread only ever reads cached results and never waits for scoring.

### Corpus Construction

On each 30-second poll, `app.py` builds the text corpus passed to the scorer:

```python
texts = []
for event in events:
    # Skip MEDIUM events whose keyword is a generic English word
    if severity not in ('HIGH', 'CRITICAL') and keyword in _CORPUS_STOP:
        continue
    texts += event['sample_posts'][:2]   # full sentences, not just the keyword
    texts += [event['title']]
texts += recent_posts[:60]
```

Using `sample_posts` (actual post text) rather than the event title alone gives the scorer real vocabulary: "Iran has begun laying mines in the Strait of Hormuz" is far more useful than "Volume spike: hormuz".

`_CORPUS_STOP` is an ~80-word blocklist of common English words that appear as velocity-spike keywords but contribute noise rather than signal (e.g., "available", "minister", "whether").

### Token Indexing

Markets are pre-indexed at load time so scoring passes never re-tokenize:

```python
def _expand_tokens(text):
    tokens = _tokenize(text)         # lowercase, strip punctuation, remove stopwords
    word_bigrams = {
        f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1)
    }
    return frozenset(tokens) | word_bigrams
```

**Word bigrams** (e.g., `"iran war"`, `"oil prices"`, `"supreme court"`) are the critical feature. They enable phrase-level matching — a market about "oil prices" won't match a corpus about "court prices". Character-level bigrams were used previously but caused rampant false positives (single-character pairs like `"an"`, `"ar"` match nearly any text).

### Scoring Formula

```python
def score_market_against_corpus(market, corpus_tokens):
    market_tokens = market['_tok']
    return len(market_tokens & corpus_tokens) / len(market_tokens)
```

This is **market-side coverage**: what fraction of the market's content words appear in today's news.

Key properties:
- **Stable** regardless of corpus size — union-Jaccard (intersection/union) collapses to near zero as corpus grows, so a 117-token corpus would make even a perfect Iran/Hormuz market score only 0.03
- **Directional** — measures whether the market is about the current news, not vice versa
- **Fast** — pure frozenset intersection on pre-indexed sets, ~3 seconds for 100k markets

Score interpretation:

| Score | Meaning |
|-------|---------|
| 0.40+ | Market is substantially about the current event |
| 0.20–0.40 | Strong topical overlap (2–4 matching content phrases) |
| 0.10–0.20 | Loose relevance (shares 1–2 topic words) |
| 0.0 | Zero overlap |

### Sort Modes

The Semantic Match panel offers four sort modes:

| Mode | Formula | Use case |
|------|---------|---------|
| **Confidence** | `_score` descending | Default — highest semantic overlap first |
| **Prob ↓ / Prob ↑** | `yes_ask` price | Find over- or under-priced markets |
| **Value** | `_score × uncertainty` where `uncertainty = 1 - abs(yes_ask - 50) / 50` | Event-trading sweet spot — high-confidence matches that haven't fully priced in yet (uncertainty peaks at 50¢, is zero at 0¢ or 100¢) |

### Category Toggles

Each category row in the Semantic Match column has two independent interactions:

- **Click the row** — selects the category and drills into its series/markets (same as browse tab)
- **Click the `●` dot on the right** — hides that category from all results; dot becomes `✕`, label gets strikethrough, row dims. Click again to restore. A "reset" link appears on the "All" row when any category is excluded.

Hiding a category (e.g. Sports, Entertainment) removes it from the series column, market count, and pagination entirely. The excluded set (`kMatchExcludedCats`) is session-only and resets on page reload.

### Background Thread Architecture

```
/api/kalshi/match request
        │
        ├─ build corpus texts from events + posts
        ├─ kalshi_manager.update_match_corpus(texts)  ← returns instantly
        │       │
        │       ├─ hash(texts) == last_hash?  → skip (nothing changed)
        │       ├─ _match_running?  → update pending, return (dedup)
        │       └─ spawn daemon thread → _run_match_loop()
        │               │
        │               └─ _score_markets()
        │                       ├─ build corpus_tokens = union(expand(t) for t in texts)
        │                       ├─ for each market: score_market_against_corpus()
        │                       ├─ filter >= THRESHOLD_LOW (0.15)
        │                       └─ store → _match_cache
        │
        └─ kalshi_manager.get_match_results()  ← returns last cached results instantly
```

### "Why Matched" Detail View

`GET /api/kalshi/match_detail?ticker=TICKER` scores the market against each event and post **individually**, showing exactly which sources drove the match. This is the explainability layer — traders can verify the match is semantically meaningful, not a spurious token collision.

---

## Kalshi Feed & Browse

**File:** `kalshi_feed.py` | **Class:** `KalshiManager`

### Market Cache

Markets are fetched from the Kalshi Trade API using cursor pagination (1000 markets/page, ~100 pages for the filtered corpus). The cache is stored in `data/kalshi_markets.json` and refreshed once daily at midnight UTC.

**Parlay filtering:** The `KXMVE*` series prefix is blocked at fetch time via `_BLOCKED_SERIES_PREFIXES`. This series auto-generates ~546k sports parlay permutation markets that have no semantic signal for news-driven matching and would otherwise represent 84% of the corpus.

### Three-Path Bootstrap

On startup, `KalshiManager.__init__()` picks the fastest available path:

1. **Both caches fresh (written today):** load from disk, stamp categories, ready in <1 second
2. **Markets cached, no series cache:** serve markets immediately, enrich categories in background
3. **No cache / stale:** pull full corpus from Kalshi API in background thread (2–5 minutes)

### Browse UI

The browse tab provides a 3-column drill-down: Category → Series → Markets, with sliders for price range and days-to-close and sort options (default, price ascending/descending, closing soonest/latest).

---

## Market Indices Bar

**File:** `market_indices.py` | **Class:** `MarketIndicesManager`

Polls Yahoo Finance every 60 seconds for two instrument rows:

**Row 1 — Equities:** S&P 500, NASDAQ, DOW, DAX, FTSE 100, CAC 40

**Row 2 — Commodities:** VIX, Brent Crude, WTI Crude, Natural Gas, Gold, Bitcoin

Each tile shows current price, change (Δ and %), OHLC data, and market status (OPEN / FUTURES / CLOSED).

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Landing page |
| `/dashboard` | GET | Main Bluesky dashboard UI |
| `/api/posts` | GET | Recent Bluesky posts |
| `/api/events` | GET | Current detected events |
| `/api/status` | GET | Feed + detector status |
| `/api/kalshi/status` | GET | Market cache status |
| `/api/kalshi/series` | GET | Series list for browse UI |
| `/api/kalshi/markets` | GET | Filtered market list |
| `/api/kalshi/match` | GET | Semantic match results (instant) |
| `/api/kalshi/match_detail` | GET | Per-source score breakdown |
| `/api/kalshi/refresh` | POST | Force immediate market re-fetch |

**`/api/kalshi/match` params:** `threshold` (default 0.15), `page`, `per_page`, `top_n`

**`/api/kalshi/markets` params:** `category`, `series_ticker`, `event_ticker`, `min_price`, `max_price`, `min_days`, `max_days`, `sort`

---

## Running the Server

```bash
pip install flask flask-cors requests
python app.py
# → http://localhost:5001/dashboard
```

On first run with no cache, the Kalshi panel will show "fetching" for 2–5 minutes while the full corpus downloads. Subsequent starts load from disk in under a second.

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

Edit `accounts.txt` — one handle per line. Restart the server or click the feed refresh button.

### Adding a Detection Strategy

```python
from event_detector import DetectionStrategy

class MyStrategy(DetectionStrategy):
    def analyze(self, posts, existing_events):
        return []   # return list of new event dicts

detector.add_strategy(MyStrategy())
```

### Extending the Series Blocklist

```python
_BLOCKED_SERIES_PREFIXES: Tuple[str, ...] = (
    'KXMVE',      # sports parlays (~546k markets)
    'KXOTHER',    # add more noisy series here
)
```