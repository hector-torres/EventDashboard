"""
Event Dashboard
Main Flask application - modular, extensible architecture
"""

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
import threading
import time
import os

from bluesky_feed import BlueSkyFeedManager
from event_detector import EventDetector
from market_indices import MarketIndicesManager
from kalshi_feed import KalshiFeedManager
from gas_prices import GasPricesManager

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)

# ─── Module Initialization ────────────────────────────────────────────────────
feed_manager   = BlueSkyFeedManager()
event_detector = EventDetector()
market_manager = MarketIndicesManager()
kalshi_manager = KalshiFeedManager()   # starts its own daily background thread
gas_manager    = GasPricesManager()

# kalshi_manager handles series internally

# ─── Background Polling Threads ───────────────────────────────────────────────
MARKET_POLL_INTERVAL = 60

def poll_feeds():
    while True:
        try:
            posts = feed_manager.fetch_latest()
            if posts:
                event_detector.analyze(posts)
        except Exception as e:
            print(f"[Poll Error] {e}")
            feed_manager.status = f"error — retrying"
        time.sleep(30)

def poll_markets():
    while True:
        try:
            market_manager.fetch_all(gas_manager=gas_manager)
        except Exception as e:
            print(f"[Market Poll Error] {e}")
        time.sleep(MARKET_POLL_INTERVAL)

threading.Thread(target=poll_feeds,   daemon=True).start()
threading.Thread(target=poll_markets, daemon=True).start()
gas_manager.start()   # scrapes AAA at 00:00, 08:00, 16:00 UTC

# ─── Page routes ──────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/dashboard')
def dashboard():
    return send_from_directory('.', 'dashboard.html')

@app.route('/markets')
def markets():
    return send_from_directory('.', 'markets.html')

# ─── Bluesky / Events routes ──────────────────────────────────────────────────

@app.route('/api/posts')
def get_posts():
    return jsonify({
        'posts':        feed_manager.get_cached_posts(),
        'last_updated': feed_manager.last_updated,
        'status':       feed_manager.status,
    })

@app.route('/api/events')
def get_events():
    return jsonify({
        'events':      event_detector.get_events(),
        'event_count': len(event_detector.get_events()),
    })

@app.route('/api/status')
def get_status():
    ks = kalshi_manager.get_status()
    return jsonify({
        'platform': 'EventDashboard',
        'version':  '1.1.0',
        'feeds_active':    len(feed_manager.active_feeds),
        'posts_cached':    len(feed_manager.get_cached_posts()),
        'events_detected': len(event_detector.get_events()),
        'poll_interval':   30,
        'kalshi':          ks,
        'modules': {
            'bluesky_feed':   'active',
            'event_detector': 'active',
            'kalshi_feed':    ks['status'],
        }
    })

@app.route('/api/feeds')
def get_feeds():
    return jsonify({'feeds': feed_manager.active_feeds})

