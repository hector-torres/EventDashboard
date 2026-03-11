"""
Event Detection Module
Analyzes incoming posts to detect breaking news events.
Modular: add new detection strategies without touching other code.

Detection strategies (pluggable):
  - KeywordCluster: groups posts by keyword/topic clusters
  - VelocitySpike: detects sudden volume spikes on a topic

v2 improvements (based on Reuters Tracer research):
  - All-caps wire format detection (fintwitter/AFP/wire alerts)
  - Expanded geopolitical + military keyword vocabulary
  - Named entity co-occurrence scoring (country + action verb)
  - Broader VELOCITY_NOISE_WORDS to reduce generic-word spikes
  - Breaking / Developing / Stale event lifecycle states
  - Source-tier weighting (major news orgs count more toward threshold)
  - Proximity-aware proper noun check for velocity spikes
  - Event deduplication by keyword overlap (not just exact topic_key)
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import List, Dict, Optional
import re
import uuid

# ─── Breaking News Keywords ──────────────────────────────────────────────────
BREAKING_KEYWORDS = {
    'CRITICAL': {
        'phrases': [
            'breaking news', 'breaking:', 'breaking --', 'breaking \u2014',
            'just broke', 'shots fired', 'mass shooting', 'active shooter',
            'confirmed dead', 'multiple casualties', 'martial law',
            'state of emergency', 'amber alert', 'missing child',
            'nuclear alert', 'missile launch', 'bomb exploded', 'building collapsed',
            'drone strike', 'airstrike on', 'airstrikes on', 'missile strike',
            'rocket attack', 'killed in strike', 'killed in attack',
        ],
        'words': [
            'earthquake', 'tsunami', 'hurricane', 'tornado', 'explosion',
            'evacuation', 'airstrike', 'airstrikes',
        ],
    },
    'HIGH': {
        'phrases': [
            'just in', 'just confirmed', 'developing story', 'developing situation',
            'confirmed reports', 'official statement', 'press conference',
            'has been arrested', 'has been killed', 'has been indicted',
            'death toll', 'declared dead', 'found dead',
            'ceasefire violated', 'no-fly zone',
            'under attack', 'under siege', 'troops have entered', 'has declared',
        ],
        'words': [
            'crisis', 'attack', 'shooting', 'arrested', 'indicted',
            'election', 'coup', 'assassination', 'impeachment',
            'intercept', 'intercepts', 'intercepted',
            'deploys', 'deployed', 'mobilizes', 'mobilized',
            'invasion', 'invades', 'offensive', 'blockade', 'sanctions',
        ],
    },
    'MEDIUM': {
        'phrases': [
            'sources say', 'sources tell', 'according to officials',
            'per sources', 'we are told', 'reports indicate',
        ],
        'words': [
            'protest', 'crash', 'collision', 'missing', 'wildfire', 'floods',
            'outbreak', 'recall', 'ceasefire',
            'drones', 'warships', 'convoy', 'airspace', 'territory',
            'strait', 'pipeline', 'embargo',
        ],
    },
}

CRITICAL_FALSE_POSITIVE_CONTEXTS = [
    'breaking bad', 'groundbreaking', 'tie-breaking', 'record-breaking',
    'norm-breaking', 'path-breaking', 'barrier-breaking', 'game-breaking',
]

CLUSTER_THRESHOLD      = 3
CLUSTER_WINDOW_MINUTES = 10
MAX_EVENTS             = 50
AGE_BREAKING_MAX       = 30    # minutes
AGE_DEVELOPING_MAX     = 240   # minutes

# ─── Source Tier Weights ─────────────────────────────────────────────────────
SOURCE_TIER_WEIGHTS = {
    'reuters.com':         5,
    'apnews.com':          5,
    'en.afp.com':          5,
    'afp.com':             5,
    'bbc.co.uk':           5,
    'bbc.com':             5,
    'nytimes.com':         4,
    'wsj.com':             4,
    'ft.com':              4,
    'financialtimes.com':  4,
    'bloomberg.com':       4,
    'economist.com':       4,
    'theguardian.com':     3,
    'latimes.com':         3,
    'washingtonpost.com':  3,
    'chicagotribune.com':  3,
    'asia.nikkei.com':     3,
    'nikkei.com':          3,
    'japantimes.co.jp':    3,
    'thejerusalempost':    3,
    'haaretz.com':         3,
    'dw.com':              3,
    'spiegel.de':          3,
    'fintwitter':          2,
    'unusual_whales':      2,
    'cnbc.com':            2,
    'cnn.com':             2,
    'foxnews.com':         2,
    'sky.com':             2,
}

def _get_source_weight(author_handle: str) -> int:
    h = (author_handle or '').lower()
    for domain, weight in SOURCE_TIER_WEIGHTS.items():
        if domain in h:
            return weight
    return 1


# ─── Named Entity Co-occurrence Patterns ─────────────────────────────────────
COUNTRY_NAMES = {
    'iran', 'iraq', 'israel', 'russia', 'ukraine', 'china', 'taiwan',
    'north korea', 'south korea', 'pakistan', 'india', 'afghanistan',
    'syria', 'yemen', 'lebanon', 'libya', 'sudan', 'ethiopia', 'somalia',
    'venezuela', 'cuba', 'myanmar', 'belarus', 'georgia', 'armenia',
    'azerbaijan', 'moldova', 'serbia', 'kosovo', 'turkey', 'saudi arabia',
    'egypt', 'niger', 'mali', 'haiti', 'kuwait', 'qatar', 'bahrain',
    'iranian', 'russian', 'ukrainian', 'chinese', 'taiwanese', 'israeli',
    'north korean', 'syrian', 'yemeni', 'lebanese', 'kuwaiti',
}

GEO_ACTION_VERBS_HIGH = {
    'fires', 'fired', 'launches', 'launched', 'strikes', 'struck',
    'attacks', 'attacked', 'invades', 'invaded', 'deploys', 'deployed',
    'bombs', 'bombed', 'shells', 'shelled', 'intercepts', 'intercepted',
    'arrests', 'arrested', 'kills', 'killed', 'shoots', 'shot',
    'seizes', 'seized', 'blockades', 'imposes', 'closes', 'suspends',
    'threatens', 'withdraws', 'mobilizes', 'declares', 'confirms',
}

GEO_ACTION_VERBS_MEDIUM = {
    'cuts', 'raises', 'hikes', 'slashes', 'bans', 'sanctions',
    'expels', 'recalls', 'protests', 'warns', 'condemns',
    'collapses', 'defaults', 'surges', 'crashes', 'plunges',
}

PERSON_TITLES = {
    'president', 'prime minister', 'minister', 'secretary', 'general',
    'admiral', 'chancellor', 'premier', 'senator', 'governor',
    'ambassador', 'envoy', 'spokesman', 'spokesperson', 'official',
    'ceo', 'chief', 'director', 'chairman',
}

PERSON_ACTION_VERBS_HIGH = {
    'resigns', 'resigned', 'fired', 'arrested', 'killed', 'assassinated',
    'elected', 'appointed', 'confirmed', 'impeached', 'indicted',
    'charged', 'convicted', 'sentenced',
}

def _check_entity_patterns(text: str):
    """
    Check for named entity co-occurrence patterns.
    Returns (severity, description) or (None, None).
    """
    wlist = re.findall(r'\b\w+\b', text.lower())
    words = set(wlist)

    # Country co-occurrence check
    matched_country = None
    for country in COUNTRY_NAMES:
        if ' ' in country:
            if country in text.lower():
                matched_country = country
                break
        elif country in words:
            matched_country = country
            break

    if matched_country:
        if words & GEO_ACTION_VERBS_HIGH:
            verb = next(iter(words & GEO_ACTION_VERBS_HIGH))
            return 'HIGH', f'geo:{matched_country}+{verb}'
        if words & GEO_ACTION_VERBS_MEDIUM:
            verb = next(iter(words & GEO_ACTION_VERBS_MEDIUM))
            return 'MEDIUM', f'geo:{matched_country}+{verb}'

    # Person title + HIGH verb
    matched_title = words & PERSON_TITLES
    if matched_title and (words & PERSON_ACTION_VERBS_HIGH):
        title = next(iter(matched_title))
        verb  = next(iter(words & PERSON_ACTION_VERBS_HIGH))
        return 'HIGH', f'person:{title}+{verb}'

    return None, None


# ─── All-Caps Wire Detection ──────────────────────────────────────────────────
_WIRE_MIN_WORDS  = 5
_WIRE_CAPS_RATIO = 0.60
_WIRE_MIN_LEN    = 2

def _detect_wire_caps(text: str):
    """
    Detect all-caps wire/financial alert format.
    Checks each sentence independently so mixed-case posts don't cancel out.
    Returns ('HIGH', 'wire_alert') or (None, None).
    """
    for sentence in re.split(r'[\n.!?]', text):
        words = sentence.split()
        if len(words) < _WIRE_MIN_WORDS:
            continue
        significant = [w for w in words if len(w) >= _WIRE_MIN_LEN]
        if not significant:
            continue
        caps_count = sum(1 for w in significant if w.isupper() and w.isalpha())
        if caps_count / len(significant) >= _WIRE_CAPS_RATIO:
            return 'HIGH', 'wire_alert'
    return None, None


# ─── Detection Strategies ────────────────────────────────────────────────────

class DetectionStrategy:
    name = "base"

    def analyze(self, posts: List[Dict], existing_events: List[Dict]) -> List[Dict]:
        return []


class KeywordClusterStrategy(DetectionStrategy):
    name = "keyword_cluster"

    def analyze(self, posts: List[Dict], existing_events: List[Dict]) -> List[Dict]:
        clusters = defaultdict(list)

        for post in posts:
            raw_text   = post.get('text', '')
            text_lower = raw_text.lower()
            post_weight = max(
                post.get('weight', 1),
                _get_source_weight(post.get('author_handle', ''))
            )

            # 1. Standard keyword match
            severity, matched_kw = self._classify(text_lower)

            # 2. All-caps wire detection
            wire_sev, wire_kw = _detect_wire_caps(raw_text)
            if wire_sev and self._rank(wire_sev) > self._rank(severity):
                severity, matched_kw = wire_sev, wire_kw

            # 3. Named entity co-occurrence (fill in if no strong keyword yet)
            if not severity or severity == 'MEDIUM':
                ent_sev, ent_kw = _check_entity_patterns(raw_text)
                if ent_sev and self._rank(ent_sev) > self._rank(severity):
                    severity, matched_kw = ent_sev, ent_kw

            if severity and matched_kw:
                key = matched_kw.lower().replace(' ', '_')
                clusters[key].append({
                    'post':     post,
                    'severity': severity,
                    'keyword':  matched_kw,
                    'weight':   post_weight,
                })

        new_events = []
        existing_topics = {e.get('topic_key') for e in existing_events}

        for topic_key, cluster in clusters.items():
            total_weight = sum(c['weight'] for c in cluster)
            if total_weight < CLUSTER_THRESHOLD:
                continue
            if topic_key in existing_topics:
                continue
            if self._too_similar_to_existing(topic_key, existing_events):
                continue
            # Also dedup within this batch of new events
            if self._too_similar_to_existing(topic_key, new_events):
                continue

            severity  = cluster[0]['severity']
            keyword   = cluster[0]['keyword']
            sorted_c  = sorted(cluster, key=lambda c: c['weight'], reverse=True)
            sample_texts = [c['post']['text'][:120] for c in sorted_c[:3]]
            priority_sources = [
                c['post']['author_handle'] for c in cluster if c['post'].get('is_news_account')
            ]
            full_posts = [
                {
                    'handle':  c['post'].get('author_handle', ''),
                    'display': c['post'].get('author_display', ''),
                    'text':    c['post'].get('text', ''),
                    'url':     c['post'].get('url', ''),
                    'ts':      c['post'].get('indexed_at', c['post'].get('created_at', '')),
                    'is_news': c['post'].get('is_news_account', False),
                }
                for c in sorted_c
            ]

            new_events.append({
                'id':               str(uuid.uuid4())[:8],
                'topic_key':        topic_key,
                'title':            self._generate_title(keyword, sorted_c, total_weight),
                'severity':         severity,
                'post_count':       len(cluster),
                'weighted_count':   total_weight,
                'keyword':          keyword,
                'sample_posts':     sample_texts,
                'posts':            full_posts,
                'sources':          list({c['post']['author_handle'] for c in cluster[:5]}),
                'priority_sources': priority_sources,
                'detected_at':      datetime.now(timezone.utc).isoformat(),
                'strategy':         self.name,
                'status':           'breaking',
            })

        return new_events

    @staticmethod
    def _rank(severity) -> int:
        return {'CRITICAL': 3, 'HIGH': 2, 'MEDIUM': 1, None: 0}.get(severity, 0)

    def _classify(self, text: str):
        for fp in CRITICAL_FALSE_POSITIVE_CONTEXTS:
            if fp in text:
                text = text.replace(fp, ' ' * len(fp))
        for severity in ('CRITICAL', 'HIGH', 'MEDIUM'):
            spec = BREAKING_KEYWORDS[severity]
            for phrase in spec['phrases']:
                if phrase in text:
                    return severity, phrase
            for word in spec['words']:
                if re.search(r'\b' + re.escape(word) + r'\b', text):
                    return severity, word
        return None, None

    @staticmethod
    def _too_similar_to_existing(topic_key: str, existing_events: List[Dict]) -> bool:
        """
        Suppress a new event if its root tokens heavily overlap with an existing event.
        Uses 6-char prefix as a simple stem (intercept / intercepted → 'interc').
        """
        def stem_tokens(key):
            return {t[:6] for t in re.findall(r'\w{5,}', key.lower())}

        new_stems = stem_tokens(topic_key)
        if not new_stems:
            return False
        for ev in existing_events:
            ex_stems = stem_tokens(ev.get('topic_key', ''))
            if not ex_stems:
                continue
            if len(new_stems & ex_stems) / max(len(new_stems), 1) >= 0.6:
                return True
        return False

    def _generate_title(self, keyword: str, cluster: list, total_weight: float = 0) -> str:
        count      = len(cluster)
        news_accts = sum(1 for c in cluster if c['post'].get('is_news_account'))

        if keyword.startswith('geo:') or keyword.startswith('person:'):
            parts   = keyword.replace('geo:', '').replace('person:', '').split('+')
            subject = parts[0].title() if parts else keyword
            verb    = parts[1].replace('_', ' ').title() if len(parts) > 1 else ''
            kw_display = f"{subject} — {verb}" if verb else subject
        elif keyword == 'wire_alert':
            # Extract subject from the highest-weight post's first 60 chars
            best_text = cluster[0]['post'].get('text', '')[:60].strip()
            # Truncate at colon or em-dash (wire format: "SUBJECT: DETAIL")
            for sep in (':', '\u2014', ' - '):
                if sep in best_text:
                    best_text = best_text.split(sep)[0].strip()
                    break
            kw_display = best_text.title()[:40] if best_text else 'Wire Alert'
        else:
            kw_display = keyword.title() if len(keyword) < 30 else keyword[:30].title() + '...'

        if news_accts:
            return f"{kw_display} — {count} posts ({news_accts} news sources)"
        return f"{kw_display} — {count} posts"


# ─── Velocity Spike ───────────────────────────────────────────────────────────
VELOCITY_NOISE_WORDS: set = {
    'would', 'could', 'should', 'think', 'about', 'their', 'there', 'these',
    'those', 'other', 'after', 'before', 'where', 'while', 'which', 'going',
    'being', 'doing', 'having', 'every', 'never', 'still', 'again', 'might',
    'since', 'until', 'today', 'first', 'right', 'great', 'years', 'times',
    'weeks', 'maybe', 'really', 'people', 'things', 'because', 'without',
    'something', 'anything', 'nothing', 'everyone', 'between', 'another',
    'through', 'during', 'against', 'within', 'across', 'become', 'around',
    'under', 'above', 'below', 'using', 'according', 'following',
    'million', 'billion', 'trillion', 'thousand', 'hundred',
    # v2 additions
    'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'second',
    'third', 'fourth', 'fifth', 'tenth', 'much', 'many', 'most', 'some',
    'both', 'each', 'only', 'even', 'just', 'back', 'long',
    'make', 'made', 'said', 'says', 'also', 'very', 'more', 'less',
    'world', 'global', 'major', 'large', 'small', 'high', 'huge',
    'year', 'month', 'week', 'days', 'hours', 'time',
    'news', 'story', 'event', 'latest', 'update', 'recent',
    'report', 'reports', 'source', 'sources', 'statement',
    'share', 'shared', 'check', 'watch', 'video', 'photo', 'image',
    'thread', 'twitter', 'bluesky', 'posted', 'replies', 'comments',
    'article', 'continue', 'continued',
}

_ACRONYM_RE    = re.compile(r'\b[A-Z]{3,6}\b')
_PROPERNOUN_RE = re.compile(r'\b[A-Z][a-z]{2,}\b')


def _word_is_signal(word: str, sample_texts: List[str]) -> bool:
    """v2: require proper noun within 10 tokens of the spiking word in >=2 posts."""
    if word in VELOCITY_NOISE_WORDS:
        return False
    nearby_count = 0
    for text in sample_texts[:10]:
        tokens = text.split()
        for i, tok in enumerate(tokens):
            if tok.lower() == word:
                window = ' '.join(tokens[max(0, i-10): i+11])
                if _ACRONYM_RE.search(window) or _PROPERNOUN_RE.search(window):
                    nearby_count += 1
                    break
    return nearby_count >= 2


class VelocitySpikeStrategy(DetectionStrategy):
    name = "velocity_spike"

    def __init__(self):
        self._history: Dict[str, List] = defaultdict(list)

    def analyze(self, posts: List[Dict], existing_events: List[Dict]) -> List[Dict]:
        now    = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=CLUSTER_WINDOW_MINUTES)
        new_events = []

        for post in posts:
            raw_text = post.get('text', '')
            words    = set(re.findall(r'\b\w{6,}\b', raw_text.lower()))
            weight   = max(
                post.get('weight', 1),
                _get_source_weight(post.get('author_handle', ''))
            )
            for word in words:
                self._history[word].append((now, weight, raw_text))
                self._history[word] = [e for e in self._history[word] if e[0] > cutoff]

        existing_topics = {e.get('topic_key') for e in existing_events}

        for word, entries in self._history.items():
            topic_key    = f"velocity_{word}"
            total_weight = sum(w for _, w, _ in entries)

            if total_weight < 10:
                continue
            if topic_key in existing_topics:
                continue
            sample_texts = [t for _, _, t in entries]
            if not _word_is_signal(word, sample_texts):
                continue

            new_events.append({
                'id':             str(uuid.uuid4())[:8],
                'topic_key':      topic_key,
                'title':          f'Volume spike: "{word}" ({len(entries)} mentions, weight {total_weight:.0f})',
                'severity':       'MEDIUM',
                'post_count':     len(entries),
                'weighted_count': total_weight,
                'keyword':        word,
                'sample_posts':   [t[:140] for _, _, t in entries[:3]],
                'sources':        [],
                'priority_sources': [],
                'detected_at':    now.isoformat(),
                'strategy':       self.name,
                'status':         'breaking',
            })

        return new_events


# ─── Event Lifecycle ──────────────────────────────────────────────────────────

def _compute_event_status(detected_at_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(detected_at_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds() / 60
        if age < AGE_BREAKING_MAX:
            return 'breaking'
        elif age < AGE_DEVELOPING_MAX:
            return 'developing'
        else:
            return 'stale'
    except Exception:
        return 'active'


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class EventDetector:
    def __init__(self):
        self.strategies: List[DetectionStrategy] = [
            KeywordClusterStrategy(),
            VelocitySpikeStrategy(),
        ]
        self._events: List[Dict] = []

    def analyze(self, posts: List[Dict]) -> List[Dict]:
        all_new = []
        for strategy in self.strategies:
            try:
                new = strategy.analyze(posts, self._events)
                all_new.extend(new)
            except Exception as e:
                print(f"[EventDetector] Strategy '{strategy.name}' error: {e}")

        if all_new:
            self._events = (all_new + self._events)[:MAX_EVENTS]
            print(f"[EventDetector] {len(all_new)} new event(s) detected.")

        for ev in self._events:
            ev['status'] = _compute_event_status(ev.get('detected_at', ''))

        return all_new

    def get_events(self) -> List[Dict]:
        for ev in self._events:
            ev['status'] = _compute_event_status(ev.get('detected_at', ''))
        return self._events

    def clear_events(self):
        self._events = []

    def add_strategy(self, strategy: DetectionStrategy):
        self.strategies.append(strategy)