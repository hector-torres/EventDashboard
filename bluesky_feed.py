"""
Bluesky Feed Module
Handles all Bluesky AT Protocol API interactions.
Modular: swap out or extend feeds without touching other modules.

Authentication:
  Set BSKY_HANDLE and BSKY_PASSWORD as environment variables, or edit the
  constants below directly.
  The searchPosts endpoint requires a valid session token.

  Recommended: use a Bluesky App Password (Settings > Privacy > App Passwords)
  rather than your main account password.

Rate limits: ~3000 req/5min for authenticated endpoints (we poll every 30s = safe)
"""

import os
import requests
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional
from dotenv import load_dotenv

load_dotenv()

# ─── Credentials — loaded from .env file ─────────────────────────────────────
BSKY_HANDLE   = os.environ.get("BSKY_HANDLE",   "")  # e.g. yourhandle.bsky.social
BSKY_PASSWORD = os.environ.get("BSKY_PASSWORD", "")  # Use an App Password

# ─── Priority accounts file ───────────────────────────────────────────────────
ACCOUNTS_FILE = os.path.join(os.path.dirname(__file__), "accounts.txt")

# ─── Feed Configuration (add/remove feeds here) ───────────────────────────────
FEED_CONFIG = [
    {
        'id':      'breaking_news',
        'name':    'Breaking News',
        'type':    'search',
        'query':   'breaking news',
        'limit':   25,
        'enabled': True,
    },
    {
        'id':      'world_news',
        'name':    'World News',
        'type':    'search',
        'query':   'breaking world news',
        'limit':   20,
        'enabled': True,
    },
    {
        'id':      'urgent',
        'name':    'Urgent Reports',
        'type':    'search',
        'query':   'urgent developing story',
        'limit':   15,
        'enabled': True,
    },
]

BASE_URL              = "https://bsky.social/xrpc"
MAX_CACHED_POSTS      = 150
PRIORITY_POST_WEIGHT  = 3   # Priority account posts count this many times toward thresholds