@app.route('/api/feeds/refresh', methods=['POST'])
def refresh_feeds():
    try:
        posts = feed_manager.fetch_latest(force=True)
        event_detector.analyze(posts)
        return jsonify({'success': True, 'posts_fetched': len(posts)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/feeds/keywords', methods=['GET'])
def get_keywords():
    """Return current active feeds with enabled state."""
    return jsonify({'feeds': feed_manager.active_feeds})


@app.route('/api/feeds/keywords', methods=['POST'])
def add_keyword():
    """Add a new keyword query feed. Body: {query, limit?}"""
    data  = request.get_json() or {}
    query = (data.get('query') or '').strip()
    limit = int(data.get('limit', 20))
    if not query:
        return jsonify({'success': False, 'error': 'query required'}), 400
    feed = feed_manager.add_keyword(query, limit)
    if feed is None:
        return jsonify({'success': False, 'error': 'duplicate or empty query'}), 409
    return jsonify({'success': True, 'feed': feed})


@app.route('/api/feeds/keywords/<feed_id>', methods=['DELETE'])
def remove_keyword(feed_id):
    """Remove a keyword feed by id."""
    removed = feed_manager.remove_keyword(feed_id)
    return jsonify({'success': removed})


@app.route('/api/feeds/keywords/<feed_id>/toggle', methods=['POST'])
def toggle_keyword(feed_id):
    """Toggle a feed enabled/disabled."""
    enabled = feed_manager.toggle_keyword(feed_id)
    return jsonify({'success': True, 'enabled': enabled})

@app.route('/api/accounts')
def get_accounts():
    return jsonify({
        'accounts': sorted(list(feed_manager.priority_handles)),
        'count':    len(feed_manager.priority_handles),
    })

@app.route('/api/accounts/reload', methods=['POST'])
def reload_accounts():
    handles = feed_manager.reload_priority_accounts()
    return jsonify({'success': True, 'accounts': sorted(handles), 'count': len(handles)})


@app.route('/api/accounts', methods=['POST'])
def add_account():
    """Add a new tracked account. Body: {handle}"""
    data   = request.get_json() or {}
    handle = (data.get('handle') or '').strip().lstrip('@').lower()
    if not handle:
        return jsonify({'success': False, 'error': 'handle required'}), 400
    added = feed_manager.add_account(handle)
    if not added:
        return jsonify({'success': False, 'error': 'already tracked or invalid'}), 409
    return jsonify({'success': True, 'handle': handle,
                    'accounts': sorted(list(feed_manager.priority_handles))})


@app.route('/api/accounts/<handle>', methods=['DELETE'])
def remove_account(handle):
    """Remove a tracked account by handle."""
    removed = feed_manager.remove_account(handle)
    return jsonify({'success': removed,
                    'accounts': sorted(list(feed_manager.priority_handles))})

# ─── Market indices routes ────────────────────────────────────────────────────

@app.route('/api/markets')
def get_markets():
    return jsonify({
        'indices':      market_manager.get_cached(),
        'last_updated': market_manager.last_updated,
        'status':       market_manager.status,
    })

@app.route('/api/gas')
def api_gas():
    """AAA national average gas prices (updated ~every 8h)."""
    return jsonify(gas_manager.get_data())


@app.route('/api/markets/refresh', methods=['POST'])
def refresh_markets():
    try:
        indices = market_manager.fetch_all(gas_manager=gas_manager)
        return jsonify({'success': True, 'count': len(indices)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ─── Kalshi routes ────────────────────────────────────────────────────────────

@app.route('/api/kalshi/status')
def kalshi_status():
    return jsonify(kalshi_manager.get_status())


@app.route('/api/kalshi/series')
def kalshi_series():
    """Return series list for the browse UI, plus the set of active series tickers."""
    series = kalshi_manager.get_series()
    # Collect distinct series tickers present on stored markets
    markets = kalshi_manager.get_markets()
    active_tickers = set()
    for m in markets:
        st = m.get('series_ticker', '')
        if not st:
            et = m.get('event_ticker', '')
            parts = et.rsplit('-', 1)
            st = parts[0] if len(parts) == 2 else et
        if st:
            active_tickers.add(st)

    # Fallback: if the series cache is empty (e.g. series fetch timed out on startup),
    # synthesize minimal series objects from the active tickers on stored markets so
    # the browse UI still works. Full series metadata arrives once the background
    # series fetch completes and the cache is populated.
    if not series and active_tickers:
        cat_map = kalshi_manager._cat_map
        series = [
            {
                'ticker':    t,
                'title':     t,   # title will be enriched once real series data arrives
                'category':  cat_map.get(t, ''),
                'frequency': '',
            }
            for t in sorted(active_tickers)
        ]

    return jsonify({'series': series, 'active_tickers': sorted(active_tickers)})


# ── Location alias map — maps city names to Kalshi location codes ─────────────
# Kalshi uses airport/weather-station codes in tickers (e.g. SATX = San Antonio TX).
# This lets users search by city name and find the relevant markets.
_LOCATION_ALIASES: dict = {
    'san antonio':   ['satx'],
    'new york':      ['nyc', 'jfk', 'lga', 'ewr'],
    'los angeles':   ['lax', 'la'],
    'chicago':       ['chi', 'ord', 'mdw'],
    'houston':       ['hou', 'iah', 'hou'],
    'phoenix':       ['phx'],
    'philadelphia':  ['phl', 'phi'],
    'san francisco': ['sfo', 'sf', 'sfom'],
    'seattle':       ['sea'],
    'denver':        ['den'],
    'boston':        ['bos'],
    'atlanta':       ['atl'],
    'miami':         ['mia'],
    'dallas':        ['dfw', 'dal'],
    'minneapolis':   ['msp', 'min'],
    'portland':      ['pdx'],
    'las vegas':     ['las'],
    'detroit':       ['dtw', 'det'],
    'baltimore':     ['bwi', 'bal'],
    'washington':    ['dca', 'iad', 'was'],
    'new orleans':   ['msy', 'no'],
    'salt lake':     ['slc'],
    'kansas city':   ['mci', 'kci'],
    'memphis':       ['mem'],
    'nashville':     ['bna'],
    'charlotte':     ['clt'],
    'raleigh':       ['rdu'],
    'indianapolis':  ['ind'],
    'columbus':      ['cmh'],
    'jacksonville':  ['jax'],
    'austin':        ['aus'],
    'fort worth':    ['dfw'],
    'oklahoma city': ['okc'],
    'el paso':       ['elp'],
    'tucson':        ['tus'],
    'albuquerque':   ['abq'],
    'sacramento':    ['smf'],
    'san jose':      ['sjc'],
    'san diego':     ['san'],
    'tampa':         ['tpa'],
    'orlando':       ['mco', 'orl'],
    'pittsburgh':    ['pit'],
    'cincinnati':    ['cvg'],
    'st louis':      ['stl'],
    'cleveland':     ['cle'],
    'milwaukee':     ['mke'],
    'richmond':      ['ric'],
}

@app.route('/api/kalshi/markets')
def kalshi_markets():
    """
    Return filtered Kalshi markets (paginated).
    Query params: category, min_price, max_price, min_days, max_days, page, per_page
    """
    category      = request.args.get('category')      or None
    series_ticker = request.args.get('series_ticker') or None
    event_ticker  = request.args.get('event_ticker')  or None
    search_query  = (request.args.get('q') or '').strip().lower()
    min_price = float(request.args.get('min_price', 0))
    max_price = float(request.args.get('max_price', 100))
    min_days  = request.args.get('min_days')
    max_days  = request.args.get('max_days')
    page      = max(1, int(request.args.get('page', 1)))
    per_page  = max(1, int(request.args.get('per_page', 15)))

    markets = kalshi_manager.filter_markets(
        category      = category,
        series_ticker = series_ticker,
        event_ticker  = event_ticker,
        min_price     = min_price,
        max_price     = max_price,
        min_days      = float(min_days) if min_days else None,
        max_days      = float(max_days) if max_days else None,
    )

    # Text search filter (applied after fetch, title + subtitle)
    if search_query:
        # Expand search terms using location aliases (e.g. "san antonio" → "satx")
        search_terms = {search_query}
        for city, codes in _LOCATION_ALIASES.items():
            if city in search_query or search_query in city:
                search_terms.update(codes)
            else:
                for code in codes:
                    if code == search_query:
                        search_terms.add(city)

        def _matches(m):
            fields = [
                (m.get('title') or '').lower(),
                (m.get('subtitle') or '').lower(),
                (m.get('ticker') or '').lower(),
                (m.get('series_ticker') or '').lower(),
                (m.get('event_ticker') or '').lower(),
            ]
            return any(term in field for term in search_terms for field in fields)

        markets = [m for m in markets if _matches(m)]

    total       = len(markets)
    total_pages = max(1, -(-total // per_page))
    start       = (page - 1) * per_page

    return jsonify({
        'markets':     _serialize_markets(markets[start : start + per_page]),
        'total':       total,
        'page':        page,
        'per_page':    per_page,
        'total_pages': total_pages,
        'status':      kalshi_manager.get_status(),
    })

@app.route('/api/kalshi/match')
def kalshi_match():
    """
    Semantic match markets against current events + recent posts.
    Scoring runs in a background thread — this always returns instantly.
    Query params: threshold, top_n, page, per_page
    """
    threshold = float(request.args.get('threshold', 0.15))
    top_n     = int(request.args.get('top_n', 200))
    page      = max(1, int(request.args.get('page', 1)))
    per_page  = max(1, int(request.args.get('per_page', 15)))

    events = event_detector.get_events()
    posts  = feed_manager.get_cached_posts()

    # Build match corpus from events + recent posts
    # Use full sample_post sentences (richer than just title+keyword) for better matching.
    # Filter out MEDIUM events whose keyword is a generic English word — these produce
    # noisy corpus tokens that cause false positives.
    _CORPUS_STOP = {
        'available','former','including','nearly','changing','whether','turning',
        'social','current','issued','action','service','members','financial',
        'companies','director','officials','warning','strategy','soaring',
        'investigating','almost','based','following','reported','claims',
        'report','reports','new','old','big','high','low','good','bad',
        'long','short','last','first','next','time','times','part','place',
        'world','people','country','government','president','minister','official',
    }

    texts = []
    for e in events:
        sev = e.get('severity', '')
        kw  = e.get('keyword', '').lower().replace('geo:','').replace('velocity_','').split('+')[0]
        wt  = e.get('weighted_count', 0)
        # Always include HIGH/CRITICAL; include MEDIUM only if keyword is meaningful
        if sev not in ('HIGH', 'CRITICAL') and kw in _CORPUS_STOP:
            continue
        # Add sample post sentences (full context beats title/keyword alone)
        for post in e.get('sample_posts', [])[:2]:
            if post and len(post.strip()) > 20:
                texts.append(post.strip())
        # Also add the event title as a fallback
        title = e.get('title', '').strip()
        if title:
            texts.append(title)
    texts += [p.get('text', '') for p in posts[:60]]
    texts  = [t for t in texts if t]

    if not texts:
        return jsonify({
            'markets': [], 'total': 0, 'page': 1,
            'per_page': per_page, 'total_pages': 1,
            'corpus': 'No events detected yet',
            'scoring': False,
            'note': 'No events or posts to match against.',
        })

    # Kick off background rescore, return cached results immediately
    kalshi_manager.update_match_corpus(texts)
    cached = kalshi_manager.get_match_results()

    all_matched = [m for m in cached.get('markets', []) if m.get('_score', 0) >= threshold]
    scored_at   = cached.get('scored_at', '')
    is_scoring  = kalshi_manager._match_running

    total       = len(all_matched)
    total_pages = max(1, -(-total // per_page))
    start       = (page - 1) * per_page

    # Build corpus summary string
    n_events = len(events)
    n_posts  = min(len(posts), 60)
    corpus_str = f'{n_events} event{"s" if n_events != 1 else ""} · {n_posts} post{"s" if n_posts != 1 else ""}'
    if scored_at:
        try:
            from datetime import datetime, timezone
            dt    = datetime.fromisoformat(scored_at)
            age_s = int((datetime.now(timezone.utc) - dt).total_seconds())
            corpus_str += f' · scored {age_s}s ago'
        except Exception:
            pass
    if is_scoring:
        corpus_str += ' · scoring…'

    return jsonify({
        'markets':     _serialize_markets(all_matched[start : start + per_page]),
        'total':       total,
        'page':        page,
        'per_page':    per_page,
        'total_pages': total_pages,
        'threshold':   threshold,
        'corpus':      corpus_str,
        'scoring':     is_scoring,
        'status':      kalshi_manager.get_status(),
    })

@app.route('/api/kalshi/refresh', methods=['POST'])
def kalshi_refresh():
    """Manually trigger a Kalshi market re-pull (runs in background)."""
    threading.Thread(target=kalshi_manager.force_refresh, daemon=True).start()
    return jsonify({'success': True, 'message': 'Kalshi refresh triggered.'})

@app.route('/api/kalshi/match_detail')
def kalshi_match_detail():
    """
    For a given market ticker, return per-text match scores showing
    exactly which events/posts drove the semantic match.
    Uses market-side coverage scoring (same as the main matcher).
    Query params: ticker (required), threshold (optional, default 0.05)
    """
    from kalshi_feed import _expand_tokens, _index_market_tokens, score_market_against_corpus
    ticker    = request.args.get('ticker', '').upper()
    threshold = float(request.args.get('threshold', 0.05))

    if not ticker:
        return jsonify({'error': 'ticker param required'}), 400

    with kalshi_manager._lock:
        market = next((m for m in kalshi_manager._markets if m.get('ticker','').upper() == ticker), None)

    if not market:
        return jsonify({'error': f'Market {ticker} not found in cache'}), 404

    _index_market_tokens(market)  # ensure _tok is present
    market_tok = market.get('_tok', frozenset())

    events = event_detector.get_events()
    posts  = feed_manager.get_cached_posts()

    scored_texts = []

    # Score each event individually: build a single-item corpus per event
    for e in events:
        sev = e.get('severity', '')
        # Use sample_posts for richer text, same as the main corpus builder
        texts = [p for p in e.get('sample_posts', [])[:2] if p and len(p.strip()) > 20]
        texts.append(e.get('title', ''))
        for text in texts:
            if not text:
                continue
            tok = _expand_tokens(text)
            if not tok:
                continue
            score = len(market_tok & tok) / len(market_tok) if market_tok else 0.0
            if score >= threshold:
                scored_texts.append({
                    'source':      'event',
                    'text':        text,
                    'score':       round(score, 4),
                    'severity':    sev,
                    'strategy':    e.get('strategy', ''),
                    'keyword':     e.get('keyword', ''),
                    'post_count':  e.get('post_count', 0),
                    'detected_at': e.get('detected_at', ''),
                })
                break  # one entry per event (best text already picked)

    # Score each post individually
    for p in posts[:60]:
        text = p.get('text', '').strip()
        if not text:
            continue
        tok = _expand_tokens(text)
        if not tok:
            continue
        score = len(market_tok & tok) / len(market_tok) if market_tok else 0.0
        if score >= threshold:
            scored_texts.append({
                'source':  'post',
                'text':    text,
                'score':   round(score, 4),
                'handle':  p.get('author_handle', ''),
                'display': p.get('author_display', ''),
                'url':     p.get('url', ''),
                'is_news': p.get('is_news_account', False),
            })

    scored_texts.sort(key=lambda x: x['score'], reverse=True)
    # Deduplicate: keep only highest-scoring entry per unique text
    seen = set()
    deduped = []
    for item in scored_texts:
        key = item['text'][:80]
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    overall_score = max((x['score'] for x in deduped), default=0.0)

    return jsonify({
        'ticker':        ticker,
        'market_title':  market.get('title', ''),
        'market_text':   f"{market.get('title','')} {market.get('subtitle','') or ''}".strip(),
        'overall_score': round(overall_score, 4),
        'matches':       deduped[:30],
        'threshold':     threshold,
    })


@app.route('/api/kalshi/market/<ticker>')
def kalshi_market_live(ticker):
    """
    Fetch live market data for a single ticker directly from Kalshi API.
    Returns full market object + orderbook + candlesticks. Used by market_detail.html.
    """
    import urllib.request, urllib.error, ssl, json as _json
    ticker = ticker.upper()

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    def _get(url):
        req = urllib.request.Request(url, headers={'Accept': 'application/json'})
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            return _json.loads(resp.read().decode())

    # Try elections API first, fall back to trading API
    BASES = [
        'https://api.elections.kalshi.com/trade-api/v2',
        'https://trading-api.kalshi.com/trade-api/v2',
    ]
    base   = BASES[0]
    result = {}

    # Fetch market — try both API domains
    mkt_data = None
    for b in BASES:
        try:
            mkt_data = _get(f'{b}/markets/{ticker}')
            base = b   # use whichever domain found the market for subsequent calls
            break
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue   # try next domain
            return jsonify({'error': f'Market fetch error: {e.code}'}), 502
        except Exception as e:
            return jsonify({'error': str(e)}), 502
    if mkt_data is None:
        return jsonify({'error': f'Market {ticker} not found on any Kalshi API domain'}), 404
    result['market'] = mkt_data.get('market', mkt_data)

    # Fetch orderbook
    try:
        ob = _get(f'{base}/markets/{ticker}/orderbook')
        result['orderbook'] = ob.get('orderbook', ob.get('orderbook_fp', {}))
    except Exception:
        result['orderbook'] = {}

    # Fetch candlesticks (1d interval, last 30 days)
    try:
        from datetime import datetime, timezone, timedelta
        end_ts   = int(datetime.now(timezone.utc).timestamp())
        start_ts = int((datetime.now(timezone.utc) - timedelta(days=30)).timestamp())

        # series_ticker from the market object is most reliable.
        # Fallback: strip the last hyphen-segment (date/strike suffix).
        # e.g. INXD-23DEC31-B5000 → rsplit gives INXD-23DEC31 which is wrong;
        # the real series ticker is stored on the market object as series_ticker.
        mkt_obj       = result['market']
        series_ticker = (mkt_obj.get('series_ticker') or
                         mkt_obj.get('event_ticker', '').rsplit('-', 1)[0] or
                         ticker.rsplit('-', 1)[0])

        candle_url = (
            f'{base}/series/{series_ticker}/markets/{ticker}/candlesticks'
            f'?start_ts={start_ts}&end_ts={end_ts}&period_interval=1440'
        )
        try:
            candle_resp = _get(candle_url)
            result['candlesticks'] = candle_resp.get('candlesticks', [])
        except Exception as ce:
            # Log to Flask console so we can see the actual error
            import traceback as _tb
            print(f'[market_detail] Candlestick fetch failed for {ticker}: {ce}')
            print(f'[market_detail] URL attempted: {candle_url}')
            _tb.print_exc()
            # Try alternate: some markets use event_ticker directly as series path
            alt_series = mkt_obj.get('event_ticker', '')
            if alt_series and alt_series != series_ticker:
                try:
                    alt_url = (
                        f'{base}/series/{alt_series}/markets/{ticker}/candlesticks'
                        f'?start_ts={start_ts}&end_ts={end_ts}&period_interval=1440'
                    )
                    candle_resp2 = _get(alt_url)
                    result['candlesticks'] = candle_resp2.get('candlesticks', [])
                    print(f'[market_detail] Alt candlestick URL succeeded: {alt_url}')
                except Exception:
                    result['candlesticks'] = []
            else:
                result['candlesticks'] = []
    except Exception as e:
        import traceback as _tb
        print(f'[market_detail] Candlestick setup error for {ticker}: {e}')
        _tb.print_exc()
        result['candlesticks'] = []

    # Fetch sibling markets in the same event (multi-outcome markets like "who will be X?")
    # These share an event_ticker; each sibling's subtitle is the candidate/option name.
    try:
        event_ticker = result['market'].get('event_ticker', '')
        if event_ticker:
            siblings_data = _get(f'{base}/markets?event_ticker={event_ticker}&limit=100&status=open')
            siblings = siblings_data.get('markets', [])
            # Sort by yes_ask ascending (most likely first) and exclude self
            siblings = [m for m in siblings if m.get('ticker', '').upper() != ticker]
            siblings.sort(key=lambda m: float(m.get('yes_ask_dollars') or m.get('yes_ask') or 0), reverse=True)
            result['siblings'] = [
                {
                    'ticker':           m.get('ticker', ''),
                    'subtitle':         m.get('subtitle') or m.get('yes_sub_title') or '',
                    'yes_ask_dollars':  m.get('yes_ask_dollars'),
                    'yes_ask':          m.get('yes_ask'),
                    'no_ask_dollars':   m.get('no_ask_dollars'),
                    'no_ask':           m.get('no_ask'),
                    'volume':           m.get('volume') or m.get('volume_fp'),
                    'last_price_dollars': m.get('last_price_dollars'),
                    'last_price':       m.get('last_price'),
                }
                for m in siblings
            ]
        else:
            result['siblings'] = []
    except Exception:
        result['siblings'] = []

    return jsonify(result)


@app.route('/market_detail')
def market_detail_page():
    """Full market detail page: live pricing, orderbook, price history, semantic matches."""
    return send_from_directory('.', 'market_detail.html')

@app.route('/match_detail')
def match_detail_page():
    """Standalone page showing why a market matched semantically."""
    return send_from_directory('.', 'match_detail.html')


@app.route('/api/polymarket/match')
def polymarket_match():
    """
    Fuzzy-match the given Kalshi ticker against Polymarket markets.
    Uses keyword extraction from the Kalshi market title + subtitle, fires
    1-2 broad searches against the Polymarket Gamma API, scores all results
    with the same token-overlap scorer used for Kalshi market matching, and
    returns the top 5 by similarity score.
    Query params: ticker (required)
    """
    import urllib.request, ssl, json as _json, re as _re
    from kalshi_feed import _expand_tokens

    ticker = (request.args.get('ticker') or '').upper()
    if not ticker:
        return jsonify({'error': 'ticker param required'}), 400

    # ── Get Kalshi market from cache ──────────────────────────────────────────
    with kalshi_manager._lock:
        mkt = next(
            (m for m in kalshi_manager._markets if m.get('ticker', '').upper() == ticker),
            None
        )

    if not mkt:
        return jsonify({'error': f'Market {ticker} not found in cache'}), 404

    title    = (mkt.get('title') or '').strip()
    subtitle = (mkt.get('subtitle') or '').strip()
    combined = f'{title} {subtitle}'.strip()

    # ── Build search keywords: extract meaningful 1-2 word phrases ────────────
    _STOP = {
        'a','an','the','is','are','was','were','will','would','could','should',
        'may','might','do','does','did','have','has','had','be','been','being',
        'and','but','or','nor','for','yet','so','in','on','at','to','of','by',
        'as','if','it','its','this','that','with','from','into','about','over',
        'who','what','which','when','where','how','not','no','than','then',
        'there','their','they','he','she','we','you','i','my','your','our',
        'up','all','any','some','next','new','first','last','more','most',
    }
    words = [w for w in _re.findall(r'[a-zA-Z]{3,}', combined.lower()) if w not in _STOP]
    # Use first 4 meaningful words as the search query — broad enough to get results
    keywords = ' '.join(words[:4]) if words else title[:40]

    # ── Fetch from Polymarket Gamma API ───────────────────────────────────────
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    def _get(url):
        req = urllib.request.Request(
            url,
            headers={'Accept': 'application/json', 'User-Agent': 'EventDashboard/1.3'}
        )
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            return _json.loads(resp.read().decode())

    base = 'https://gamma-api.polymarket.com'
    raw_markets = []

    # Fire two queries: keyword search + a broader fallback using just first 2 words
    queries = [keywords]
    if len(words) > 2:
        queries.append(' '.join(words[:2]))

    seen_ids = set()
    for q in queries:
        try:
            encoded = urllib.request.quote(q)
            results = _get(f'{base}/markets?q={encoded}&limit=30&active=true')
            if isinstance(results, list):
                for m in results:
                    mid = m.get('id') or m.get('conditionId') or m.get('slug')
                    if mid and mid not in seen_ids:
                        seen_ids.add(mid)
                        raw_markets.append(m)
        except Exception:
            pass

    if not raw_markets:
        return jsonify({'matches': [], 'query': keywords, 'note': 'No Polymarket results returned'})

    # ── Score each Polymarket market against the Kalshi title ─────────────────
    kalshi_tok = _expand_tokens(combined)
    scored = []

    for m in raw_markets:
        question = (m.get('question') or '').strip()
        description = (m.get('description') or '')[:200]
        pm_text = f'{question} {description}'.strip()
        pm_tok  = _expand_tokens(pm_text)

        if not pm_tok or not kalshi_tok:
            continue

        # Bidirectional coverage: average of (kalshi tokens in pm) and (pm tokens in kalshi)
        # This handles both short and long market titles fairly
        fwd = len(kalshi_tok & pm_tok) / len(kalshi_tok)
        rev = len(kalshi_tok & pm_tok) / len(pm_tok)
        score = round((fwd + rev) / 2, 4)

        if score < 0.05:
            continue

        # Parse outcomePrices (returned as JSON string by Polymarket)
        outcome_prices_raw = m.get('outcomePrices') or '[]'
        try:
            prices = _json.loads(outcome_prices_raw) if isinstance(outcome_prices_raw, str) else outcome_prices_raw
            prices = [float(p) for p in prices]
        except Exception:
            prices = []

        outcomes_raw = m.get('outcomes') or '[]'
        try:
            outcomes = _json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else outcomes_raw
        except Exception:
            outcomes = []

        yes_price = None
        if prices:
            # For binary markets outcomes[0] is typically Yes
            if outcomes and str(outcomes[0]).lower() in ('yes', 'true', '1'):
                yes_price = prices[0]
            else:
                yes_price = prices[0]   # fallback: first price

        slug       = m.get('slug') or ''
        # events field may contain the event slug for URL building
        event_slug = ''
        events_list = m.get('events') or []
        if isinstance(events_list, list) and events_list:
            event_slug = (events_list[0].get('slug') or '')

        url = f'https://polymarket.com/event/{event_slug}/{slug}' if event_slug else f'https://polymarket.com/market/{slug}'

        vol = m.get('volume')
        liq = m.get('liquidity')

        scored.append({
            'score':       score,
            'question':    question,
            'slug':        slug,
            'url':         url,
            'yes_price':   round(yes_price, 4) if yes_price is not None else None,
            'yes_pct':     round(yes_price * 100) if yes_price is not None else None,
            'outcomes':    outcomes[:6] if outcomes else [],
            'prices':      [round(float(p), 4) for p in prices[:6]],
            'volume':      round(float(vol), 2) if vol else None,
            'liquidity':   round(float(liq), 2) if liq else None,
            'end_date':    m.get('endDate') or m.get('end_date') or '',
            'active':      m.get('active', True),
            'closed':      m.get('closed', False),
        })

    # Filter out closed/resolved markets — active=true in the API query isn't
    # always sufficient; the 'closed' field is the reliable signal.
    scored = [m for m in scored if not m.get('closed')]

    scored.sort(key=lambda x: x['score'], reverse=True)

    return jsonify({
        'matches': scored[:5],
        'query':   keywords,
        'kalshi_title': title,
    })



# ── Manifold Markets comparison ───────────────────────────────────────────────
@app.route('/api/manifold/match')
def manifold_match():
    """Fuzzy-match Kalshi ticker against Manifold Markets (no auth required)."""
    import urllib.request, ssl, json as _json, re as _re
    from kalshi_feed import _expand_tokens

    ticker = (request.args.get('ticker') or '').upper()
    if not ticker:
        return jsonify({'error': 'ticker param required'}), 400

    with kalshi_manager._lock:
        mkt = next((m for m in kalshi_manager._markets
                    if m.get('ticker', '').upper() == ticker), None)
    if not mkt:
        return jsonify({'error': f'Market {ticker} not found in cache'}), 404

    title    = (mkt.get('title') or '').strip()
    subtitle = (mkt.get('subtitle') or '').strip()
    combined = f'{title} {subtitle}'.strip()
    _STOP = {
        'a','an','the','is','are','was','were','will','would','could','should',
        'may','might','do','does','did','have','has','had','be','been','being',
        'and','but','or','nor','for','yet','so','in','on','at','to','of','by',
        'as','if','it','its','this','that','with','from','into','about','over',
        'who','what','which','when','where','how','not','no','than','then',
        'there','their','they','he','she','we','you','i','my','your','our',
        'up','all','any','some','next','new','first','last','more','most',
    }
    words    = [w for w in _re.findall(r'[a-zA-Z]{3,}', combined.lower()) if w not in _STOP]
    keywords = ' '.join(words[:4]) if words else title[:40]

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    def _get(url):
        req = urllib.request.Request(url, headers={
            'Accept': 'application/json', 'User-Agent': 'EventDashboard/1.6'})
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            return _json.loads(resp.read().decode())

    raw = []
    try:
        enc  = urllib.request.quote(keywords)
        data = _get(f'https://api.manifold.markets/v0/search-markets?term={enc}&limit=25')
        if isinstance(data, list):
            raw = data
    except Exception:
        pass

    if not raw:
        return jsonify({'matches': [], 'query': keywords,
                        'note': 'No Manifold results returned'})

    kalshi_tok = _expand_tokens(combined)
    scored = []
    for m in raw:
        if m.get('isResolved'):
            continue
        question = (m.get('question') or '').strip()
        pm_tok   = _expand_tokens(question)
        if not pm_tok or not kalshi_tok:
            continue
        fwd   = len(kalshi_tok & pm_tok) / len(kalshi_tok)
        rev   = len(kalshi_tok & pm_tok) / len(pm_tok)
        score = round((fwd + rev) / 2, 4)
        if score < 0.05:
            continue
        prob      = m.get('probability')
        yes_pct   = round(prob * 100) if prob is not None else None
        close_ts  = m.get('closeTime')
        try:
            from datetime import datetime
            close_str = datetime.fromtimestamp(close_ts / 1000).strftime('%b %d, %Y') if close_ts else None
        except Exception:
            close_str = None
        scored.append({
            'score':    score,
            'question': question,
            'url':      m.get('url') or f'https://manifold.markets/market/{m.get("id","")}',
            'yes_pct':  yes_pct,
            'volume':   round(float(m['volume']), 2) if m.get('volume') else None,
            'end_date': close_str,
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'matches': scored[:5], 'query': keywords})


# ── Metaculus comparison ──────────────────────────────────────────────────────
@app.route('/api/metaculus/match')
def metaculus_match():
    """Fuzzy-match Kalshi ticker against Metaculus questions (no auth required)."""
    import urllib.request, ssl, json as _json, re as _re
    from kalshi_feed import _expand_tokens

    ticker = (request.args.get('ticker') or '').upper()
    if not ticker:
        return jsonify({'error': 'ticker param required'}), 400

    with kalshi_manager._lock:
        mkt = next((m for m in kalshi_manager._markets
                    if m.get('ticker', '').upper() == ticker), None)
    if not mkt:
        return jsonify({'error': f'Market {ticker} not found in cache'}), 404

    title    = (mkt.get('title') or '').strip()
    subtitle = (mkt.get('subtitle') or '').strip()
    combined = f'{title} {subtitle}'.strip()
    _STOP = {
        'a','an','the','is','are','was','were','will','would','could','should',
        'may','might','do','does','did','have','has','had','be','been','being',
        'and','but','or','nor','for','yet','so','in','on','at','to','of','by',
        'as','if','it','its','this','that','with','from','into','about','over',
        'who','what','which','when','where','how','not','no','than','then',
        'there','their','they','he','she','we','you','i','my','your','our',
        'up','all','any','some','next','new','first','last','more','most',
    }
    words    = [w for w in _re.findall(r'[a-zA-Z]{3,}', combined.lower()) if w not in _STOP]
    keywords = ' '.join(words[:4]) if words else title[:40]

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    def _get(url):
        req = urllib.request.Request(url, headers={
            'Accept': 'application/json', 'User-Agent': 'EventDashboard/1.6'})
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=15) as resp:
            return _json.loads(resp.read().decode())

    raw_results = []
    try:
        enc  = urllib.request.quote(keywords)
        data = _get(f'https://www.metaculus.com/api2/questions/?search={enc}&limit=20&status=open&type=forecast')
        raw_results = data.get('results', []) if isinstance(data, dict) else []
    except Exception:
        pass

    if not raw_results:
        return jsonify({'matches': [], 'query': keywords,
                        'note': 'No Metaculus results returned'})

    kalshi_tok = _expand_tokens(combined)
    scored = []
    for m in raw_results:
        if m.get('resolution') not in (None, '', 'ambiguous'):
            continue
        title_q = (m.get('title') or '').strip()
        pm_tok  = _expand_tokens(title_q)
        if not pm_tok or not kalshi_tok:
            continue
        fwd   = len(kalshi_tok & pm_tok) / len(kalshi_tok)
        rev   = len(kalshi_tok & pm_tok) / len(pm_tok)
        score = round((fwd + rev) / 2, 4)
        if score < 0.05:
            continue
        cp   = m.get('community_prediction') or {}
        prob = cp.get('full', {}).get('q2') if isinstance(cp, dict) else None
        close_time = m.get('scheduled_resolve_time') or m.get('close_time') or ''
        close_str  = close_time[:10] if close_time else None
        qid  = m.get('id', '')
        scored.append({
            'score':       score,
            'question':    title_q,
            'url':         m.get('page_url') or f'https://www.metaculus.com/questions/{qid}/',
            'yes_pct':     round(prob * 100) if prob is not None else None,
            'volume':      m.get('number_of_forecasters'),
            'end_date':    close_str,
        })

    scored.sort(key=lambda x: x['score'], reverse=True)
    return jsonify({'matches': scored[:5], 'query': keywords})




# ─── Helpers ──────────────────────────────────────────────────────────────────

# ── Series/Category inference from series_ticker ────────────────────────────

def _serialize_markets(markets):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    out = []
    for m in markets:
        close_raw = m.get('close_time') or m.get('expiration_time') or ''
        days_left = None
        if close_raw:
            try:
                close_dt  = datetime.fromisoformat(close_raw.replace('Z', '+00:00'))
                days_left = round((close_dt - now).total_seconds() / 86400, 1)
            except Exception:
                pass
        raw_series = m.get('series_ticker') or ''
        out.append({
            'ticker':            m.get('ticker', ''),
            'title':             m.get('title', ''),
            'subtitle':          m.get('subtitle', ''),
            'category':          m.get('category', ''),
            'series_ticker':     raw_series,
            'event_ticker':      m.get('event_ticker', ''),
            'yes_ask':           m.get('yes_ask'),
            'yes_ask_dollars':   m.get('yes_ask_dollars'),
            'yes_bid':           m.get('yes_bid'),
            'no_ask':            m.get('no_ask'),
            'no_ask_dollars':    m.get('no_ask_dollars'),
            'last_price':        m.get('last_price'),
            'last_price_dollars': m.get('last_price_dollars'),
            'volume':            m.get('volume'),
            'close_time':        close_raw,
            'days_left':         days_left,
            'url':               f"https://kalshi.com/markets/{m.get('ticker', '')}",
            '_score':            m.get('_score'),
        })
    return out

# ─── Entry Point ──────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("🔴 Event Dashboard starting...")
    print("   Dashboard: http://localhost:5001/dashboard")
    app.run(debug=True, port=5001, use_reloader=False)