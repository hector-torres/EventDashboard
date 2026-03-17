# Event Trading Terminal — v1.2
**KLTT Holdings** | Internal research tool

Real-time event detection and market intelligence terminal. Monitors Bluesky for breaking news, detects developing events through NLP and velocity analysis, and surfaces relevant Kalshi prediction markets. Built for rapid situational awareness during fast-moving market events.

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
9. [Market Dashboard Page](#market-dashboard-page)
10. [Trading Strategy Panel](#trading-strategy-panel)
11. [Market Indices Bar](#market-indices-bar)
12. [Gas Prices (AAA)](#gas-prices-aaa)
13. [API Reference](#api-reference)
14. [Setup & Running](#setup--running)
15. [Configuration & Tuning](#configuration--tuning)

---

## Overview

Event Trading Terminal polls a curated list of Bluesky news accounts every 30 seconds, runs multiple detection strategies on incoming posts, and continuously matches the resulting event corpus against ~35,000 open Kalshi prediction markets using a background scoring engine. A broad keyword sweep feed surfaces posts from the wider Bluesky public with configurable noise filtering.

**Three pages:**
- **Home** (`/`) — landing page with links to both dashboards
- **Event Dashboard** (`/dashboard`) — news feed, event detection, keyword sweep
- **Market Dashboard** (`/markets`) — Kalshi market browser, event matching, trading strategies

---

## Pages & Navigation

All pages share a consistent title bar: **Event Trading Terminal** brand → nav links (Home · Event Dashboard · Market Dashboard) → KLTT Holdings. Links are 12px white, unbolded. A ⏸ Pause button on the Event Dashboard stops all auto-refresh intervals for 5 minutes with a countdown timer.

### Home (`/`)
Landing page with links to both dashboards.

### Event Dashboard (`/dashboard`)
Three-panel layout:

| Panel | Contents |
|-------|---------|
| **News Accounts** | Live feed from tracked priority accounts. Collapsible **Tracked Accounts** bar with add/remove UI. |
| **Detected Events** | Events ranked CRITICAL → HIGH → MEDIUM, with lifecycle status badges. Collapsible **Spikes** bar shows velocity-triggered keyword chips. |
| **Keyword Sweep** | Broad keyword search feed with collapsible **Keywords** bar (add/pause/remove) and **Noise Filter** slider. |

### Market Dashboard (`/markets`)
Three-column layout:

| Column | Contents |
|--------|---------|
| **All Markets** | Category → Series → Markets drill-down. Price/days dual sliders, sort strip, search bar, pagination. |
| **Event Matches** | Semantic match against current events. Confidence slider, sort modes, category filters. |
| **Expiry & Price Strategies** | Four strategy tabs: Near Expiry, Extreme, Signal, Tension. Fully paginated, category filters, urgency indicators. |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                   Flask Server (port 5001)                       │
│                                                                   │
│  ┌────────────────┐   poll every 30s   ┌───────────────────────┐ │
│  │  bluesky_feed  │──────────────────▶ │    event_detector     │ │
│  │  (FeedManager) │                    │    (EventDetector)    │ │
│  └───────┬────────┘                    └──────────┬────────────┘ │
│          │                                        │               │
│  ┌───────▼────────┐                  ┌────────────▼────────────┐ │
│  │  post_scorer   │                  │      nlp_enhancer       │ │
│  │  10 filters    │                  │  NER · dedup · classify │ │
│  └────────────────┘                  └─────────────────────────┘ │
│                                                   │ events        │
│  ┌────────────────────────────────────────────────▼───────────┐  │
│  │                       kalshi_feed                           │  │
│  │  ~35k markets, pre-indexed · background scoring thread      │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                                                    │
│  ┌────────────────┐  poll 60s   ┌─────────────────────────────┐   │
│  │ market_indices │  (Yahoo)    │        gas_prices           │   │
│  └────────────────┘             │  (AAA scrape, 3×/day)       │   │
│                                 └─────────────────────────────┘   │
└────────────────────────────────────────────────────────────────────┘
```

**Data flow:**
1. `bluesky_feed.py` fetches posts from tracked accounts + 8 keyword search feeds concurrently
2. `post_scorer.py` scores every non-priority post for noise (10 active filters)
3. `event_detector.py` runs three detection strategies with NLP enhancement on each batch
4. `kalshi_feed.py` background-scores all markets against the event corpus
5. `market_indices.py` polls Yahoo Finance + AAA every 60 seconds
6. All pages poll the JSON APIs every 30–60 seconds; heavy computation is off the request thread

---

## Module Reference

| File | Role | Lines |
|------|------|-------|
| `app.py` | Flask routes, poll thread, corpus builder | 547 |
| `bluesky_feed.py` | AT Protocol client, feed management, noise wiring | 512 |
| `post_scorer.py` | Modular noise scoring system | 494 |
| `event_detector.py` | Event detection strategies | 697 |
| `nlp_enhancer.py` | NER, negation, semantic dedup, zero-shot | 492 |
| `kalshi_feed.py` | Kalshi API, market cache, semantic scoring | 811 |
| `market_indices.py` | Yahoo Finance + AAA index polling | 483 |
| `gas_prices.py` | AAA gas price scraper | 206 |
| `dashboard.html` | Event Dashboard UI | 3410 |
| `markets.html` | Market Dashboard UI | 2811 |
| `index.html` | Landing page | 236 |

**Persistence files:**

| File | Contents |
|------|---------|
| `accounts.txt` | Tracked Bluesky handles (one per line, `#` comments preserved) |
| `custom_feeds.json` | User-added keyword feeds + disabled states for built-in feeds |

---

## Noise Scoring System

**File:** `post_scorer.py` | **Class:** `PostScorer`

Every search feed post is independently scored before rendering. Priority account posts bypass scoring entirely. The score threshold is configurable live via the Keyword Sweep noise filter slider (default: 5).

### Active Filters (no extra API calls)

| ID | Filter | Signal | Points |
|----|--------|---------|--------|
| F1 | Content length | < 20 chars | +5 |
| F2 | Account age | < 3 days old | +5 |
| F2 | Account age | 3–7 days old | +2 |
| F3 | Hashtag count | > 3 tags | +4 |
| F3 | Hashtag count | == 3 tags | +2 |
| F4 | Engagement | zero likes + reposts + replies | +1 |
| F4 | Engagement | ≥ 3 total (bonus) | −1 |
| F5 | Solicitation | follow-farming phrases | +5 |
| F6 | Reply detection | post is a reply to another post | +3 |
| F7 | Language | non-English `record.langs[]` | +3 |
| F11 | URL-only | < 3 real words after stripping URLs/filler | +4 |
| F12 | Excessive mentions | > 3 @mentions | +2 |
| F12 | Excessive mentions | > 5 @mentions | +4 |
| F13 | Repeated handle | same handle 3×+ in one poll batch | +2–3 |

**Score buckets** (with default threshold 5):
- **clean** — score 0–2: show normally
- **dim** — score 3–4: show at 42% opacity, desaturated (hover to restore)
- **hide** — score ≥ 5: filtered from display (still passed to event detector)

The hide threshold and dim threshold (auto-set to threshold − 2) are adjustable live via the slider in the Keyword Sweep panel.

### Pending Filters (require `getProfile` API calls)

| ID | Filter | Signal | Points | Status |
|----|--------|---------|--------|--------|
| F8 | Follower count | < 10 / < 50 followers | +4 / +2 | Disabled |
| F9 | Post count | < 50 / < 100 posts | +4 / +2 | Disabled |
| F10 | Bio description | missing / < 20 chars | +5 / +2 | Disabled |

Enable when profile cache is implemented: `scorer.enable_filter('follower_count')`.

### Extending

```python
class MyFilter(NoiseFilter):
    id    = 'my_filter'
    label = 'My custom filter'

    def score(self, post: dict) -> tuple:
        # Return (points, reason_string) or (0, None)
        if some_condition(post):
            return 4, 'my_reason'
        return 0, None

scorer = get_scorer()
scorer.register(MyFilter())
```

---

## Event Detection Engine

**File:** `event_detector.py` | **Class:** `EventDetector`

Uses a pluggable strategy pattern — add new strategies via `detector.add_strategy(strategy)`.

### Detection Strategies

#### KeywordClusterStrategy
Groups posts by shared keywords within a 10-minute sliding window.

1. Each post is matched against `BREAKING_KEYWORDS` — tiered vocabulary (CRITICAL / HIGH / MEDIUM)
2. NLP entity check: named entity + geo-action verb generates a synthetic keyword (e.g., `ent:Kim Jong Un+launches`)
3. Wire-format detection (`_detect_wire_caps`) catches all-caps headlines
4. Posts sharing a keyword within the window are grouped into one event
5. Threshold: `CLUSTER_THRESHOLD = 3` weighted posts, or 1 post from a tier-5 source
6. Events get a `weighted_count` — major news orgs count up to 5× a generic account
7. **Negation check:** posts are dropped if the matched keyword is negated in context
8. **Semantic dedup:** new events are compared against the rolling event buffer; paraphrases are suppressed

**Source tier weights:**

| Weight | Sources |
|--------|---------|
| 5 | Reuters, AP, AFP, BBC |
| 4 | NYT, WSJ, FT, Bloomberg, The Economist |
| 3 | Guardian, WaPo, Nikkei, Haaretz, DW |
| 2 | CNBC, CNN, unusual_whales, fintwitter |
| 1 | All others |

#### VelocitySpikeStrategy
Detects sudden volume surges independent of keyword matching. Tokens exceeding `SPIKE_THRESHOLD` (weight ≥ 10) in a sliding window are promoted to MEDIUM events, after filtering `VELOCITY_NOISE_WORDS`. Requires a proper noun in ≥2 posts near the spiking word. Drives the collapsible **Spikes** bar in the Event Dashboard.

#### ZeroShotStrategy *(requires sentence-transformers)*
For each post with a named entity but no keyword match, classifies text against seven category descriptions using cosine similarity. Fires a MEDIUM event if confidence ≥ 0.32.

**Categories:** Military Action, Natural Disaster, Economic/Financial, Political/Government, Crime/Security, Health/Medical, Technology/Cyber.

### Event Lifecycle

| State | Age |
|-------|-----|
| `breaking` | < 30 minutes |
| `developing` | 30 min – 4 hours |
| `stale` | > 4 hours (dropped on next pass) |

Maximum 50 events in window.

---

## NLP Enhancement Layer

**File:** `nlp_enhancer.py` | **Class:** `NLPEnhancer`

All features degrade gracefully when optional dependencies are missing. The module prints its active mode on startup: `NLPEnhancer(p1=regex, p3/4=EmbeddingEngine(tfidf))`.

### Phase 1 — Named Entity Recognition + Negation Detection

**NER** — Extracts named entities via spaCy (if installed) or a five-pass regex cascade:
1. Multi-word consecutive caps (`Kim Jong Un`, `White House`)
2. Geographic connectors (`Sea of Japan`, `Gulf of Mexico`)
3. `the X` patterns (`the Fed`, `the Kremlin`, `the Pentagon`)
4. Sentence-initial names (`Zelenskyy announces…`)
5. ALL-CAPS acronyms (`NATO`, `FBI`, `IMF`)

**Entity severity upgrade** — Named entity + HIGH verb (`launches`, `strikes`, `kills`) → upgrade to HIGH. Named entity + MEDIUM verb (`announces`, `declares`, `raises`) → upgrade to MEDIUM.

**Negation detection** — 60-character window around each matched keyword checks for `no `, `not `, `denied`, `ruled out`, etc. With spaCy: uses dependency-parse negation arcs.

### Phase 3 — Semantic Event Deduplication

Rolling deque of the last 50 event embeddings. Cosine similarity ≥ 0.75 → suppressed as duplicate. Fallback without `sentence-transformers`: hybrid TF-IDF (word bigrams + character trigrams), threshold 0.40.

### Phase 4 — Zero-shot Category Classification

Seven category descriptions are pre-embedded at startup. Score ≥ 0.32 + named entity → eligible for `ZeroShotStrategy`. Only active with `sentence-transformers`.

**Install:**
```bash
pip install spacy && python -m spacy download en_core_web_sm  # Phase 1
pip install sentence-transformers                              # Phase 3 + 4
# all-MiniLM-L6-v2 (~90MB) downloads automatically on first use
```

---

## Semantic Market Matcher

**File:** `kalshi_feed.py` + `app.py`

### Corpus Construction
On each 30-second poll, `app.py` builds a text corpus from CRITICAL/HIGH events + up to 60 recent posts and updates the background scorer.

### Token Indexing
Each market is pre-indexed using word bigrams: `_expand_tokens(text) → frozenset(unigrams | word_bigrams)`. The `_tok` key is in-memory only, stripped before writing the disk cache.

### Scoring Formula
**Market-side coverage:** `intersection / len(market_tokens)`

| Score | Meaning |
|-------|---------|
| ≥ 0.40 | Market is substantially about the current event |
| 0.20–0.40 | Strong topical overlap |
| 0.10–0.20 | Loose relevance |

### Sort Modes

| Mode | Formula | Use case |
|------|---------|---------|
| **Confidence** | `_score` desc | Default |
| **Prob ↓/↑** | `yes_ask` price | Over/under-priced markets |
| **Value** | `_score × (1 − \|yes_ask−50\| / 50)` | High confidence + price uncertainty |

### Background Thread Architecture
```
/api/kalshi/match request
    ├─ build corpus from events + posts
    ├─ update_match_corpus(texts) → returns instantly
    │     ├─ hash unchanged? → skip
    │     └─ spawn daemon → score all markets → cache
    └─ get_match_results() → returns last cached results
```

---

## Market Dashboard Page

### All Markets Column
Category pills → series list → paginated market cards. Price/days dual range sliders, sort strip, search bar.

### Event Matches Column
Confidence slider (default ≥0.15), sort modes, category filter pills, event deduplication with expandable siblings.

### Kalshi API Resilience
- Pagination retries up to 3× with 60-second timeouts
- Safety guard: new pull must return ≥ `max(1000, existing_count/2)` markets before overwriting cache
- `KXMVE*` series excluded (sports parlay markets)

### Loading Overlay
Polls `/api/kalshi/status` every 3 seconds. Shows overlay while `count < 1000`. Dismisses and initializes UI directly — no page reload.

---

## Trading Strategy Panel

All four tabs auto-refresh every 60 seconds, fully paginated (500 markets/API page, 50/display page). Category filter pills on each tab.

| Tab | Criteria | Sort |
|-----|----------|------|
| **Near Expiry** | Closes in ≤ N days, price 25–75¢ | Days asc |
| **Extreme** | Closes in ≤ N days (or All), price ≤10¢ or ≥90¢ | Price extremity |
| **Signal** | Semantic score ≥ threshold, price 20–80¢ | Score desc |
| **Tension** | Semantic score ≥ threshold, price 30–70¢, days ≤ N | Score desc |

---

## Market Indices Bar

**File:** `market_indices.py` | Polls Yahoo Finance every 60 seconds. On Event Dashboard only.

**Row 1 — Equities:** S&P 500, NASDAQ, DOW, DAX, FTSE 100, CAC 40  
**Row 2 — Commodities:** VIX, Brent Crude, WTI Crude, Natural Gas, Gasoline (AAA), Bitcoin  

Each tile shows: label, status badge (OPEN/CLOSED/FUTURES/AAA), current price, % change (color-coded green/red), O/H/L meta row, sparkline, and countdown to close/open.

---

## Gas Prices (AAA)

**File:** `gas_prices.py` | `GasPricesManager`

Scrapes `gasprices.aaa.com` for US national average retail prices. Refreshes at **00:00, 08:00, 16:00 UTC**. Tile shows current Regular avg with Yest / Wk / Mo comparisons in the meta row.

---

## API Reference

### Feed & Events
```
GET  /api/posts                      Cached posts (150 max) with noise scores + media fields
GET  /api/events                     Detected events with severity, lifecycle, sample posts
GET  /api/status                     System health + module status
GET  /api/feeds                      Active feed list
POST /api/feeds/refresh              Force immediate fetch
```

### Keyword Management
```
GET  /api/feeds/keywords             All feeds with id, query, enabled, custom flag
POST /api/feeds/keywords             Add keyword — body: {query, limit?}
DELETE /api/feeds/keywords/<id>      Remove keyword feed (persisted to custom_feeds.json)
POST /api/feeds/keywords/<id>/toggle Toggle enabled/disabled (persisted)
```

### Account Management
```
GET  /api/accounts                   Tracked handles (sorted)
POST /api/accounts                   Add handle — body: {handle} (persisted to accounts.txt)
DELETE /api/accounts/<handle>        Remove handle (persisted)
POST /api/accounts/reload            Reload from accounts.txt without restart
```

### Market Data
```
GET  /api/markets                    Market indices (Yahoo Finance + AAA)
GET  /api/gas                        AAA gas prices (all grades + direction)
GET  /api/kalshi/status              Cache status, market count, last updated
GET  /api/kalshi/series              Series list — params: category, q
GET  /api/kalshi/markets             Markets — params: category, series_ticker, q,
                                       min/max_price, min/max_days, sort, page, per_page
GET  /api/kalshi/match               Semantic match results (instant, background-computed)
GET  /api/kalshi/match_detail        Per-source score breakdown for one market
POST /api/kalshi/refresh             Trigger immediate Kalshi re-fetch
```

---

## Setup & Running

**Requirements:** Python 3.10+

```bash
# Core (required)
pip install flask flask-cors requests beautifulsoup4 scikit-learn numpy scipy python-dotenv

# NLP Phase 1 — real NER + dependency-parse negation
pip install spacy
python -m spacy download en_core_web_sm

# NLP Phases 3 + 4 — semantic dedup + zero-shot classification
pip install sentence-transformers
# all-MiniLM-L6-v2 (~90MB) downloads on first use
```

**Credentials** — create `.env` in the project root:
```
BSKY_HANDLE=yourhandle.bsky.social
BSKY_PASSWORD=xxxx-xxxx-xxxx-xxxx
```
Use a Bluesky App Password (Settings → Privacy and Security → App Passwords).

**Run:**
```bash
python app.py
# → http://localhost:5001
```

The Bluesky poll thread starts immediately. Kalshi data loads in the background (~30–60 seconds). The loading overlay on the Market Dashboard dismisses automatically when the cache is ready.

---

## Configuration & Tuning

### `bluesky_feed.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `MAX_CACHED_POSTS` | 150 | Posts held in memory |
| `PRIORITY_POST_WEIGHT` | 3 | How much extra weight priority-account posts get in event scoring |
| `FEED_CONFIG` | 8 feeds | Search queries — edit to add/remove permanent feeds |

**Default keyword feeds:**

| ID | Query | Limit |
|----|-------|-------|
| `breaking` | `breaking` | 30 |
| `just_in` | `just in` | 20 |
| `developing` | `developing story` | 20 |
| `flash` | `flash alert` | 15 |
| `explosion` | `explosion attack strike` | 20 |
| `earthquake` | `earthquake hurricane tornado wildfire` | 20 |
| `markets` | `market crash rate hike fed reserve` | 20 |
| `geopolitical` | `missile launches troops invasion sanctions` | 20 |

### `post_scorer.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `SCORE_DIM` | 3 | Score threshold for dimming |
| `SCORE_HIDE` | 5 | Score threshold for hiding (overridden by UI slider) |

### `event_detector.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `CLUSTER_THRESHOLD` | 3 | Min weighted posts to form a cluster event |
| `CLUSTER_WINDOW_MINUTES` | 10 | Sliding window for keyword clustering |
| `MAX_EVENTS` | 50 | Max events in rolling window |
| `AGE_BREAKING_MAX` | 30 min | "breaking" status duration |
| `AGE_DEVELOPING_MAX` | 240 min | "developing" status duration |

### `nlp_enhancer.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `DEDUP_THRESHOLD_SEMANTIC` | 0.75 | Cosine similarity cutoff for semantic dedup |
| `DEDUP_THRESHOLD_TFIDF` | 0.40 | TF-IDF fallback dedup cutoff |
| `ZERO_SHOT_MIN_CONFIDENCE` | 0.32 | Min score to fire a zero-shot event |

### `kalshi_feed.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `THRESHOLD_LOW` | 0.15 | Pre-filter for background scorer |
| `_BLOCKED_SERIES_PREFIXES` | `('KXMVE',)` | Series excluded from all results |
| `PAGE_LIMIT` | 1000 | Markets per Kalshi API page |

### `gas_prices.py`

| Constant | Default | Effect |
|----------|---------|--------|
| `REFRESH_HOURS_UTC` | `{0, 8, 16}` | UTC hours to re-scrape AAA |

### Adding a Detection Strategy
```python
from event_detector import DetectionStrategy

class MyStrategy(DetectionStrategy):
    def analyze(self, posts, existing_events):
        return []  # list of new event dicts

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

---

## Version History

| Version | Summary |
|---------|---------|
| v1.0 | Core pipeline: Bluesky feed, event detection, Kalshi browser, market indices bar |
| v1.1 | NLP integration (Phases 1/3/4), noise scoring system (F1–F7), AAA gas prices via scraper, velocity spike detection, semantic event matching, strategy panel |
| v1.2 | **Keyword Sweep** column replacing Event Matches panel; **noise scoring** expanded (F11–F13: URL-only, excessive mentions, repeated handle); **collapsible bars** (Spikes / Keywords / Tracked Accounts) with unified `.cbar` system; **live keyword management** (add/pause/remove from UI); **account management** from UI; **full persistence** (accounts.txt, custom_feeds.json); **media badges** (🖼 image / ▶ video / 🔗 link) on all post cards; full post text (no truncation); **noise filter slider** (3–10, live re-render); noise score displayed on each post; comprehensive **UI readability pass** (panel headers, market bar, nav links); "error — retrying" status bug fixed; title 16px across all pages |