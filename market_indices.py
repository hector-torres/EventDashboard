"""
Market Indices Module
Fetches major stock market index data via yfinance (Yahoo Finance).
No API key required. Data is delayed ~15 minutes per Yahoo Finance terms.

Indices tracked (configurable in INDICES_CONFIG):
  ^GSPC   — S&P 500
  ^IXIC   — NASDAQ Composite
  ^VIX    — CBOE Volatility Index
  ^GDAXI  — DAX (Germany)
  ^FTSE   — FTSE 100 (UK)
  ^FCHI   — CAC 40 (France)

Futures (used when primary market is closed):
  ES=F    — S&P 500 Futures
  NQ=F    — NASDAQ Futures
  DAX=F   — DAX Futures (if available)
"""

import yfinance as yf
from datetime import datetime, timezone, time as dtime
import pytz
from typing import Dict, List, Optional

# ─── Index Configuration ──────────────────────────────────────────────────────
# ── Row assignment ─────────────────────────────────────────────────────────────
# row=1  →  main equity indices bar
# row=2  →  volatility + commodities + crypto bar
INDICES_CONFIG = [
    # ── Row 1: Major equity indices ───────────────────────────────────────────
    {
        'id':          'sp500',
        'label':       'S&P 500',
        'symbol':      '^GSPC',
        'futures':     'ES=F',
        'currency':    'USD',
        'exchange_tz': 'America/New_York',
        'open_time':   dtime(9, 30),
        'close_time':  dtime(16, 0),
        'market_days': [0, 1, 2, 3, 4],
        'row':         1,
        'tooltip':     'NYSE/NASDAQ · Mon–Fri 9:30–16:00 ET · After-hours futures via ES=F (CME Globex, ~23h/day)',
    },
    {
        'id':          'nasdaq',
        'label':       'NASDAQ',
        'symbol':      '^IXIC',
        'futures':     'NQ=F',
        'currency':    'USD',
        'exchange_tz': 'America/New_York',
        'open_time':   dtime(9, 30),
        'close_time':  dtime(16, 0),
        'market_days': [0, 1, 2, 3, 4],
        'row':         1,
        'tooltip':     'NASDAQ · Mon–Fri 9:30–16:00 ET · After-hours futures via NQ=F (CME Globex, ~23h/day)',
    },
    {
        'id':          'dow',
        'label':       'DOW',
        'symbol':      '^DJI',
        'futures':     'YM=F',
        'currency':    'USD',
        'exchange_tz': 'America/New_York',
        'open_time':   dtime(9, 30),
        'close_time':  dtime(16, 0),
        'market_days': [0, 1, 2, 3, 4],
        'row':         1,
        'tooltip':     'NYSE · Mon–Fri 9:30–16:00 ET · After-hours futures via YM=F (Mini Dow, CME Globex)',
    },
    {
        'id':          'dax',
        'label':       'DAX',
        'symbol':      '^GDAXI',
        'futures':     None,
        'currency':    'EUR',
        'exchange_tz': 'Europe/Berlin',
        'open_time':   dtime(9, 0),
        'close_time':  dtime(17, 30),
        'market_days': [0, 1, 2, 3, 4],
        'row':         1,
        'tooltip':     'Xetra (Frankfurt) · Mon–Fri 09:00–17:30 CET · No futures via Yahoo Finance',
    },
    {
        'id':          'ftse',
        'label':       'FTSE 100',
        'symbol':      '^FTSE',
        'futures':     None,
        'currency':    'GBP',
        'exchange_tz': 'Europe/London',
        'open_time':   dtime(8, 0),
        'close_time':  dtime(16, 30),
        'market_days': [0, 1, 2, 3, 4],
        'row':         1,
        'tooltip':     'London Stock Exchange · Mon–Fri 08:00–16:30 GMT/BST · No futures via Yahoo Finance',
    },
    {
        'id':          'cac',
        'label':       'CAC 40',
        'symbol':      '^FCHI',
        'futures':     None,
        'currency':    'EUR',
        'exchange_tz': 'Europe/Paris',
        'open_time':   dtime(9, 0),
        'close_time':  dtime(17, 30),
        'market_days': [0, 1, 2, 3, 4],
        'row':         1,
        'tooltip':     'Euronext Paris · Mon–Fri 09:00–17:30 CET · No futures via Yahoo Finance',
    },
    # ── Row 2: Volatility, commodities, crypto ────────────────────────────────
    {
        'id':          'vix',
        'label':       'VIX',
        'symbol':      '^VIX',
        'futures':     None,
        'currency':    'USD',
        'exchange_tz': 'America/New_York',
        'open_time':   dtime(9, 30),
        'close_time':  dtime(16, 15),
        'market_days': [0, 1, 2, 3, 4],
        'row':         2,
        'tooltip':     'CBOE Volatility Index · Tracks S&P 500 implied volatility · Mon–Fri 9:30–16:15 ET · Not directly tradeable; no futures on Yahoo Finance',
    },
    {
        # CME Globex energy futures trade ~23h/day Mon–Fri
        # (Sun 18:00 ET open; daily maintenance break ~17:00–18:00 ET)
        'id':             'brent',
        'label':          'Brent Crude',
        'symbol':         'BZ=F',
        'futures':        None,
        'currency':       'USD',
        'exchange_tz':    'America/New_York',
        'open_time':      dtime(18, 0),
        'close_time':     dtime(17, 0),
        'market_days':    [0, 1, 2, 3, 4, 6],
        'row':            2,
        'always_futures': True,
        'hide_countdown': True,
        'tooltip':        'ICE Brent Crude futures (BZ=F) · CME Globex · ~23h/day Sun 18:00 – Fri 17:00 ET · Daily maintenance break ~17:00–18:00 ET · 15-min delayed (Yahoo Finance)',
    },
    {
        'id':             'wti',
        'label':          'WTI Crude',
        'symbol':         'CL=F',
        'futures':        None,
        'currency':       'USD',
        'exchange_tz':    'America/New_York',
        'open_time':      dtime(18, 0),
        'close_time':     dtime(17, 0),
        'market_days':    [0, 1, 2, 3, 4, 6],
        'row':            2,
        'always_futures': True,
        'hide_countdown': True,
        'tooltip':        'WTI Crude Oil futures (CL=F) · CME Globex (NYMEX) · ~23h/day Sun 18:00 – Fri 17:00 ET · Daily maintenance break ~17:00–18:00 ET · 15-min delayed (Yahoo Finance)',
    },
    {
        'id':             'natgas',
        'label':          'Nat Gas',
        'symbol':         'NG=F',
        'futures':        None,
        'currency':       'USD',
        'exchange_tz':    'America/New_York',
        'open_time':      dtime(18, 0),
        'close_time':     dtime(17, 0),
        'market_days':    [0, 1, 2, 3, 4, 6],
        'row':            2,
        'always_futures': True,
        'hide_countdown': True,
        'tooltip':        'Henry Hub Natural Gas futures (NG=F) · CME Globex (NYMEX) · ~23h/day Sun 18:00 – Fri 17:00 ET · Daily maintenance break ~17:00–18:00 ET · 15-min delayed (Yahoo Finance)',
    },
    {
        'id':             'gold',
        'label':          'Gold',
        'symbol':         'GC=F',
        'futures':        None,
        'currency':       'USD',
        'exchange_tz':    'America/New_York',
        'open_time':      dtime(18, 0),
        'close_time':     dtime(17, 0),
        'market_days':    [0, 1, 2, 3, 4, 6],
        'row':            2,
        'always_futures': True,
        'hide_countdown': True,
        'tooltip':        'Gold futures (GC=F) · CME Globex (COMEX) · ~23h/day Sun 18:00 – Fri 17:00 ET · Daily maintenance break ~17:00–18:00 ET · 15-min delayed (Yahoo Finance)',
    },
    {
        'id':             'btc',
        'label':          'BTC-USD',
        'symbol':         'BTC-USD',
        'futures':        None,
        'currency':       'USD',
        'exchange_tz':    'America/New_York',
        'open_time':      dtime(0, 0),
        'close_time':     dtime(23, 59),
        'market_days':    [0, 1, 2, 3, 4, 5, 6],
        'row':            2,
        'always_open':    True,
        'hide_countdown': True,   # trades 24/7 — no meaningful close time
        'tooltip':        'Bitcoin spot price (BTC-USD) · Trades 24/7 globally · No close time · 15-min delayed (Yahoo Finance)',
    },
]

