"""
measles_tracker.py — CDC US Measles Cases tracker
Scrapes the CDC measles data page weekly (Thursdays ~14:00 UTC, shortly after
CDC's noon Thursday update) and maintains a rolling 7-week history.
Source: https://www.cdc.gov/measles/data-research/index.html
"""

import json
import os
import re
import threading
import logging
import time
from datetime import datetime, timezone, timedelta

try:
    import requests
    _DEPS_OK = True
except ImportError:
    _DEPS_OK = False

logger = logging.getLogger(__name__)

_CDC_URL      = 'https://www.cdc.gov/measles/data-research/index.html'
_HISTORY_FILE = os.path.join(os.path.dirname(__file__), 'data', 'measles_history.json')
_HISTORY_WEEKS = 7    # rolling window

# CDC updates at noon Thursdays — we refresh at 14:00 UTC Thursday to be safe.
_REFRESH_WEEKDAY = 3  # Thursday (0=Mon)
_REFRESH_HOUR    = 14

_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/122.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
}

# Patterns to extract cumulative YTD case count from CDC prose text.
# CDC writes: "As of [date], X confirmed* measles cases were reported in the United States in [year]."
_CASE_PATTERNS = [
    re.compile(
        r'As of [^,]+,\s*([\d,]+)\s+confirmed\*?\s+measles cases?\s+were reported.*?in the United States in (\d{4})',
        re.IGNORECASE
    ),
    re.compile(
        r'([\d,]+)\s+confirmed\*?\s+measles cases?\s+(?:have been|were)\s+reported.*?United States',
        re.IGNORECASE
    ),
    re.compile(
        r'total of\s+([\d,]+)\s+confirmed\*?\s+measles cases?\s+were reported.*?in the United States in (\d{4})',
        re.IGNORECASE
    ),
]

# Full-year final count pattern (for prior year reference)
_FULL_YEAR_PATTERN = re.compile(
    r'full year of (\d{4}),\s+a total of\s+([\d,]+)\s+confirmed\*?\s+measles cases?',
    re.IGNORECASE
)