class BlueSkyFeedManager:
    def __init__(self):
        self.active_feeds       = [f for f in FEED_CONFIG if f['enabled']]
        self._cache: List[Dict] = []
        self._seen_uris: set    = set()
        self.last_updated: Optional[str] = None
        self.status: str        = "initializing"
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        self.priority_handles: set = self._load_priority_accounts()

        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'EventDashboard/1.0 (news monitoring platform)',
            'Accept':     'application/json',
        })
        self._authenticate()

    # ── Priority accounts ──────────────────────────────────────────────────────

    def _load_priority_accounts(self) -> set:
        """Load priority handles from accounts.txt. Strips @ prefix and comments."""
        handles = set()
        if not os.path.exists(ACCOUNTS_FILE):
            print(f"[BlueSky] No accounts.txt found at {ACCOUNTS_FILE} — skipping priority accounts.")
            return handles
        with open(ACCOUNTS_FILE, 'r') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                handle = line.lstrip('@').lower()
                handles.add(handle)
        print(f"[BlueSky] Loaded {len(handles)} priority account(s) from accounts.txt")
        return handles

    def reload_priority_accounts(self):
        """Reload accounts.txt at runtime without restarting the server."""
        self.priority_handles = self._load_priority_accounts()
        return list(self.priority_handles)

    # ── Authentication ─────────────────────────────────────────────────────────

    def _authenticate(self):
        """Create a Bluesky session and store the Bearer token."""
        if not BSKY_HANDLE or not BSKY_PASSWORD:
            print("[BlueSky] No credentials found.")
            print("          Create a .env file in the project root with:")
            print("")
            print("            BSKY_HANDLE=yourhandle.bsky.social")
            print("            BSKY_PASSWORD=xxxx-xxxx-xxxx-xxxx")
            print("")
            print("          Recommended: use a Bluesky App Password, not your main password.")
            print("          (Bluesky > Settings > Privacy and Security > App Passwords)")
            self.status = "no_credentials"
            return

        try:
            resp = requests.post(
                f"{BASE_URL}/com.atproto.server.createSession",
                json={"identifier": BSKY_HANDLE, "password": BSKY_PASSWORD},
                headers={"Content-Type": "application/json"},
                timeout=10
            )
            resp.raise_for_status()
            data = resp.json()
            self._access_token = data.get("accessJwt")
            self.session.headers.update({"Authorization": f"Bearer {self._access_token}"})
            # Tokens expire after ~2h; schedule refresh 5 min early
            self._token_expiry = time.time() + 7200 - 300
            print(f"[BlueSky] Authenticated as @{data.get('handle')}")
            self.status = "live"
        except Exception as e:
            print(f"[BlueSky] Authentication failed: {e}")
            self.status = f"auth_error: {e}"

    def _ensure_token(self):
        """Re-authenticate if the token has expired."""
        if not self._access_token or time.time() >= self._token_expiry:
            print("[BlueSky] Token expired - re-authenticating...")
            self._authenticate()

    # ── Fetching ───────────────────────────────────────────────────────────────

    def fetch_latest(self, force: bool = False) -> List[Dict]:
        """Fetch posts from all active feeds + a dedicated getAuthorFeed call for
        every priority account. All fetches run concurrently so total time =
        slowest single request, not sum of all requests."""
        if self.status in ("no_credentials", "auth_error"):
            return []

        self._ensure_token()

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_search(feed):
            posts = self._fetch_feed(feed)
            return [(post, feed) for post in posts]

        def _fetch_account(handle):
            synthetic = {'id': f'priority_acct_{handle}', 'name': handle, 'type': 'account'}
            posts = self._get_author_feed(handle, limit=10)
            return [(post, synthetic) for post in posts]

        tasks = {}
        all_results = []  # list of (raw_post, feed)

        with ThreadPoolExecutor(max_workers=12) as pool:
            # Submit all search feeds
            for feed in self.active_feeds:
                f = pool.submit(_fetch_search, feed)
                tasks[f] = ('feed', feed['id'])
            # Submit all priority account feeds
            for handle in self.priority_handles:
                f = pool.submit(_fetch_account, handle)
                tasks[f] = ('account', handle)

            for future in as_completed(tasks):
                kind, name = tasks[future]
                try:
                    all_results.extend(future.result())
                except Exception as e:
                    print(f"[BlueSky] Error fetching {kind} '{name}': {e}")

        new_posts = []
        for raw_post, feed in all_results:
            uri = raw_post.get('uri', '')
            if uri and uri not in self._seen_uris:
                self._seen_uris.add(uri)
                new_posts.append(self._normalize_post(raw_post, feed))

        if new_posts:
            self._cache = (new_posts + self._cache)[:MAX_CACHED_POSTS]
            self.last_updated = datetime.now(timezone.utc).isoformat()
            self.status = "live"
            print(f"[BlueSky] Fetched {len(new_posts)} new posts. Cache: {len(self._cache)}")

        return new_posts

    def _fetch_feed(self, feed: Dict) -> List[Dict]:
        if feed['type'] == 'search':
            return self._search_posts(feed['query'], feed['limit'])
        if feed['type'] == 'account':
            return self._get_author_feed(feed['handle'], feed.get('limit', 20))
        return []

    def _get_author_feed(self, handle: str, limit: int = 20) -> List[Dict]:
        """Fetch recent posts from a specific account."""
        url    = f"{BASE_URL}/app.bsky.feed.getAuthorFeed"
        params = {'actor': handle, 'limit': min(limit, 100), 'filter': 'posts_no_replies'}
        resp   = self.session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        # getAuthorFeed returns feed items with a 'post' key, unwrap them
        return [item['post'] for item in resp.json().get('feed', []) if 'post' in item]

    def _search_posts(self, query: str, limit: int = 25) -> List[Dict]:
        """Search Bluesky posts by keyword (requires auth)."""
        url    = f"{BASE_URL}/app.bsky.feed.searchPosts"
        params = {'q': query, 'limit': min(limit, 100), 'sort': 'latest'}
        resp   = self.session.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return resp.json().get('posts', [])

    def _normalize_post(self, raw: Dict, feed: Dict) -> Dict:
        """Normalize a raw Bluesky post into a clean, consistent schema."""
        author  = raw.get('author', {})
        record  = raw.get('record', {})
        handle          = author.get('handle', 'unknown').lower()
        is_news_account = handle in self.priority_handles
        return {
            'uri':             raw.get('uri', ''),
            'cid':             raw.get('cid', ''),
            'feed_id':         feed['id'],
            'feed_name':       feed['name'],
            'author_handle':   handle,
            'author_display':  author.get('displayName', author.get('handle', 'Unknown')),
            'author_avatar':   author.get('avatar', ''),
            'text':            record.get('text', ''),
            'created_at':      record.get('createdAt', ''),
            'indexed_at':      raw.get('indexedAt', ''),
            'like_count':      raw.get('likeCount', 0),
            'repost_count':    raw.get('repostCount', 0),
            'reply_count':     raw.get('replyCount', 0),
            'url':             f"https://bsky.app/profile/{handle}/post/{raw.get('uri', '').split('/')[-1]}",
            'fetched_at':      datetime.now(timezone.utc).isoformat(),
            'is_news_account': is_news_account,
            'weight':          1,
        }

    # ── Cache access ───────────────────────────────────────────────────────────

    def get_cached_posts(self) -> List[Dict]:
        return self._cache

    def add_feed(self, feed_config: Dict) -> bool:
        if any(f['id'] == feed_config['id'] for f in self.active_feeds):
            return False
        self.active_feeds.append(feed_config)
        return True

    def remove_feed(self, feed_id: str) -> bool:
        before = len(self.active_feeds)
        self.active_feeds = [f for f in self.active_feeds if f['id'] != feed_id]
        return len(self.active_feeds) < before