CURRENCY_SYMBOLS = {'USD': '$', 'EUR': '€', 'GBP': '£'}


class MarketIndicesManager:
    def __init__(self):
        self._cache: Dict = {}
        self.last_updated: Optional[str] = None
        self.status: str = "initializing"

    # ── Market hours helpers ───────────────────────────────────────────────────

    def _is_market_open(self, cfg: Dict) -> bool:
        """Check if this market is currently open."""
        tz  = pytz.timezone(cfg['exchange_tz'])
        now = datetime.now(tz)
        if now.weekday() not in cfg['market_days']:
            return False
        t = now.time().replace(second=0, microsecond=0)
        return cfg['open_time'] <= t < cfg['close_time']

    def _time_to_close(self, cfg: Dict) -> Optional[Dict]:
        """Return timing info until market close, or None if market is closed."""
        if not self._is_market_open(cfg):
            return None
        tz    = pytz.timezone(cfg['exchange_tz'])
        now   = datetime.now(tz)
        close = now.replace(
            hour=cfg['close_time'].hour,
            minute=cfg['close_time'].minute,
            second=0, microsecond=0
        )
        delta = close - now
        secs  = int(delta.total_seconds())
        if secs <= 0:
            return None
        hours, rem = divmod(secs, 3600)
        mins        = rem // 60
        label = f"{hours}h {mins:02d}m" if hours else f"{mins}m"
        return {'label': label, 'seconds': secs}

    def _next_open(self, cfg: Dict) -> Optional[Dict]:
        """Return timing info for the next market open.
        Checks today first (if open time hasn't passed yet), then scans forward."""
        from datetime import timedelta
        tz        = pytz.timezone(cfg['exchange_tz'])
        now       = datetime.now(tz)
        tz_abbr   = now.strftime('%Z')   # e.g. "EST", "CET", "GMT"
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

        # Check today first — if it's a market day and open time is still ahead
        today_open = now.replace(
            hour=cfg['open_time'].hour,
            minute=cfg['open_time'].minute,
            second=0, microsecond=0
        )
        if now.weekday() in cfg['market_days'] and today_open > now:
            secs  = int((today_open - now).total_seconds())
            label = f"Today {cfg['open_time'].strftime('%H:%M')} {tz_abbr}"
            return {'label': label, 'seconds': secs}

        # Otherwise scan forward up to 7 days
        for i in range(1, 8):
            candidate = (now + timedelta(days=i)).replace(
                hour=cfg['open_time'].hour,
                minute=cfg['open_time'].minute,
                second=0, microsecond=0
            )
            if candidate.weekday() in cfg['market_days']:
                secs  = int((candidate - now).total_seconds())
                label = f"{day_names[candidate.weekday()]} {cfg['open_time'].strftime('%H:%M')} {tz_abbr}"
                return {'label': label, 'seconds': secs}
        return None

    # ── Fetching ───────────────────────────────────────────────────────────────

    def fetch_all(self) -> List[Dict]:
        """Fetch all configured indices. Returns list of normalized index dicts."""
        results = []
        for cfg in INDICES_CONFIG:
            try:
                data = self._fetch_index(cfg)
                if data:
                    results.append(data)
                    self._cache[cfg['id']] = data
            except Exception as e:
                print(f"[Markets] Error fetching {cfg['label']}: {e}")
                # Return stale cache if available
                if cfg['id'] in self._cache:
                    results.append(self._cache[cfg['id']])

        self.last_updated = datetime.now(timezone.utc).isoformat()
        self.status = "live"
        print(f"[Markets] Fetched {len(results)} indices.")
        return results

    def _fetch_index(self, cfg: Dict) -> Optional[Dict]:
        """Fetch a single index from Yahoo Finance."""
        always_open    = cfg.get('always_open', False)
        always_futures = cfg.get('always_futures', False)
        hide_countdown = cfg.get('hide_countdown', False)
        is_open  = always_open or self._is_market_open(cfg)
        symbol   = cfg['symbol']
        currency = CURRENCY_SYMBOLS.get(cfg['currency'], '')

        ticker = yf.Ticker(symbol)
        info   = ticker.info

        price     = info.get('regularMarketPrice') or info.get('previousClose')
        prev_close = info.get('previousClose') or info.get('regularMarketPreviousClose')
        open_price = info.get('regularMarketOpen') or info.get('open')
        day_high   = info.get('regularMarketDayHigh') or info.get('dayHigh')
        day_low    = info.get('regularMarketDayLow')  or info.get('dayLow')

        if price is None:
            return None

        change     = price - prev_close if prev_close else 0
        change_pct = (change / prev_close * 100) if prev_close else 0
        direction  = 'up' if change >= 0 else 'down'

        # Fetch 1-day intraday history for sparkline (5-min bars)
        sparkline = []
        sparkline_open_frac = None   # 0.0–1.0: where "market open" falls on x-axis
        try:
            hist = ticker.history(period='1d', interval='5m')
            if not hist.empty:
                closes = hist['Close'].dropna()
                timestamps = closes.index.tolist()
                closes_list = closes.tolist()

                # Compute open_frac: for always_open (BTC etc.) use midnight local,
                # for regular markets use the configured open_time.
                if always_open:
                    # Find index of first bar at/after local midnight UTC
                    import pandas as pd
                    tz_obj = pytz.timezone(cfg['exchange_tz'])
                    midnight = datetime.now(tz_obj).replace(hour=0, minute=0, second=0, microsecond=0)
                    midnight_utc = midnight.astimezone(timezone.utc)
                    total = len(timestamps)
                    if total > 1:
                        t0 = timestamps[0].to_pydatetime() if hasattr(timestamps[0], 'to_pydatetime') else timestamps[0]
                        te = timestamps[-1].to_pydatetime() if hasattr(timestamps[-1], 'to_pydatetime') else timestamps[-1]
                        if t0.tzinfo is None:
                            import pandas as pd
                            t0 = t0.replace(tzinfo=timezone.utc)
                            te = te.replace(tzinfo=timezone.utc)
                        span = (te - t0).total_seconds()
                        if span > 0:
                            offset = (midnight_utc - t0).total_seconds()
                            sparkline_open_frac = max(0.0, min(1.0, offset / span))
                elif not always_futures:
                    # Regular session: mark where open_time falls in today's data
                    tz_obj = pytz.timezone(cfg['exchange_tz'])
                    today = datetime.now(tz_obj).date()
                    open_dt = tz_obj.localize(datetime.combine(today, cfg['open_time']))
                    open_utc = open_dt.astimezone(timezone.utc)
                    total = len(timestamps)
                    if total > 1:
                        t0 = timestamps[0].to_pydatetime() if hasattr(timestamps[0], 'to_pydatetime') else timestamps[0]
                        te = timestamps[-1].to_pydatetime() if hasattr(timestamps[-1], 'to_pydatetime') else timestamps[-1]
                        if t0.tzinfo is None:
                            t0 = t0.replace(tzinfo=timezone.utc)
                            te = te.replace(tzinfo=timezone.utc)
                        span = (te - t0).total_seconds()
                        if span > 0:
                            offset = (open_utc - t0).total_seconds()
                            if 0.02 < offset / span < 0.98:
                                sparkline_open_frac = offset / span

                # Downsample to at most 40 points for compact SVG
                step = max(1, len(closes_list) // 40)
                sparkline = [round(v, 4) for v in closes_list[::step]]
        except Exception as e:
            pass

        # Fetch futures if market is closed
        futures_data = None
        if not is_open and cfg.get('futures'):
            futures_data = self._fetch_futures(cfg['futures'], currency)

        time_to_close = self._time_to_close(cfg) if is_open else None
        next_open     = self._next_open(cfg) if not is_open else None

        return {
            'id':           cfg['id'],
            'label':        cfg['label'],
            'row':          cfg.get('row', 1),
            'tooltip':      cfg.get('tooltip', ''),
            'symbol':       symbol,
            'currency':     cfg['currency'],
            'currency_sym': currency,
            'price':        price,
            'open':         open_price,
            'prev_close':   prev_close,
            'day_high':     day_high,
            'day_low':      day_low,
            'change':       change,
            'change_pct':   change_pct,
            'direction':    direction,
            'is_open':      is_open,
            'always_futures': always_futures,
            'always_open':    always_open,
            'hide_countdown': hide_countdown,
            'time_to_close': None if hide_countdown else time_to_close,
            'next_open':     None if hide_countdown else next_open,
            'futures':      futures_data,
            'fetched_at':   datetime.now(timezone.utc).isoformat(),
            'sparkline':      sparkline,
            'sparkline_open_frac': sparkline_open_frac,
        }

    def _fetch_futures(self, symbol: str, currency_sym: str) -> Optional[Dict]:
        """Fetch futures data for a given symbol."""
        try:
            ticker = yf.Ticker(symbol)
            info   = ticker.info
            price  = info.get('regularMarketPrice') or info.get('previousClose')
            prev   = info.get('previousClose') or info.get('regularMarketPreviousClose')
            if price is None:
                return None
            change     = price - prev if prev else 0
            change_pct = (change / prev * 100) if prev else 0
            return {
                'symbol':     symbol,
                'price':      price,
                'change':     change,
                'change_pct': change_pct,
                'direction':  'up' if change >= 0 else 'down',
            }
        except Exception:
            return None

    def get_cached(self) -> List[Dict]:
        return list(self._cache.values())