class MeaslesTracker:
    """
    Scrapes CDC measles data page weekly. Provides:
      - current YTD cumulative case count
      - week-over-week change
      - rolling 7-week history for sparkline
    """

    def __init__(self):
        self._data    = {'status': 'initializing'}
        self._lock    = threading.Lock()
        self._history = self._load_history()
        self._thread  = None
        self._last_fetch_week = -1  # ISO week number of last successful fetch

    # ── Public API ────────────────────────────────────────────────────────────

    def get_data(self):
        """Return current measles data dict."""
        with self._lock:
            return dict(self._data)

    def get_history(self):
        """Return rolling weekly history list [{week, cases}, ...], oldest first."""
        with self._lock:
            return list(self._history)

    def start(self):
        """Start background weekly refresh thread."""
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name='measles-tracker'
        )
        self._thread.start()
        logger.info('[Measles] background thread started')

    def force_refresh(self):
        """Trigger an immediate fetch."""
        threading.Thread(target=self._fetch, daemon=True).start()

    # ── Internal ──────────────────────────────────────────────────────────────

    def _loop(self):
        # Fetch immediately on startup (builds initial data from history + live)
        self._fetch()
        while True:
            time.sleep(3600)   # check every hour
            now_utc  = datetime.now(timezone.utc)
            weekday  = now_utc.weekday()
            hour     = now_utc.hour
            iso_week = now_utc.isocalendar()[1]
            # Refresh on Thursday at 14:00 UTC, once per week
            if (weekday == _REFRESH_WEEKDAY
                    and hour >= _REFRESH_HOUR
                    and iso_week != self._last_fetch_week):
                self._fetch()

    def _fetch(self):
        if not _DEPS_OK:
            with self._lock:
                self._data = {'status': 'error', 'error': 'requests not installed'}
            return

        try:
            resp = requests.get(_CDC_URL, headers=_HEADERS, timeout=20)
            resp.raise_for_status()
            data = self._parse(resp.text)
            now  = datetime.now(timezone.utc)

            with self._lock:
                self._data = data
                self._last_fetch_week = now.isocalendar()[1]
                # Record weekly snapshot keyed by ISO week
                if data.get('cases') is not None:
                    week_key = f"{now.year}-W{now.isocalendar()[1]:02d}"
                    self._append_history(data['cases'], week_key, now.date().isoformat())

            logger.info(
                f"[Measles] fetched OK — {data.get('cases')} YTD cases "
                f"(+{data.get('change', 0)} wow) as of {data.get('as_of')}"
            )
        except Exception as e:
            logger.warning(f'[Measles] fetch error: {e}')
            with self._lock:
                self._data = {**self._data, 'status': 'error', 'error': str(e)}

    def _parse(self, html):
        """Extract YTD cumulative case count from CDC prose text."""
        now_year = datetime.now(timezone.utc).year
        cases    = None
        as_of    = None

        # Try to find "As of [date], X confirmed measles cases were reported ... in [year]"
        for pattern in _CASE_PATTERNS:
            m = pattern.search(html)
            if m:
                try:
                    raw = m.group(1).replace(',', '')
                    cases = int(raw)
                    break
                except (IndexError, ValueError):
                    continue

        # Extract as_of date from "As of [Month DD, YYYY]" pattern
        as_of_m = re.search(r'As of ([A-Z][a-z]+ \d+,\s*\d{4})', html)
        if as_of_m:
            as_of = as_of_m.group(1).strip()

        # Prior year reference for context
        prior_year = None
        prior_cases = None
        fy_m = _FULL_YEAR_PATTERN.search(html)
        if fy_m:
            try:
                prior_year  = int(fy_m.group(1))
                prior_cases = int(fy_m.group(2).replace(',', ''))
            except (IndexError, ValueError):
                pass

        # Week-over-week change from history
        change = None
        direction = 'flat'
        prev = self._history[-1]['cases'] if self._history else None
        if cases is not None and prev is not None:
            change = cases - prev
            direction = 'up' if change > 0 else ('down' if change < 0 else 'flat')
        elif cases is not None and prev is None:
            change = 0
            direction = 'flat'

        return {
            'status':     'ok',
            'cases':      cases,        # cumulative YTD
            'as_of':      as_of,
            'change':     change,       # wow new cases (None if first fetch)
            'change_pct': round(change / prev * 100, 2) if (change is not None and prev and prev > 0) else None,
            'direction':  direction,
            'year':       now_year,
            'prior_year': prior_year,
            'prior_cases': prior_cases,
            'source':     'CDC measles data-research page',
            'last_updated': datetime.now(timezone.utc).isoformat(),
        }

    def _load_history(self):
        """Load rolling weekly history from disk."""
        try:
            if os.path.exists(_HISTORY_FILE):
                with open(_HISTORY_FILE) as fh:
                    data = json.load(fh)
                # Keep last _HISTORY_WEEKS entries
                return sorted(data, key=lambda e: e.get('week', ''))[-_HISTORY_WEEKS:]
        except Exception as e:
            logger.warning(f'[Measles] Failed to load history: {e}')
        return []

    def _append_history(self, cases, week_key, date_str):
        """Append this week's count. Deduplicates by week key."""
        try:
            os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)
            self._history = [e for e in self._history if e.get('week') != week_key]
            self._history.append({'week': week_key, 'date': date_str, 'cases': cases})
            self._history = sorted(
                self._history, key=lambda e: e.get('week', '')
            )[-_HISTORY_WEEKS:]
            with open(_HISTORY_FILE, 'w') as fh:
                json.dump(self._history, fh, indent=2)
        except Exception as e:
            logger.warning(f'[Measles] Failed to save history: {e}')