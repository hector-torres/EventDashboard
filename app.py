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
        markets = [
            m for m in markets
            if search_query in (m.get('title') or '').lower()
            or search_query in (m.get('subtitle') or '').lower()
            or search_query in (m.get('ticker') or '').lower()
        ]

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

@app.route('/match_detail')
def match_detail_page():
    """Standalone page showing why a market matched semantically."""
    return send_from_directory('.', 'match_detail.html')

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