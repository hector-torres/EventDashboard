"""
post_scorer.py — Search Feed noise scoring for Bluesky posts
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Assigns a noise score to Search Feed posts (is_news_account=False).
Priority-account posts are never scored (they bypass this entirely).

Score buckets:
    0–2  → CLEAN  — show normally
    3–4  → DIM    — show with reduced opacity in UI
    5+   → HIDE   — filtered from Search Feed (still used by event detector)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Active filters (v1.2):

  F1  Content length        — < 20 chars          → +5
  F2  Account age           — < 3 days → +5  | < 7 days → +2
  F3  Hashtag/tag count     — > 3 tags  → +4  | == 3     → +2
  F4  Engagement proxy      — 0 total engagement  → +1  | ≥3 → -1 bonus
  F5  Solicitation phrases  — follow/repost farming → +5
  F6  Reply detection       — post is a reply      → +3
  F7  Language              — non-English langs[]  → +3
  F11 URL-only posts        — nearly all URL, < 3 real words → +4
  F12 Excessive mentions    — > 3 @mentions         → +3
  F13 Repeated handle       — same handle 3+ times in batch → +2/+3

Planned filters (future — require getProfile API call per handle):
  F8  Follower count        — < 10 → +4  |  < 50 → +2
  F9  Post count            — < 50 → +4  |  < 100 → +2
  F10 Bio description       — missing → +5  |  < 20 chars → +2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
EXTENDING:

To add a new filter, subclass NoiseFilter and register it in PostScorer:

    class MyFilter(NoiseFilter):
        id    = 'my_filter'
        label = 'Human-readable label'

        def score(self, post: dict) -> tuple[int, str | None]:
            # Return (points, reason_string) or (0, None)
            ...

    scorer = PostScorer()
    scorer.register(MyFilter())

To add a profile-data filter (F8–F10), call scorer.update_profile_cache(handle, profile)
from a background thread after fetching getProfile. The scorer will use it automatically.
"""

import re
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Score thresholds ───────────────────────────────────────────────────────────
SCORE_CLEAN = 0    # 0–2: show normally
SCORE_DIM   = 3    # 3–4: dim in UI (opacity reduced)
SCORE_HIDE  = 5    # 5+:  filter from Search Feed

# ── Solicitation phrases ───────────────────────────────────────────────────────
_SOLICITATION_PHRASES = frozenset([
    'repost and follow', 'repost & follow',
    'rp and follow',     'rp & follow',
    'follow for follow', 'follow back',
    'f4f', 'l4l',        'follow me back',
    'rt and follow',     'rt & follow',
    'mass follow',       'follow everyone',
    'gain followers',
])

# ── URL-only stripping vocabulary ─────────────────────────────────────────────
_URL_STRIP_WORDS = re.compile(
    r'\b(rt|via|from|read|more|here|link|source|article|thread)\b',
    re.IGNORECASE,
)


# ══════════════════════════════════════════════════════════════════════════════
# BASE FILTER
# ══════════════════════════════════════════════════════════════════════════════

class NoiseFilter:
    """
    Base class for all noise filters.
    Subclass and implement score(post) → (points, reason | None).
    Register with PostScorer.register().
    """
    id:      str  = 'base'
    label:   str  = 'Base filter'
    enabled: bool = True

    def score(self, post: dict) -> tuple:
        """
        Evaluate the post and return (points, reason).
        Return (0, None) if this filter does not apply.
        reason is a short string stored in noise_reasons for debugging.
        """
        return 0, None


# ══════════════════════════════════════════════════════════════════════════════
# ACTIVE FILTERS  (F1–F7, F11–F13 — no extra API calls)
# ══════════════════════════════════════════════════════════════════════════════

class ContentLengthFilter(NoiseFilter):
    """F1 — Very short posts have minimal news value."""
    id    = 'content_length'
    label = 'Content length'
    MIN_CHARS = 20

    def score(self, post):
        text = post.get('text', '')
        if len(text.strip()) < self.MIN_CHARS:
            return 5, f'short_text({len(text.strip())})'
        return 0, None


class AccountAgeFilter(NoiseFilter):
    """F2 — Very new accounts are a common botnet signal."""
    id    = 'account_age'
    label = 'Account age'

    def score(self, post):
        created = post.get('author_created_at', '')
        if not created:
            return 0, None
        try:
            dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
            age_days = (datetime.now(timezone.utc) - dt).days
            if age_days < 3:
                return 5, f'new_account({age_days}d)'
            if age_days < 7:
                return 2, f'young_account({age_days}d)'
        except Exception:
            pass
        return 0, None


class TagCountFilter(NoiseFilter):
    """F3 — Excessive hashtags indicate tag-stuffing spam."""
    id    = 'tag_count'
    label = 'Hashtag count'

    def score(self, post):
        n = post.get('tag_count', 0)
        if n > 3:
            return 4, f'tag_spam({n})'
        if n == 3:
            return 2, 'many_tags'
        return 0, None


