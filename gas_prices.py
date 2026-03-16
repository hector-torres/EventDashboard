"""
gas_prices.py — AAA National Average Gas Prices scraper
Refreshes at 00:00, 08:00, 16:00 UTC (every ~8 hours).
Source: https://gasprices.aaa.com/
"""

import threading
import logging
import time
from datetime import datetime, timezone

try:
    import requests
    from bs4 import BeautifulSoup
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False

logger = logging.getLogger(__name__)

# UTC hours to refresh at
REFRESH_HOURS_UTC = {0, 8, 16}

_AAA_URL = 'https://gasprices.aaa.com/'
_HEADERS  = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}


class GasPricesManager:
    """
    Scrapes AAA national average gas prices.

    Data shape returned by get_data():
    {
        'status':   'ok' | 'error' | 'initializing',
        'as_of':    '3/16/26',
        'current':  3.718,   # Regular, current avg
        'yesterday':3.699,
        'week_ago': 3.478,
        'month_ago':2.929,
        'year_ago': 3.076,
        'direction':'up' | 'down' | 'flat',
        'grades': {
            'Regular':  {'current':3.718, 'yesterday':3.699, ...},
            'Mid-Grade':{'current':4.216, ...},
            'Premium':  {'current':4.585, ...},
            'Diesel':   {'current':4.988, ...},
        },
        'last_updated': '2026-03-16T00:00:00+00:00',
    }
    """

    def __init__(self):
        self._data        = {'status': 'initializing'}
        self._lock        = threading.Lock()
        self._last_hour   = -1   # which UTC hour we last fetched on
        self._thread      = None

    # ── Public API ────────────────────────────────────────────────────────────

    def get_data(self):
        with self._lock:
            return dict(self._data)

    def start(self):
        """Start background refresh thread."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name='gas-prices')
        self._thread.start()
        logger.info('[GasPrices] background thread started')

    def force_refresh(self):
        """Trigger an immediate fetch (e.g. for manual refresh)."""
        threading.Thread(target=self._fetch, daemon=True).start()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _loop(self):
        # Fetch immediately on startup
        self._fetch()
        while True:
            time.sleep(60)  # check every minute
            now_utc = datetime.now(timezone.utc)
            hour    = now_utc.hour
            # Trigger at the start of each refresh hour (only once per hour)
            if hour in REFRESH_HOURS_UTC and hour != self._last_hour:
                self._fetch()

    def _fetch(self):
        if not _DEPS_OK:
            with self._lock:
                self._data = {
                    'status': 'error',
                    'error':  'requests or BeautifulSoup not installed',
                }
            return

        try:
            resp = requests.get(_AAA_URL, headers=_HEADERS, timeout=15)
            resp.raise_for_status()
            data = self._parse(resp.text)
            with self._lock:
                self._data = data
                self._last_hour = datetime.now(timezone.utc).hour
            logger.info(f'[GasPrices] fetched OK — Regular ${data.get("current")} as of {data.get("as_of")}')
        except Exception as e:
            logger.warning(f'[GasPrices] fetch error: {e}')
            with self._lock:
                # Keep stale data if we have it; just mark error
                self._data = {**self._data, 'status': 'error', 'error': str(e)}

    def _parse(self, html):
        soup = BeautifulSoup(html, 'html.parser')

        # ── As-of date + direction from the big circle ─────────────────────
        as_of     = ''
        direction = 'flat'
        circle = soup.select_one('.average-price')
        if circle:
            span = circle.find('span')
            if span:
                as_of = span.get_text(' ', strip=True).replace('Price as of', '').strip()
            icon = circle.find('i', class_=lambda c: c and 'fa-caret' in c)
            if icon:
                direction = 'up' if 'up' in (icon.get('class') or []) else 'down'

        # ── National average table ──────────────────────────────────────────
        # Structure: first row = headers, subsequent rows = time periods
        table = soup.select_one('table.table-mob')
        grades   = {}
        row_map  = {}  # e.g. 'Current Avg.' → {'Regular': 3.718, ...}

        if table:
            rows = table.find_all('tr')
            headers = []
            for row in rows:
                cells = row.find_all(['th', 'td'])
                texts = [c.get_text(strip=True) for c in cells]
                if not texts:
                    continue
                if not headers:
                    # First row is the header
                    headers = texts  # ['', 'Regular', 'Mid-Grade', ...]
                    continue
                label = texts[0]  # e.g. 'Current Avg.'
                row_map[label] = {}
                for i, grade in enumerate(headers[1:], start=1):
                    if i < len(texts):
                        val = self._parse_price(texts[i])
                        row_map[label][grade] = val

            # Build per-grade dict
            for grade in headers[1:]:
                if grade:
                    grades[grade] = {
                        'current':   row_map.get('Current Avg.',   {}).get(grade),
                        'yesterday': row_map.get('Yesterday Avg.', {}).get(grade),
                        'week_ago':  row_map.get('Week Ago Avg.',  {}).get(grade),
                        'month_ago': row_map.get('Month Ago Avg.', {}).get(grade),
                        'year_ago':  row_map.get('Year Ago Avg.',  {}).get(grade),
                    }

        regular  = grades.get('Regular', {})
        current  = regular.get('current')
        yest     = regular.get('yesterday')

        # Determine direction from price comparison if circle didn't tell us
        if current is not None and yest is not None:
            if current > yest:   direction = 'up'
            elif current < yest: direction = 'down'
            else:                direction = 'flat'

        # Change vs yesterday
        change     = round(current - yest, 3) if current is not None and yest is not None else None
        change_pct = round((change / yest * 100), 2) if change is not None and yest else None

        return {
            'status':       'ok',
            'as_of':        as_of,
            'current':      current,
            'yesterday':    yest,
            'week_ago':     regular.get('week_ago'),
            'month_ago':    regular.get('month_ago'),
            'year_ago':     regular.get('year_ago'),
            'change':       change,
            'change_pct':   change_pct,
            'direction':    direction,
            'grades':       grades,
            'source':       'AAA gasprices.aaa.com',
            'last_updated': datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _parse_price(text):
        """Parse '$3.718' → 3.718, return None if invalid."""
        try:
            return float(text.replace('$', '').replace(',', '').strip())
        except (ValueError, AttributeError):
            return None