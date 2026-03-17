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
6. [NLP Enhancement Layer](#nlp-enhancement-layer)
7. [Semantic Market Matcher](#semantic-market-matcher)
8. [Market Dashboard Page](#market-dashboard-page)
9. [Trading Strategy Panel](#trading-strategy-panel)
10. [Market Indices Bar](#market-indices-bar)
11. [Gas Prices (AAA)](#gas-prices-aaa)
12. [API Reference](#api-reference)
13. [Running the Server](#running-the-server)
14. [Configuration & Tuning](#configuration--tuning)

---

## Overview

Event Trading Terminal polls a curated list of Bluesky news accounts every 30 seconds, runs multiple detection strategies on incoming posts, and continuously matches the resulting event corpus against ~30,000 open Kalshi prediction markets using a background scoring engine.

**Three pages:**
- **Home** (`/`) — landing page with links to both views
- **Event Dashboard** (`/dashboard`) — live Bluesky feed + event detection + Kalshi panel
- **Market Dashboard** (`/markets`) — dedicated market browser with browse, event matches, and trading strategy columns

---

## Pages & Navigation

All pages share a consistent title bar: **Event Trading Terminal** brand → nav links (Home · Event Dashboard · Market Dashboard) → KLTT Holdings. A ⏸ Pause button stops all auto-refresh intervals for 5 minutes with a countdown timer; clicking it during the countdown resets to +5 minutes.

### Home (`/`)
Landing page. Two cards linking to Event Dashboard and Market Dashboard.

### Event Dashboard (`/dashboard`)
Three-panel layout:

| Panel | Contents |
|-------|---------|
| **Feed** | Split: News Accounts feed (top) + Search feed (bottom). Live Bluesky posts, color-coded by breaking status. |
| **Events** | Detected events ranked CRITICAL → HIGH → MEDIUM. Expandable cards with sample posts. Keyword spike chips for velocity events. |
| **Event Matches** | Browse (category → series → markets) and Semantic Match tabs. |

### Market Dashboard (`/markets`)
Three-column layout:

| Column | Contents |
|--------|---------|
| **All Markets** | Category pill filters → series list → market cards. Price/days sliders, sort strip, search bar. |
| **Event Matches** | Live matched markets from the event corpus. Confidence slider, sort modes, category filter pills, event deduplication with expandable siblings. |
| **Expiry & Price Strategies** | Four strategy tabs with live signals, full pagination, category filters, and urgency indicators. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   Flask Server (port 5001)                       │
│                                                                   │
│  ┌────────────────┐   poll every 30s   ┌───────────────────────┐ │
│  │  bluesky_feed  │──────────────────▶ │    event_detector     │ │
│  │  (FeedManager) │                    │    (EventDetector)    │ │
│  └────────────────┘                    └──────────┬────────────┘ │
│                                                   │               │
│                             ┌─────────────────────▼───────────┐  │
│                             │         nlp_enhancer             │  │
│                             │  Phase 1: NER + negation         │  │
│                             │  Phase 3: semantic dedup         │  │
│                             │  Phase 4: zero-shot classify     │  │
│                             └─────────────────────────────────┘  │
│                                                   │ events        │
│  ┌────────────────────────────────────────────────▼───────────┐  │
│  │                       kalshi_feed                           │  │
│  │  (KalshiManager)                                            │  │
│  │  ┌──────────────┐  update_match   ┌──────────────────────┐ │  │
│  │  │ market cache │───────────────▶ │   scoring thread     │ │  │
│  │  │ ~30k markets │   _corpus()     │   market-coverage    │ │  │
│  │  │ pre-indexed  │                 │   scoring            │ │  │
│  │  └──────────────┘                 └──────────────────────┘ │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌────────────────┐  poll 60s   ┌─────────────────────────────┐   │
│  │ market_indices │  (Yahoo)    │        gas_prices           │   │
│  └────────────────┘             │  (AAA scrape, 3x/day)       │   │
│                                 └─────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

**Data flow:**
1. `bluesky_feed.py` fetches posts from tracked accounts via the Bluesky AT Protocol API
2. `event_detector.py` runs three detection strategies with NLP enhancement
3. `nlp_enhancer.py` provides NER, negation detection, semantic dedup, and zero-shot classification
4. `app.py`'s `/api/kalshi/match` builds a text corpus from current events + recent posts
5. The background scoring thread scores all markets against the corpus and caches results
6. Pages poll the APIs every 30–60 seconds; all heavy computation is off the request thread

---

## Module Reference

| File | Role |
|------|------|
| `app.py` | Flask routes, background poll threads, corpus builder |
| `bluesky_feed.py` | Bluesky API polling, post caching (`FeedManager`) |
| `event_detector.py` | Breaking news detection strategies (`EventDetector`) |
| `nlp_enhancer.py` | NLP layer: NER, negation, semantic dedup, zero-shot (`NLPEnhancer`) |
| `kalshi_feed.py` | Kalshi API, market cache, semantic scoring (`KalshiManager`) |
| `market_indices.py` | Yahoo Finance index/commodity polling (`MarketIndicesManager`) |
| `gas_prices.py` | AAA national average gas price scraper (`GasPricesManager`) |
| `dashboard.html` | Event Dashboard page |
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
2. NLP entity check: any named entity + geo-action verb generates a synthetic keyword (e.g., `ent:Kim Jong Un+launches`)
3. Wire-format detection (`_detect_wire_caps`) catches all-caps wire service headlines
4. Posts sharing a keyword within the window are grouped into one event
5. Threshold: `CLUSTER_THRESHOLD = 3` weighted posts, or 1 post from a tier-5 source
6. Events get a `weighted_count` — major news orgs count up to 5× a generic account
7. **Negation check (v1.1):** posts matching a keyword are dropped if that keyword is negated in context ("No missile launch detected" does not fire)
8. **Semantic dedup (v1.1):** new events are compared against the rolling event buffer — paraphrases of existing events are suppressed

**Source tier weights:**

| Weight | Sources |
|--------|---------|
| 5 | Reuters, AP, AFP, BBC |
| 4 | NYT, WSJ, FT, Bloomberg, The Economist |
| 3 | Guardian, WaPo, Nikkei, Haaretz, DW |
| 2 | CNBC, CNN, unusual_whales, fintwitter |
| 1 | All others |

#### VelocitySpikeStrategy
Detects sudden volume surges independent of keyword matching. Tokens exceeding `SPIKE_THRESHOLD` (weight ≥ 10) in a sliding window are promoted to MEDIUM events, after filtering `VELOCITY_NOISE_WORDS`. Requires a proper noun within 10 tokens of the spiking word in ≥2 posts.

#### ZeroShotStrategy *(v1.1, requires sentence-transformers)*
For each post with a named entity but no keyword match, classifies text against seven category descriptions using cosine similarity of sentence embeddings. Fires a MEDIUM event if confidence ≥ 0.32 and the cluster has sufficient weight. The `zero_shot_conf` field is stored on the event dict.

**Categories:** Military Action, Natural Disaster, Economic/Financial, Political/Government, Crime/Security, Health/Medical, Technology/Cyber.

### Event Lifecycle

| State | Age |
|-------|-----|
| `breaking` | < 30 minutes |
| `developing` | 30 min – 4 hours |
| `stale` | > 4 hours (dropped on next pass) |

Maximum 50 events in window. New events displace oldest stale events.

---

## NLP Enhancement Layer

**File:** `nlp_enhancer.py` | **Class:** `NLPEnhancer` *(new in v1.1)*

All features degrade gracefully when optional dependencies are missing. The module prints its active mode on startup: `NLPEnhancer(p1=regex, p3/4=EmbeddingEngine(tfidf))`.

### Phase 1 — Named Entity Recognition + Negation Detection

**NER** — Extracts named entities via spaCy (if installed) or a five-pass regex cascade:
1. Multi-word consecutive caps (`Kim Jong Un`, `White House`)
2. Geographic connectors (`Sea of Japan`, `Gulf of Mexico`)
3. `the X` patterns (`the Fed`, `the Kremlin`, `the Pentagon`)
4. Sentence-initial names (`Zelenskyy announces…`)
5. ALL-CAPS acronyms (`NATO`, `FBI`, `IMF`)

**Entity severity upgrade** — Named entity + HIGH verb (`launches`, `strikes`, `kills`) → upgrade to HIGH. Named entity + MEDIUM verb (`announces`, `declares`, `raises`) → upgrade to MEDIUM. Benign verbs (`meets`, `visits`, `said`) are excluded.

**Negation detection** — 60-character window before/after each matched keyword checks for `no `, `not `, `denied`, `ruled out`, `false reports`, `not confirmed`, etc. With spaCy: upgrades to dependency-parse negation arcs.

**Install:**
```bash
pip install spacy
python -m spacy download en_core_web_sm
```

### Phase 3 — Semantic Event Deduplication

Rolling deque of the last 50 event embeddings. Before accepting a new event, cosine similarity is checked against all stored events. If ≥ 0.75 — suppressed as duplicate. Catches paraphrases like "Iran launches missiles" ≈ "Iranian missile strike" that stem-overlap misses.

Fallback without `sentence-transformers`: hybrid TF-IDF (word bigrams + character trigrams), threshold 0.40.

### Phase 4 — Zero-shot Category Classification

Seven category descriptions are pre-embedded at startup. For each post, cosine similarity is measured against each category. Score ≥ 0.32 + named entity present → eligible for `ZeroShotStrategy`. Only active with `sentence-transformers` installed.

**Install (Phase 3 + 4):**
```bash
pip install sentence-transformers
# all-MiniLM-L6-v2 (~90MB) downloads automatically on first use
```

### Tunable thresholds

| Constant | Default | Effect |
|----------|---------|--------|
| `DEDUP_THRESHOLD_SEMANTIC` | 0.75 | Cosine similarity cutoff for semantic dedup |
| `DEDUP_THRESHOLD_TFIDF` | 0.40 | TF-IDF fallback dedup cutoff |
| `ZERO_SHOT_MIN_CONFIDENCE` | 0.32 | Min score to fire a zero-shot event |
| `ZERO_SHOT_MIN_SCORE` | 0.30 | Min entity score to enable zero-shot |

---

## Semantic Market Matcher

**File:** `kalshi_feed.py` + `app.py`

### Corpus Construction
On each 30-second poll, `app.py` builds a text corpus:
- CRITICAL/HIGH events: use `sample_posts` sentences
- MEDIUM events: skipped if keyword is in `_CORPUS_STOP`
- Appends up to 60 recent Bluesky posts

### Token Indexing
Each market is pre-indexed at load time using word bigrams:
```
_expand_tokens(text) → frozenset(unigrams | word_bigrams)
```
The `_tok` key is stored in-memory only and stripped before writing the disk cache (prevents JSON serialization errors on cache save).

### Scoring Formula
**Market-side coverage:** `intersection / len(market_tokens)`

| Score | Meaning |
|-------|---------|
| ≥ 0.40 | Market is substantially about the current event |
| 0.20–0.40 | Strong topical overlap |
| 0.10–0.20 | Loose relevance |
| 0.0 | No overlap |

### Sort Modes

| Mode | Formula | Use case |
|------|---------|---------|
| **Confidence** | `_score` desc | Default |
| **Prob ↓ / ↑** | `yes_ask` price | Find over/under-priced markets |
| **Value** | `_score × (1 − \|yes_ask−50\| / 50)` | High confidence + price uncertainty |

### Event Deduplication
Markets in the same `event_ticker` are grouped under one card. Collapsed expander shows "+ N more outcomes ↓".

### Background Thread Architecture
```
/api/kalshi/match request
    ├─ build corpus from events + posts
    ├─ update_match_corpus(texts) → returns instantly
    │     ├─ hash unchanged? → skip
    │     └─ spawn daemon → score all markets → cache results
    └─ get_match_results() → returns last cached results instantly
```

---

## Market Dashboard Page

### All Markets Column
- **Category pills** — click to filter, "All" shows every series alphabetically
- **Series pane** — scrollable list of series in selected category
- **Market cards** — paginated, price/days sliders, sort strip, search bar
- **Days filter** — 0–1500 day range; params omitted at defaults (avoids filtering long-dated markets)

### Event Matches Column
- Confidence slider (default ≥0.15), sort modes, category filter pills
- Event deduplication with expandable siblings inline

### Kalshi API Resilience
- Pagination retries up to 3× with 60-second timeouts and exponential backoff
- Safety guard: new pull must return ≥ `max(1000, existing_count/2)` markets before overwriting cache
- **Parlay filter:** `KXMVE*` series excluded (~546k sports parlay markets)

### Loading Overlay
Polls `/api/kalshi/status` every 3 seconds. Shows overlay with live count while `count < 1000`. Dismisses and initializes UI directly — no page reload.

---

## Trading Strategy Panel

All four tabs auto-refresh every 60 seconds. Results are **fully paginated** — all matching markets fetched across API pages (500 per request), displayed 50 per display page with ← Prev / Next → controls. Category filter pills on each tab.

### Near Expiry
Markets closing within N days (3d/7d/14d), price 25–75¢. Sorted by days ascending.

### Extreme
Markets closing within N days (1d/3d/7d/**All**), priced ≤10¢ or ≥90¢. "All" option removes the days cap. When "All" is selected, urgency bar reflects price extremity rather than time pressure.

### Signal — Semantic Match + Undecided Price
Filters `mpMatchAll` for score ≥ threshold (default 0.30) AND price 20–80¢. Badge: `"0.67 match · 42¢ Leans NO"`. Auto-updates on Event Matches refresh.

### Tension — Semantic Match + Near-Expiry
Filters `mpMatchAll` for score ≥ threshold, price 30–70¢, days ≤ N (default 7). Most urgent combination.

### Card Display
- **Near Expiry / Extreme:** large days-remaining number with urgency colour
- **Signal / Tension:** large match % with days tag and score bar
- All tabs: spread warning if `yes_ask + no_ask > 100¢`

---

## Market Indices Bar

**File:** `market_indices.py` | **Class:** `MarketIndicesManager`

Polls Yahoo Finance every 60 seconds. Present on Event Dashboard only.

**Row 1 — Equities:** S&P 500, NASDAQ, DOW, DAX, FTSE 100, CAC 40  
**Row 2 — Commodities:** VIX, Brent Crude, WTI Crude, Natural Gas, Gasoline (AAA), Bitcoin

---

## Gas Prices (AAA)

**File:** `gas_prices.py` | **Class:** `GasPricesManager` *(new in v1.1)*

Scrapes `gasprices.aaa.com` for US national average retail gas prices. Updates at **00:00, 08:00, and 16:00 UTC**.

**Tile display:**
- Headline price: current Regular avg
- Meta row: Yest / Wk (week ago) / Mo (month ago)
- Badge: "AAA" with as-of date in tooltip

**`/api/gas`** returns the full data object including all grades (Regular, Mid-Grade, Premium, Diesel) and direction.

---

## API Reference

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Landing page |
| `/dashboard` | GET | Event Dashboard |
| `/markets` | GET | Market Dashboard page |
| `/api/posts` | GET | Recent Bluesky posts |
| `/api/events` | GET | Current detected events |
| `/api/status` | GET | Feed + detector status |
| `/api/markets` | GET | Market indices data (Yahoo Finance + AAA) |
| `/api/gas` | GET | AAA national average gas prices |
| `/api/kalshi/status` | GET | Market cache status |
| `/api/kalshi/series` | GET | Series list for browse UI |
| `/api/kalshi/markets` | GET | Filtered/paginated markets |
| `/api/kalshi/match` | GET | Semantic match results (instant) |
| `/api/kalshi/match_detail` | GET | Per-source score breakdown for one market |
| `/api/kalshi/refresh` | POST | Force immediate market re-fetch |

**`/api/kalshi/markets` key params:** `category`, `series_ticker`, `event_ticker`, `q`, `min_price`, `max_price`, `min_days`, `max_days`, `sort`, `page`, `per_page`

---

## Running the Server

```bash
pip install flask flask-cors requests beautifulsoup4 scikit-learn numpy scipy
python app.py
# → http://localhost:5001
```

**Optional NLP upgrades:**
```bash
# Phase 1 — real NER + dependency-parse negation:
pip install spacy
python -m spacy download en_core_web_sm

# Phase 3 + 4 — semantic dedup + zero-shot classification:
pip install sentence-transformers
```

The embedding model (`all-MiniLM-L6-v2`, ~90MB) downloads automatically on first use.

---

## Configuration & Tuning

**`event_detector.py`**

| Constant | Default | Effect |
|----------|---------|--------|
| `CLUSTER_THRESHOLD` | 3 | Min weighted posts to form a cluster event |
| `CLUSTER_WINDOW_MINUTES` | 10 | Sliding window for clustering |
| `MAX_EVENTS` | 50 | Max events in rolling window |
| `AGE_BREAKING_MAX` | 30 min | "breaking" status duration |
| `AGE_DEVELOPING_MAX` | 240 min | "developing" status duration |

**`nlp_enhancer.py`**

| Constant | Default | Effect |
|----------|---------|--------|
| `DEDUP_THRESHOLD_SEMANTIC` | 0.75 | Cosine similarity cutoff for semantic dedup |
| `DEDUP_THRESHOLD_TFIDF` | 0.40 | TF-IDF fallback dedup cutoff |
| `ZERO_SHOT_MIN_CONFIDENCE` | 0.32 | Min score to fire a zero-shot event |
| `ZERO_SHOT_MIN_SCORE` | 0.30 | Min entity score to enable zero-shot |

**`kalshi_feed.py`**

| Constant | Default | Effect |
|----------|---------|--------|
| `THRESHOLD_LOW` | 0.15 | Pre-filter for background scorer |
| `_BLOCKED_SERIES_PREFIXES` | `('KXMVE',)` | Series excluded from corpus |
| `PAGE_LIMIT` | 1000 | Markets per Kalshi API page |

**`gas_prices.py`**

| Constant | Default | Effect |
|----------|---------|--------|
| `REFRESH_HOURS_UTC` | `{0, 8, 16}` | UTC hours to re-scrape AAA |

### Adding Tracked Accounts
Edit `accounts.txt` — one handle per line. Restart the server.

### Adding a Detection Strategy
```python
from event_detector import DetectionStrategy

class MyStrategy(DetectionStrategy):
    def analyze(self, posts, existing_events):
        return []  # return list of new event dicts

detector.add_strategy(MyStrategy())
```

### Adding a Zero-shot Category
Edit `ZERO_SHOT_CATEGORIES` in `nlp_enhancer.py`:
```python
ZERO_SHOT_CATEGORIES['energy_commodity'] = (
    'oil price crude futures energy commodity OPEC supply demand '
    'gasoline natural gas refinery pipeline'
)
```