class EngagementFilter(NoiseFilter):
    """
    F4 — Zero engagement is a weak signal, meaningful only in combination.
    Real engagement (≥3) gives a small bonus.
    """
    id    = 'engagement'
    label = 'Engagement proxy'

    def score(self, post):
        total = (post.get('like_count', 0) +
                 post.get('repost_count', 0) +
                 post.get('reply_count', 0))
        if total == 0:
            return 1, 'zero_engagement'
        if total >= 3:
            return -1, 'engaged'
        return 0, None


class SolicitationFilter(NoiseFilter):
    """F5 — Posts asking for reposts/follows are bots or noise."""
    id    = 'solicitation'
    label = 'Solicitation phrases'

    def score(self, post):
        tl = post.get('text', '').lower()
        for phrase in _SOLICITATION_PHRASES:
            if phrase in tl:
                return 5, 'solicitation'
        return 0, None


class ReplyFilter(NoiseFilter):
    """
    F6 — Replies in keyword search results are almost always noise.
    Someone replying 'breaking!' in a thread has no broadcast value.
    """
    id    = 'is_reply'
    label = 'Reply detection'

    def score(self, post):
        if post.get('is_reply', False):
            return 3, 'is_reply'
        return 0, None


class LanguageFilter(NoiseFilter):
    """
    F7 — Non-English posts are off-topic for this use case.
    Dims rather than hides — may contain real news worth preserving
    for the event detector even if not shown in the UI.
    """
    id    = 'language'
    label = 'Language'

    def score(self, post):
        langs = post.get('langs', [])
        if not langs:
            return 0, None   # no lang data = assume English
        if any(l.startswith('en') for l in langs):
            return 0, None
        return 3, f'non_english({",".join(langs[:2])})'


class UrlOnlyFilter(NoiseFilter):
    """
    F11 — Posts that are essentially just a URL with no meaningful context.
    These are typically link-bot posts or empty shares.
    Requires fewer than 3 meaningful words remaining after stripping
    the URL, @mentions, and common filler words (RT, via, read more, etc.).
    """
    id    = 'url_only'
    label = 'URL-only post'

    def score(self, post):
        text = post.get('text', '').strip()
        if not text or 'http' not in text:
            return 0, None
        # Strip URLs, mentions, filler words, then check remaining content
        remainder = re.sub(r'https?://\S+', '', text)
        remainder = re.sub(r'@\w+', '', remainder)
        remainder = _URL_STRIP_WORDS.sub('', remainder)
        remainder = re.sub(r'[^\w\s]', ' ', remainder)
        real_words = [w for w in remainder.split() if len(w) >= 3]
        if len(real_words) < 3:
            return 4, f'url_only({len(real_words)}words)'
        return 0, None


class MentionCountFilter(NoiseFilter):
    """
    F12 — Excessive @mentions are a hallmark of spam and engagement farming.
    Normal news posts mention 0–2 accounts at most.
    """
    id    = 'mention_count'
    label = 'Excessive mentions'

    def score(self, post):
        n = post.get('mention_count', 0)
        if n > 5:
            return 4, f'mention_spam({n})'
        if n > 3:
            return 2, f'many_mentions({n})'
        return 0, None


class RepeatedHandleFilter(NoiseFilter):
    """
    F13 — The same unknown account appearing many times in one search batch
    is likely a high-volume bot or aggressive self-promoter.
    This is a batch-level filter: PostScorer.score_batch() injects
    a '_batch_handle_count' field before calling per-post scoring.
    """
    id    = 'repeated_handle'
    label = 'Repeated handle in batch'

    def score(self, post):
        count = post.get('_batch_handle_count', 1)
        if count >= 4:
            return 3, f'handle_repeat({count}x)'
        if count >= 3:
            return 2, f'handle_repeat({count}x)'
        return 0, None


# ══════════════════════════════════════════════════════════════════════════════
# PLANNED FILTERS  (F8–F10, require profile cache)
# ══════════════════════════════════════════════════════════════════════════════

class FollowerCountFilter(NoiseFilter):
    """
    F8 — Low follower count suggests spam or very new account.
    Requires profile data from getProfile API (cached per handle).
    DISABLED until profile cache is implemented.
    """
    id      = 'follower_count'
    label   = 'Follower count'
    enabled = False

    def __init__(self, profile_cache: dict = None):
        self._cache = profile_cache or {}

    def score(self, post):
        if not self.enabled:
            return 0, None
        profile = self._cache.get(post.get('author_handle', ''), {})
        followers = profile.get('followersCount')
        if followers is None:
            return 0, None
        if followers < 10:
            return 4, f'few_followers({followers})'
        if followers < 50:
            return 2, f'low_followers({followers})'
        return 0, None


class PostCountFilter(NoiseFilter):
    """
    F9 — Very low post count suggests a throwaway or bot account.
    Requires profile data from getProfile API.
    DISABLED until profile cache is implemented.
    """
    id      = 'post_count'
    label   = 'Post count'
    enabled = False

    def __init__(self, profile_cache: dict = None):
        self._cache = profile_cache or {}

    def score(self, post):
        if not self.enabled:
            return 0, None
        profile = self._cache.get(post.get('author_handle', ''), {})
        posts_count = profile.get('postsCount')
        if posts_count is None:
            return 0, None
        if posts_count < 50:
            return 4, f'few_posts({posts_count})'
        if posts_count < 100:
            return 2, f'low_posts({posts_count})'
        return 0, None


