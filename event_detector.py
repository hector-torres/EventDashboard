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

v2.1 event quality improvements:
  - #1 Cluster coherence gate: posts in a cluster must share ≥1 named entity
    (e.g. "attack" about Tehran + "attack" about 2023 street art → suppressed)
  - #2 Entity requirement for ambiguous words: high-ambiguity single-word triggers
    (attack, crisis, shooting, crash…) require a named entity in each post
  - #3 Historical reference filter: posts referencing a past year near the keyword
    or using retrospective framing ("after the attack", "since the shooting")
    are excluded from clusters; priority/news accounts are exempt
  - #4 Per-word threshold overrides: ambiguous single words require higher
    cluster weight (5 instead of 3) before firing an event
"""

from datetime import datetime, timezone, timedelta
from collections import defaultdict
from typing import List, Dict, Optional
import re
import uuid

# NLP enhancement layer (spaCy optional — degrades gracefully without it)
try:
    from nlp_enhancer import get_enhancer as _get_nlp
    _nlp = _get_nlp()
    print(f"[EventDetector] NLP enhancer loaded: {_nlp.describe()}")
except ImportError:
    _nlp = None
    print("[EventDetector] nlp_enhancer not found — NLP features disabled")

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


# ─── Improvement #2: High-ambiguity single words that require a named entity ──
# These words appear in breaking news but are too generic on their own to form
# a coherent event. A post matching one of these must also contain at least one
# named entity (person, place, or org) to be counted toward a cluster.
ENTITY_REQUIRED_WORDS: set = {
    'attack', 'attacks', 'attacked',
    'crisis', 'shooting', 'crash', 'explosion', 'protest', 'protests',
    'arrested', 'arrest', 'offensive', 'invasion', 'sanctions',
    'collision', 'outbreak', 'wildfire', 'floods', 'missing',
}

# ─── Format-label phrases: require entity AND high threshold ──────────────────
# These CRITICAL/HIGH phrases match the format of a post rather than its content.
# "breaking news" from a spam aggregator is indistinguishable from a real wire
# alert on keyword alone — they need a named entity in the post to anchor the
# event, and a higher cluster weight to prevent single-source spam clusters.
ENTITY_REQUIRED_PHRASES: set = {
    'breaking news',
    'just in',
    'developing story',
    'developing situation',
    'flash alert',
}

# ─── Improvement #4: Per-word cluster threshold overrides ─────────────────────
# Generic single-word triggers need more corroborating posts before firing.
# Specific phrases (e.g. "confirmed dead") keep the global CLUSTER_THRESHOLD.
CLUSTER_THRESHOLD_OVERRIDES: dict = {
    # Ambiguous single words — need more posts before firing
    'attack':          5,
    'attacks':         5,
    'attacked':        5,
    'crisis':          5,
    'shooting':        5,
    'crash':           5,
    'explosion':       5,
    'protest':         5,
    'protests':        5,
    'arrested':        5,
    'offensive':       5,
    'invasion':        5,
    'sanctions':       4,
    'collision':       4,
    'outbreak':        4,
    'wildfire':        4,
    'floods':          4,
    'missing':         5,
    'election':        5,
    'coup':            4,
    # Format-label phrases — high threshold to require diverse sourcing
    'breaking_news':         7,
    'just_in':               6,
    'developing_story':      6,
    'developing_situation':  6,
    'flash_alert':           6,
}

CLUSTER_THRESHOLD      = 3
CLUSTER_WINDOW_MINUTES = 10
MAX_EVENTS             = 50
AGE_BREAKING_MAX       = 30    # minutes
AGE_DEVELOPING_MAX     = 240   # minutes

# ─── Source Tier Weights ─────────────────────────────────────────────────────
SOURCE_TIER_WEIGHTS = {
    # Tier 5 — major wire services
    'reuters.com':         5,
    'apnews.com':          5,
    'en.afp.com':          5,
    'afp.com':             5,
    'bbc.co.uk':           5,
    'bbc.com':             5,
    # Tier 4 — major financial / national papers
    'nytimes.com':         4,
    'wsj.com':             4,
    'ft.com':              4,
    'financialtimes.com':  4,
    'bloomberg.com':       4,
    'economist.com':       4,
    # Tier 3 — quality nationals and regional internationals
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
    'nbcnews.com':         3,
    'cbsnews.com':         3,
    'npr.org':             3,
    'axios.com':           3,
    'politico.com':        3,
    'scmp.com':            3,          # South China Morning Post
    'aljazeera.com':       3,
    'sydmorningherald':    3,          # Sydney Morning Herald (.bsky.social)
    'fintwitter':          3,          # elevated from 2 — reliable fin alerts
    # Tier 2 — cable news, aggregators, specialist outlets
    'cnbc.com':            2,
    'cnn.com':             2,
    'foxnews.com':         2,
    'sky.com':             2,
    'news.sky.com':        2,
    'unusual_whales':      2,
    'unusualwhales':       2,
    'thehill.com':         2,
    'politico.eu':         2,
    'thediplomat.com':     2,
    'usatoday.com':        2,
    'expressnews.com':     2,          # San Antonio Express-News
    'dallasnews.com':      2,
    'globalnews.ca':       2,
    'ms.now':              2,          # MSNBC / ms.now
    'msnbc.com':           2,
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
    # Key cities, territories, and regions frequently in breaking news
    'gaza', 'west bank', 'jerusalem', 'kyiv', 'moscow', 'beijing', 'taipei',
    'tehran', 'baghdad', 'damascus', 'kabul', 'tripoli', 'khartoum',
    'caracas', 'havana', 'minsk', 'seoul', 'pyongyang', 'doha',
    'riyadh', 'cairo', 'islamabad', 'kathmandu',
    'crimea', 'donbas', 'donetsk', 'kharkiv', 'zaporizhzhia',
    'taiwan strait', 'south china sea', 'red sea', 'black sea',
    'strait of hormuz', 'nagorno-karabakh',
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

    Uses NLP enhancer (spaCy or regex fallback) when available.
    Falls back to the original country/verb word-list approach otherwise.
    """
    if _nlp is not None:
        sev, kw = _nlp.entity_severity_check(text, None, None)
        if sev:
            return sev, kw

    # Legacy word-list fallback (always runs if NLP disabled)
    wlist = re.findall(r'\b\w+\b', text.lower())
    words = set(wlist)

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

    matched_title = words & PERSON_TITLES
    if matched_title and (words & PERSON_ACTION_VERBS_HIGH):
        title = next(iter(matched_title))
        verb  = next(iter(words & PERSON_ACTION_VERBS_HIGH))
        return 'HIGH', f'person:{title}+{verb}'

    return None, None