class BioFilter(NoiseFilter):
    """
    F10 — Missing or very short bio is a spam signal.
    Requires profile data from getProfile API.
    DISABLED until profile cache is implemented.
    """
    id      = 'bio'
    label   = 'Bio/description'
    enabled = False
    MIN_BIO_CHARS = 20

    def __init__(self, profile_cache: dict = None):
        self._cache = profile_cache or {}

    def score(self, post):
        if not self.enabled:
            return 0, None
        profile = self._cache.get(post.get('author_handle', ''), {})
        desc = profile.get('description', None)
        if desc is None:
            return 5, 'no_bio'
        if len(desc.strip()) < self.MIN_BIO_CHARS:
            return 2, f'short_bio({len(desc.strip())})'
        return 0, None


# ══════════════════════════════════════════════════════════════════════════════
# SCORER
# ══════════════════════════════════════════════════════════════════════════════

class PostScorer:
    """
    Orchestrates all noise filters and returns a noise score + bucket for each post.

    Single post:
        result = scorer.score(post)
        # {'score': 3, 'bucket': 'dim', 'reasons': ['is_reply']}

    Batch (enables F13 repeated-handle detection):
        scored_posts = scorer.score_batch(posts)
        # Each post gets noise_score / noise_bucket / noise_reasons added in-place.
    """

    def __init__(self):
        self._profile_cache: dict = {}

        self._filters: list = [
            ContentLengthFilter(),
            AccountAgeFilter(),
            TagCountFilter(),
            EngagementFilter(),
            SolicitationFilter(),
            ReplyFilter(),
            LanguageFilter(),
            UrlOnlyFilter(),
            MentionCountFilter(),
            RepeatedHandleFilter(),
            # Profile-dependent — disabled until cache is wired up
            FollowerCountFilter(self._profile_cache),
            PostCountFilter(self._profile_cache),
            BioFilter(self._profile_cache),
        ]

    def register(self, f: NoiseFilter):
        """Add a custom filter."""
        self._filters.append(f)
        logger.info(f'[PostScorer] registered filter: {f.id}')

    def update_profile_cache(self, handle: str, profile: dict):
        """
        Store profile data for a handle (from app.bsky.actor.getProfile).
        Expected keys: followersCount, followsCount, postsCount, description.
        """
        self._profile_cache[handle] = profile

    def enable_filter(self, filter_id: str):
        """Enable a disabled filter by id."""
        for f in self._filters:
            if f.id == filter_id:
                f.enabled = True
                logger.info(f'[PostScorer] enabled: {filter_id}')
                return
        logger.warning(f'[PostScorer] filter not found: {filter_id}')

    def score(self, post: dict) -> dict:
        """
        Score a single post.
        Priority-account posts always return CLEAN.
        Note: does not inject _batch_handle_count — use score_batch() for F13.
        """
        if post.get('is_news_account', False):
            return {'score': 0, 'bucket': 'clean', 'reasons': []}

        total, reasons = 0, []
        for f in self._filters:
            if not f.enabled:
                continue
            try:
                pts, reason = f.score(post)
                if pts != 0:
                    total += pts
                    if reason:
                        reasons.append(reason)
            except Exception as e:
                logger.debug(f'[PostScorer] {f.id} error: {e}')

        total = max(0, total)
        bucket = ('hide' if total >= SCORE_HIDE else
                  'dim'  if total >= SCORE_DIM  else
                  'clean')
        return {'score': total, 'bucket': bucket, 'reasons': reasons}

    def score_batch(self, posts: list) -> list:
        """
        Score a list of posts, enabling F13 (repeated handle detection).
        Injects _batch_handle_count into each non-priority post before scoring.
        Adds noise_score / noise_bucket / noise_reasons to each post in-place.
        Returns the same list with those fields added.
        """
        # Count handle appearances among non-priority posts only
        handle_counts = Counter(
            p.get('author_handle', '')
            for p in posts
            if not p.get('is_news_account', False)
        )

        for post in posts:
            if not post.get('is_news_account', False):
                post['_batch_handle_count'] = handle_counts.get(
                    post.get('author_handle', ''), 1)

            result = self.score(post)
            post['noise_score']   = result['score']
            post['noise_bucket']  = result['bucket']
            post['noise_reasons'] = result['reasons']

        return posts

    def describe(self) -> list:
        """Return status of all registered filters (for /api/status or debug)."""
        return [
            {'id': f.id, 'label': f.label, 'enabled': f.enabled}
            for f in self._filters
        ]


# Module-level singleton
_scorer: Optional[PostScorer] = None

def get_scorer() -> PostScorer:
    global _scorer
    if _scorer is None:
        _scorer = PostScorer()
    return _scorer