# ─── Nationality/demonym normalisation for event titles ─────────────────────
# Maps adjectival/nationality forms to canonical country name
_NORP_TO_COUNTRY: dict = {
    'iranian': 'Iran',       'iraqi': 'Iraq',         'israeli': 'Israel',
    'russian': 'Russia',     'ukrainian': 'Ukraine',   'chinese': 'China',
    'taiwanese': 'Taiwan',   'north korean': 'North Korea',
    'south korean': 'South Korea', 'syrian': 'Syria',  'yemeni': 'Yemen',
    'lebanese': 'Lebanon',   'libyan': 'Libya',        'sudanese': 'Sudan',
    'qatari': 'Qatar',       'kuwaiti': 'Kuwait',      'bahraini': 'Bahrain',
    'saudi': 'Saudi Arabia', 'turkish': 'Turkey',      'egyptian': 'Egypt',
    'pakistani': 'Pakistan', 'indian': 'India',        'afghan': 'Afghanistan',
    'venezuelan': 'Venezuela', 'cuban': 'Cuba',        'belarusian': 'Belarus',
    'georgian': 'Georgia',   'armenian': 'Armenia',    'azerbaijani': 'Azerbaijan',
    'serbian': 'Serbia',     'american': 'US',         'british': 'UK',
    'french': 'France',      'german': 'Germany',      'italian': 'Italy',
    'spanish': 'Spain',      'japanese': 'Japan',      'korean': 'South Korea',
}

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

            # Skip dim/hide posts — low-quality signal shouldn't seed event clusters.
            # Priority/news account posts are never scored (always 'clean'), so this
            # only filters keyword-sweep posts that the noise scorer flagged.
            if post.get('noise_bucket') in ('dim', 'hide'):
                continue

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

            if not (severity and matched_kw):
                continue

            # Gate: negation check
            if _nlp is not None and _nlp.is_negated(raw_text, matched_kw):
                continue

            # Improvement #3: skip posts that are clearly historical references
            # e.g. "the October 2023 Hamas attack on Israel" — past year near keyword,
            # or "after the attack / since the shooting" framing constructions.
            # Priority/news account posts are exempt — they may legitimately reference
            # past context while reporting current developments.
            if (_nlp is not None
                    and not post.get('is_news_account')
                    and not post.get('is_priority')
                    and _nlp.is_historical_reference(raw_text, matched_kw)):
                continue

            # Improvement #2: entity requirement for high-ambiguity single words
            # and format-label phrases.
            # Words like "attack", "crisis" and phrases like "breaking news" are too
            # generic to form a coherent event without a named entity in the post.
            base_kw = matched_kw.lower().strip()
            if base_kw in ENTITY_REQUIRED_WORDS or base_kw in ENTITY_REQUIRED_PHRASES:
                if _nlp is not None:
                    entities = _nlp.extract_entities(raw_text)
                else:
                    # Fallback: require at least one capitalised proper noun
                    entities = re.findall(r'\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b',
                                          raw_text)
                if not entities:
                    continue   # drop post — no named entity to anchor the event

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

            # Improvement #4: per-word/phrase threshold — ambiguous triggers require
            # more corroborating posts before firing an event.
            kw_lower     = cluster[0]['keyword'].lower().strip()
            kw_key       = kw_lower.replace(' ', '_')   # phrase form for override lookup
            threshold    = CLUSTER_THRESHOLD_OVERRIDES.get(
                kw_key, CLUSTER_THRESHOLD_OVERRIDES.get(kw_lower, CLUSTER_THRESHOLD)
            )
            if total_weight < threshold:
                continue

            if topic_key in existing_topics:
                continue

            # Improvement #1: cluster coherence — require entity overlap.
            # For clusters of 3+ posts, at least (coherence_min_fraction) of posts
            # must share at least one named entity text with another post in the cluster.
            # This prevents "attack" from grouping Tehran news with street-art posts.
            # Wire alerts and geo/entity-keyed events are exempt (already entity-grounded).
            if (len(cluster) >= 3
                    and _nlp is not None
                    and not kw_lower.startswith(('geo:', 'ent:', 'person:'))
                    and kw_lower != 'wire_alert'):
                coherent = self._check_cluster_coherence(cluster)
                if not coherent:
                    continue
            # Phase 3: semantic similarity check (upgrades stem-overlap when ST installed)
            if _nlp is not None:
                title_preview = f"{cluster[0]['keyword']} — {len(cluster)} posts"
                is_dup, sim = _nlp.is_duplicate_event(title_preview, topic_key)
                if is_dup:
                    continue
            elif self._too_similar_to_existing(topic_key, existing_events):
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

            generated_title = self._generate_title(keyword, sorted_c, total_weight)
            # Phase 3: register in semantic dedup buffer
            if _nlp is not None:
                _nlp.register_event(generated_title, topic_key)

            new_events.append({
                'id':               str(uuid.uuid4())[:8],
                'topic_key':        topic_key,
                'title':            generated_title,
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


    @staticmethod
    def _check_cluster_coherence(cluster: list) -> bool:
        """
        Improvement #1: Entity coherence gate.

        For a cluster to fire as a real event, at least COHERENCE_MIN_POSTS posts
        must share at least one named entity text with at least one other post.

        Algorithm:
        1. Extract entity texts from each post (lowercased for comparison).
        2. Build a union of all entity texts across all posts.
        3. For each post, check if any of its entities appear in another post.
        4. Count how many posts pass — if the fraction >= COHERENCE_MIN_FRACTION,
           the cluster is coherent.

        Degrades gracefully: if NLP is unavailable the gate is bypassed (the
        _check_cluster_coherence call is only made when _nlp is not None).

        Examples:
          "Tehran retaliates for attack" + "Israel responds to attack" → share
          "Israel" / "Tehran" → coherent ✓
          "Good morning, Asia [attack story]" + "street art after Hamas attack"
          → share no entities → incoherent, suppressed ✗
        """
        COHERENCE_MIN_FRACTION = 0.50  # at least 50% of posts must share an entity
        COHERENCE_MIN_POSTS    = 1     # at least 1 post pair must share an entity

        post_entity_sets = []
        for item in cluster:
            raw = item['post'].get('text', '')
            ents = _nlp.extract_entities(raw)
            # Normalise: lowercase, strip, drop very short tokens
            ent_texts = frozenset(
                e['text'].lower().strip()
                for e in ents
                if len(e['text'].strip()) >= 3
            )
            post_entity_sets.append(ent_texts)

        if not any(post_entity_sets):
            # No entities extracted at all — can't assess coherence, allow through
            return True

        # Build union of all entities
        all_entities = frozenset().union(*post_entity_sets)
        if not all_entities:
            return True

        # Count posts that share at least one entity with any other post
        coherent_count = 0
        for i, ents_i in enumerate(post_entity_sets):
            if not ents_i:
                continue
            for j, ents_j in enumerate(post_entity_sets):
                if i == j:
                    continue
                if ents_i & ents_j:   # non-empty intersection
                    coherent_count += 1
                    break             # only count each post once

        fraction = coherent_count / len(cluster)
        return (fraction >= COHERENCE_MIN_FRACTION
                or coherent_count >= COHERENCE_MIN_POSTS + 1)



    def _generate_title(self, keyword: str, cluster: list, total_weight: float = 0) -> str:
        """
        Build an informative event title from the five semantic components:
        WHO (named entity/subject) · WHAT (action verb) · WHERE (location) · OBJECT

        Strategy:
        1. For geo:/person:/ent: prefixed keywords — use the structured key directly
        2. For wire_alert — parse the wire header (SUBJECT: DETAIL format)
        3. For all other keywords — extract entities + action phrase from the best post
        Falls back gracefully at each step so something always renders.
        """
        count      = len(cluster)
        news_accts = sum(1 for c in cluster if c['post'].get('is_news_account'))

        if keyword.startswith(('geo:', 'person:', 'ent:')):
            parts   = keyword.replace('geo:', '').replace('person:', '').replace('ent:', '').split('+')
            subject = parts[0].title() if parts else keyword
            verb    = parts[1].replace('_', ' ').title() if len(parts) > 1 else ''
            kw_display = f"{subject} — {verb}" if verb else subject

        elif keyword == 'wire_alert':
            best_text = cluster[0]['post'].get('text', '')[:80].strip()
            # Strip leading BREAKING / JUST IN / ALERT prefixes
            best_text = re.sub(
                r'^(?:BREAKING|JUST IN|ALERT|FLASH|URGENT|UPDATE)[:\s\-—]*',
                '', best_text, flags=re.IGNORECASE
            ).strip()
            for sep in (':', '—', ' - ', ' | '):
                if sep in best_text:
                    best_text = best_text.split(sep)[0].strip()
                    break
            kw_display = best_text.title()[:55] if best_text else 'Wire Alert'

        else:
            # General case: extract semantic components from the best post
            kw_display = self._extract_semantic_title(keyword, cluster)

        suffix = f" ({news_accts} news {'source' if news_accts == 1 else 'sources'})" if news_accts else ''
        return f"{kw_display} — {count} {'post' if count == 1 else 'posts'}{suffix}"

    def _extract_semantic_title(self, keyword: str, cluster: list) -> str:
        """
        Extract WHO · WHAT · WHERE from the highest-weight post to build a
        descriptive title. Uses NLP entities when available, falls back to
        regex patterns.

        Target output examples:
          "Iran — Missile Attack — Qatar Gas Facility"
          "Israel — Airstrike — Gaza"
          "Fed Reserve — Rate Decision"
          "Hurricane Milton — Florida"
        """
        # Use the highest-weight post as the primary source
        best = cluster[0]['post']
        text = best.get('text', '')

        # Strip common wire prefixes so they don't pollute entity extraction
        clean = re.sub(
            r'^(?:BREAKING|JUST IN|ALERT|FLASH|URGENT|UPDATE|DEVELOPING)[:\s\-—]*',
            '', text, flags=re.IGNORECASE
        ).strip()

        entities = []
        if _nlp is not None:
            entities = _nlp.extract_entities(clean)

        # ── Extract WHO (subject/actor) ───────────────────────────────────────
        # Priority: GPE/NORP (country/nationality) > PERSON/ORG > first PROPER
        who = None
        where = None
        orgs  = []
        gpes  = []
        persons = []

        for e in entities:
            label = e.get('label', '')
            txt   = e['text'].strip()
            if not txt or len(txt) < 2:
                continue
            if label in ('GPE', 'LOC', 'NORP', 'FAC'):
                gpes.append(txt)
            elif label == 'PERSON':
                persons.append(txt)
            elif label in ('ORG', 'PROPER'):
                orgs.append(txt)

        # Also check for known country names in text (fallback when NLP is regex-only)
        text_lower = clean.lower()
        detected_countries = [c.title() for c in COUNTRY_NAMES if c in text_lower and ' ' not in c]
        detected_countries += [c.title() for c in COUNTRY_NAMES if ' ' in c and c in text_lower]

        # WHO = first GPE or detected country; WHERE = second GPE (if different)
        if gpes:
            who   = gpes[0]
            if len(gpes) > 1 and gpes[1].lower() != gpes[0].lower():
                where = gpes[1]
        elif detected_countries:
            who = detected_countries[0]
            if len(detected_countries) > 1:
                where = detected_countries[1]
        elif persons:
            who = persons[0]
        elif orgs:
            who = orgs[0]

        # ── Extract WHAT (action phrase) ──────────────────────────────────────
        # Build from keyword + any action verb found in the post near the keyword
        kw_title = keyword.title()

        # Look for a more descriptive noun phrase around the keyword in the text
        # Pattern: look for "[adjective] [noun] [keyword]" or "[keyword] [on/of/against] [noun]"
        action_phrase = None

        # Check for modifier before keyword: "missile attack", "drone strike", "rocket attack"
        modifiers = re.search(
            r'\b(missile|drone|rocket|mortar|cyber|suicide|car bomb|'
            r'nuclear|chemical|biological|coordinated|deadly|fatal|'
            r'military|armed|terrorist|mass|random|targeted)\s+' + re.escape(keyword),
            text_lower
        )
        if modifiers:
            action_phrase = modifiers.group(0).title()

        # Check for "[keyword] on/against/in [object]" pattern
        object_match = re.search(
            re.escape(keyword) + r'\s+(?:on|against|in|at|near|targeting)\s+([\w\s]{3,30})',
            text_lower
        )
        if object_match and not action_phrase:
            obj = object_match.group(1).strip()
            # Don't repeat WHO in the object phrase — whole-word match only
            who_lower = (who or '').lower()
            if who_lower and re.match(re.escape(who_lower) + r'\b', obj.lower()):
                obj = obj[len(who_lower):].strip()
            if obj and len(obj) >= 3:
                action_phrase = f"{kw_title} — {obj.title()[:25]}"

        what = action_phrase or kw_title

        # ── Extract WHERE (object/location if not already used as WHO) ────────
        if not where and gpes and len(gpes) > 1:
            where = gpes[1]
        if not where and detected_countries and len(detected_countries) > 1:
            where = detected_countries[1]

        # ── Normalise demonyms → country names ───────────────────────────────
        def _norm(name):
            return _NORP_TO_COUNTRY.get(name.lower(), name) if name else name

        who   = _norm(who)
        where = _norm(where)

        # ── Deduplicate: drop WHERE if it's the same root as WHO ─────────────
        def _root(name):
            return name.lower().replace(' ', '').rstrip('s')[:6] if name else ''

        if where and _root(where) == _root(who):
            where = None

        # ── Truncate object phrase cleanly at word boundary ───────────────────
        if where and len(where) > 22:
            trimmed = where[:22].rsplit(' ', 1)[0]
            where   = trimmed if trimmed else where[:22]

        # ── Assemble title: WHO — WHAT [— WHERE] ─────────────────────────────
        parts = []
        if who:
            parts.append(who[:30])
        parts.append(what[:35])
        if where and _root(where) != _root(who or ''):
            parts.append(where[:25])

        if parts:
            return ' — '.join(parts)

        # Final fallback: title-case the keyword
        return kw_title


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


def _spike_entity_coherent(word: str, sample_texts: List[str],
                            is_news_list: List[bool]) -> bool:
    """
    Gate #2 for VelocitySpikeStrategy: entity coherence check.

    Require that at least 2 posts share a named entity (capitalised word or
    acronym) that appears near the spiking word. This prevents a spike from
    firing when a word appears in many posts but always in completely different
    contexts (e.g. "people" spiking across unrelated posts).

    News account posts are counted with double weight toward the coherence
    threshold — a single wire alert near a proper noun is strong signal.

    Returns True (coherent) when:
      - Any 2+ non-news posts share a nearby entity, OR
      - 1+ news account post has a nearby entity (wire alerts are pre-vetted)
    """
    # Extract the entity set visible near `word` in each post
    nearby_entities: List[set] = []
    news_has_entity = False

    for text, is_news in zip(sample_texts[:15], is_news_list[:15]):
        tokens = text.split()
        post_entities: set = set()
        for i, tok in enumerate(tokens):
            if tok.lower() == word:
                window = ' '.join(tokens[max(0, i-10): i+11])
                # Collect all proper nouns / acronyms within the window
                for m in _PROPERNOUN_RE.finditer(window):
                    post_entities.add(m.group(0).lower())
                for m in _ACRONYM_RE.finditer(window):
                    post_entities.add(m.group(0).lower())
        if post_entities:
            if is_news:
                news_has_entity = True   # wire alert with entity = strong signal
            nearby_entities.append(post_entities)

    # News account post with entity nearby → pass immediately
    if news_has_entity:
        return True

    # Require at least 2 non-news posts that share >=1 entity
    if len(nearby_entities) < 2:
        return False
    for i in range(len(nearby_entities)):
        for j in range(i + 1, len(nearby_entities)):
            if nearby_entities[i] & nearby_entities[j]:
                return True
    return False


class VelocitySpikeStrategy(DetectionStrategy):
    name = "velocity_spike"

    def __init__(self):
        self._history: Dict[str, List] = defaultdict(list)

    def analyze(self, posts: List[Dict], existing_events: List[Dict]) -> List[Dict]:
        now    = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=CLUSTER_WINDOW_MINUTES)
        new_events = []

        for post in posts:
            # Gate #1: skip dim/hide posts — same rule as KeywordClusterStrategy.
            # Low-quality keyword-sweep posts shouldn't seed spike signals.
            if post.get('noise_bucket') in ('dim', 'hide'):
                continue

            raw_text = post.get('text', '')

            # Gate #3: historical reference filter — skip posts where the word
            # appears in a past-tense / retrospective context.
            # Applied at ingest so historical words never accumulate weight.
            if _nlp is not None:
                words_in_post = set(re.findall(r'\b\w{6,}\b', raw_text.lower()))
                filtered_words = set()
                for w in words_in_post:
                    if not _nlp.is_historical_reference(raw_text, w):
                        filtered_words.add(w)
                words = filtered_words
            else:
                words = set(re.findall(r'\b\w{6,}\b', raw_text.lower()))

            weight = max(
                post.get('weight', 1),
                _get_source_weight(post.get('author_handle', ''))
            )
            is_news = post.get('is_news_account', False)
            for word in words:
                # Store (timestamp, weight, text, is_news) for richer gate checks
                self._history[word].append((now, weight, raw_text, is_news))
                self._history[word] = [e for e in self._history[word] if e[0] > cutoff]

        existing_topics = {e.get('topic_key') for e in existing_events}

        for word, entries in self._history.items():
            topic_key    = f"velocity_{word}"
            total_weight = sum(w for _, w, _, _ in entries)

            if total_weight < 10:
                continue
            if topic_key in existing_topics:
                continue

            sample_texts = [t for _, _, t, _ in entries]
            is_news_list = [n for _, _, _, n in entries]

            # Gate #2 (signal check + entity coherence): require proper noun proximity
            # in >=2 posts, AND at least 2 posts sharing a named entity near the word.
            if not _word_is_signal(word, sample_texts):
                continue
            if not _spike_entity_coherent(word, sample_texts, is_news_list):
                continue

            # Build a synthetic cluster structure so we can reuse _generate_title
            sorted_entries = sorted(entries, key=lambda e: e[1], reverse=True)
            pseudo_cluster = [
                {
                    'post': {'text': t, 'is_news_account': n,
                              'author_handle': '', 'weight': w},
                    'weight': w,
                }
                for _, w, t, n in sorted_entries[:5]
            ]
            news_accts = sum(1 for _, _, _, n in entries if n)
            title = self._generate_spike_title(word, pseudo_cluster, total_weight, news_accts)

            new_events.append({
                'id':               str(uuid.uuid4())[:8],
                'topic_key':        topic_key,
                'title':            title,
                'severity':         'MEDIUM',
                'post_count':       len(entries),
                'weighted_count':   total_weight,
                'keyword':          word,
                'sample_posts':     [t[:140] for t in sample_texts[:3]],
                'sources':          [],
                'priority_sources': [e[2][:40] for e in entries if e[3]][:3],
                'detected_at':      now.isoformat(),
                'strategy':         self.name,
                'status':           'breaking',
            })

        return new_events

    def _generate_spike_title(self, word: str, cluster: list,
                               total_weight: float, news_accts: int) -> str:
        """
        Build a semantic title for a velocity spike event.
        Reuses KeywordClusterStrategy._extract_semantic_title logic via
        a pseudo-cluster built from the highest-weight spike entries.
        Falls back to a descriptive keyword title if extraction yields nothing.
        """
        # Try to extract WHO — WHAT — WHERE from the best posts
        if cluster:
            semantic = KeywordClusterStrategy._extract_semantic_title(
                KeywordClusterStrategy, word, cluster
            )
            # _extract_semantic_title returns bare keyword.title() as fallback,
            # so only use the result if it added something beyond the keyword itself
            if semantic and semantic.lower() != word.lower():
                count  = len(cluster)
                suffix = (f" ({news_accts} news "
                          f"{'source' if news_accts == 1 else 'sources'})"
                          if news_accts else '')
                return f"{semantic} — {count} {'post' if count == 1 else 'posts'}{suffix}"

        # Fallback: descriptive spike title (better than raw "Volume spike: word")
        count  = len(cluster)
        suffix = (f" ({news_accts} news "
                  f"{'source' if news_accts == 1 else 'sources'})"
                  if news_accts else '')
        return f"{word.title()} Spike — {count} {'post' if count == 1 else 'posts'}{suffix}"


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


# ─── Phase 4: Zero-shot Detection Strategy ───────────────────────────────────

class ZeroShotStrategy(DetectionStrategy):
    """
    Phase 4: Detect events that contain no keyword match but have a named entity
    plus a high-confidence zero-shot category classification.

    Only active when sentence-transformers is installed and model is loaded.
    Produces MEDIUM events tagged with the classified category.
    Skips posts already captured by KeywordClusterStrategy.
    """
    name = "zero_shot"

    def analyze(self, posts: List[Dict], existing_events: List[Dict]) -> List[Dict]:
        if _nlp is None or not _nlp.zero_shot_enabled():
            return []

        new_events = []
        existing_topics = {e.get('topic_key') for e in existing_events}

        # Collect posts that have a named entity but no keyword match
        # Group them by zero-shot category within a sliding window
        from collections import defaultdict
        category_posts: Dict[str, List] = defaultdict(list)

        for post in posts:
            raw_text = post.get('text', '')
            # Skip if already matched by keyword strategies
            # (check by seeing if this exact post is in existing events' posts)
            entities = _nlp.extract_entities(raw_text)
            if not entities:
                continue  # require at least one named entity

            result = _nlp.classify_post(raw_text)
            if result is None:
                continue

            cat_key, confidence = result
            if confidence < ZERO_SHOT_MIN_SCORE:
                continue

            weight = max(
                post.get('weight', 1),
                _get_source_weight(post.get('author_handle', ''))
            )
            category_posts[cat_key].append({
                'post':       post,
                'confidence': confidence,
                'entities':   entities,
                'weight':     weight,
            })

        for cat_key, cluster in category_posts.items():
            total_weight = sum(c['weight'] for c in cluster)
            if total_weight < CLUSTER_THRESHOLD:
                continue

            topic_key = f'zs_{cat_key}'
            if topic_key in existing_topics:
                continue

            # Require at least one news account or weight ≥ 5 to avoid noise
            has_news    = any(c['post'].get('is_news_account') for c in cluster)
            high_weight = any(c['weight'] >= 3 for c in cluster)
            if not (has_news or high_weight):
                continue

            sorted_c     = sorted(cluster, key=lambda c: c['confidence'], reverse=True)
            best_conf    = sorted_c[0]['confidence']
            best_ents    = sorted_c[0]['entities']
            entity_str   = best_ents[0]['text'] if best_ents else cat_key
            sample_texts = [c['post']['text'][:120] for c in sorted_c[:3]]

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

            cat_display = cat_key.replace('_', ' ').title()
            title = (f"[{cat_display}] {entity_str} — "
                     f"{len(cluster)} posts (conf {best_conf:.2f})")

            if _nlp is not None:
                _nlp.register_event(title, topic_key)

            new_events.append({
                'id':               str(uuid.uuid4())[:8],
                'topic_key':        topic_key,
                'title':            title,
                'severity':         'MEDIUM',
                'post_count':       len(cluster),
                'weighted_count':   total_weight,
                'keyword':          cat_key,
                'category':         cat_display,
                'zero_shot_conf':   round(best_conf, 3),
                'sample_posts':     sample_texts,
                'posts':            full_posts,
                'sources':          list({c['post']['author_handle'] for c in cluster[:5]}),
                'priority_sources': [c['post']['author_handle']
                                     for c in cluster if c['post'].get('is_news_account')],
                'detected_at':      datetime.now(timezone.utc).isoformat(),
                'strategy':         self.name,
                'status':           'breaking',
            })

        return new_events


# Expose threshold so ZeroShotStrategy can reference it
ZERO_SHOT_MIN_SCORE = 0.30   # matches nlp_enhancer.ZERO_SHOT_MIN_SCORE


# ─── Orchestrator ─────────────────────────────────────────────────────────────

class EventDetector:
    def __init__(self):
        self.strategies: List[DetectionStrategy] = [
            KeywordClusterStrategy(),
            VelocitySpikeStrategy(),
            ZeroShotStrategy(),     # Phase 4: auto-disabled when ST not installed
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