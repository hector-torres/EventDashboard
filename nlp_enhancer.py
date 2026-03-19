"""
nlp_enhancer.py — NLP enhancement layer for event_detector.py
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Phase 1  — Named Entity Recognition + Negation Detection
           spaCy (optional) or pure-Python regex fallback

Phase 3  — Semantic Event Deduplication
           sentence-transformers (optional) or TF-IDF hybrid fallback
           Catches paraphrases the stem-overlap approach misses:
           "Iran launches missiles" ≈ "Iranian missile strike" → suppress

Phase 4  — Zero-shot Category Classification
           Uses the same sentence-transformers model as Phase 3
           Embeds post text and measures similarity to category descriptions
           Catches breaking events that contain no keyword matches
           Disabled gracefully when sentence-transformers not installed

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INSTALL (optional but recommended):

    Phase 1 upgrade:
        pip install spacy
        python -m spacy download en_core_web_sm

    Phase 3 + 4:
        pip install sentence-transformers
        (model all-MiniLM-L6-v2 auto-downloads ~90MB on first use)

All features degrade gracefully when dependencies are missing.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import re
import logging
import threading
import time
from collections import deque
from typing import Optional, Tuple, List, Dict

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import scipy.sparse as sp

logger = logging.getLogger(__name__)

# ─── Optional dependency flags ────────────────────────────────────────────────
try:
    import spacy as _spacy_mod
    _SPACY_AVAILABLE = True
except ImportError:
    _SPACY_AVAILABLE = False

try:
    from sentence_transformers import SentenceTransformer as _ST
    _ST_AVAILABLE = True
except ImportError:
    _ST_AVAILABLE = False

# ══════════════════════════════════════════════════════════════════════════════
# SHARED VOCABULARY
# ══════════════════════════════════════════════════════════════════════════════

_NEG_BEFORE = frozenset([
    'no ', 'not ', 'no reports of', 'no evidence of', 'no sign of',
    "didn't", "doesn't", "don't", "hasn't", "haven't", "hadn't",
    "wasn't", "weren't", "wouldn't", "couldn't", "shouldn't",
    'false reports', 'false alarm', 'unconfirmed reports', 'unconfirmed',
    'rumor', 'rumours', 'rumored', 'alleged', 'allegedly',
    'denied any', 'denies any', 'dismiss',
])
_NEG_AFTER = frozenset([
    'not confirmed', 'not reported', 'not verified', 'not true',
    'denied', 'deny', 'denies', 'ruled out', 'debunked',
    'false alarm', 'no casualties', 'no injuries', 'no deaths',
    'later retracted', 'retracted', 'corrected',
])

_GEO_VERBS_HIGH = frozenset([
    'fires', 'fired', 'launches', 'launched', 'strikes', 'struck',
    'attacks', 'attacked', 'invades', 'invaded', 'deploys', 'deployed',
    'bombs', 'bombed', 'shells', 'shelled', 'intercepts', 'intercepted',
    'arrests', 'arrested', 'kills', 'killed', 'shoots', 'shot',
    'seizes', 'seized', 'threatens', 'withdraws', 'mobilizes', 'mobilized',
    'besieges', 'blockades', 'retaliates', 'retaliated',
    'resigns', 'resigned', 'impeaches', 'impeached', 'indicts', 'indicted',
    'assassinates', 'assassinated', 'overthrows', 'overthrown',
    'bans', 'banned', 'sanctions', 'expels', 'expelled',
    'devastates', 'devastated', 'destroys', 'destroyed', 'hits', 'slams',
])
_GEO_VERBS_MEDIUM = frozenset([
    'announces', 'announced', 'declares', 'declared', 'confirms', 'confirmed',
    'orders', 'ordered', 'signs', 'signed', 'imposes', 'imposed',
    'authorizes', 'authorized', 'vetoes', 'vetoed', 'warns', 'warned',
    'condemns', 'condemned', 'suspends', 'suspended', 'freezes', 'frozen',
    'raises', 'raised', 'cuts', 'cut', 'hikes', 'hiked', 'slashes', 'slashed',
    'defaults', 'collapses', 'collapsed', 'surges', 'surged', 'crashes', 'crashed',
])
_GEO_VERBS_BENIGN = frozenset([
    'meets', 'met', 'talks', 'spoke', 'visits', 'visited', 'says', 'said',
    'told', 'asked', 'noted', 'added', 'continued', 'discussed', 'attended',
    'joined', 'welcomed', 'called', 'tweeted', 'posted', 'shared',
])

_CAP_NOISE = frozenset([
    'The','A','An','And','Or','But','For','Nor','So','Yet',
    'In','On','At','By','To','Of','Up','As','Is','It','He',
    'She','We','I','My','His','Her','Their','Its','This','That',
    'Breaking','Just','New','Now','Report','Update','Source',
    'Via','Per','Says','Said','After','Before','During','While',
    'When','Where','How','Why','What','Which','Who',
    'Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday',
    'January','February','March','April','May','June','July',
    'August','September','October','November','December',
])
_SKIP_ACRONYMS = frozenset([
    'US','UK','EU','UN','IS','PM','AM','ET','UTC',
    'GMT','EST','PST','EDT','PDT','CDT','MDT','OK',
])

DEDUP_THRESHOLD_SEMANTIC = 0.75
DEDUP_THRESHOLD_TFIDF    = 0.40
DEDUP_WINDOW             = 50

ZERO_SHOT_CATEGORIES: Dict[str, str] = {
    'military_action':
        'military attack airstrike missile launch troops deployed war weapons fired '
        'armed forces navy army combat bombing drone strike artillery',
    'natural_disaster':
        'earthquake hurricane flood wildfire tsunami tornado natural catastrophe '
        'disaster storm surge landslide volcanic eruption',
    'economic_financial':
        'interest rates inflation federal reserve central bank market crash '
        'recession gdp currency stock market bond yield debt default',
    'political_government':
        'election president congress parliament legislation policy government '
        'official vote coup impeachment state department foreign affairs',
    'crime_security':
        'shooting murder arrest bombing attack explosion suspect crime '
        'killed dead hostage kidnapping terrorism mass casualty',
    'health_medical':
        'virus outbreak pandemic vaccine hospital disease infection '
        'public health medical emergency epidemic quarantine WHO',
    'technology_cyber':
        'cyberattack data breach hack ransomware technology company '
        'AI software network infrastructure outage',
}
ZERO_SHOT_MIN_CONFIDENCE = 0.32
ZERO_SHOT_MIN_SCORE      = 0.30


# ══════════════════════════════════════════════════════════════════════════════
# EMBEDDING ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class EmbeddingEngine:
    MODEL_NAME = 'all-MiniLM-L6-v2'

    def __init__(self):
        self._model          = None
        self._model_lock     = threading.Lock()
        self._loaded         = False
        self._mode           = 'tfidf'

        self._tfidf_vec      = None
        self._tfidf_vec_c    = None
        self._tfidf_matrix   = None
        self._tfidf_texts    = []

        self._event_buffer   = deque(maxlen=DEDUP_WINDOW)

        self._cat_embeddings = None
        self._cat_keys       = list(ZERO_SHOT_CATEGORIES.keys())

        threading.Thread(target=self._load_model, daemon=True,
                         name='nlp-embed-loader').start()

    def _load_model(self):
        if not _ST_AVAILABLE:
            logger.info('[EmbeddingEngine] sentence-transformers not installed '
                        '— using TF-IDF fallback. '
                        'Install: pip install sentence-transformers')
            self._loaded = True
            return
        try:
            with self._model_lock:
                logger.info(f'[EmbeddingEngine] loading {self.MODEL_NAME}...')
                t0 = time.time()
                self._model = _ST(self.MODEL_NAME)
                self._mode  = 'semantic'
                logger.info(f'[EmbeddingEngine] model loaded in {time.time()-t0:.1f}s')
            self._preembed_categories()
        except Exception as e:
            logger.warning(f'[EmbeddingEngine] model load failed: {e} — using TF-IDF')
        finally:
            self._loaded = True

    def _preembed_categories(self):
        if self._model is None:
            return
        try:
            descs = list(ZERO_SHOT_CATEGORIES.values())
            self._cat_embeddings = self._model.encode(
                descs, normalize_embeddings=True, show_progress_bar=False)
            logger.info(f'[EmbeddingEngine] {len(descs)} category embeddings ready')
        except Exception as e:
            logger.warning(f'[EmbeddingEngine] category pre-embed failed: {e}')

    def is_duplicate_event(self, title: str, topic_key: str) -> Tuple[bool, float]:
        if not self._loaded:
            return False, 0.0
        if self._mode == 'semantic':
            return self._dedup_semantic(title)
        return self._dedup_tfidf(title)

    def register_event(self, title: str, topic_key: str):
        if self._mode == 'semantic' and self._model is not None:
            try:
                emb = self._model.encode(
                    [title], normalize_embeddings=True, show_progress_bar=False)[0]
                self._event_buffer.append(
                    {'title': title, 'topic_key': topic_key, 'embedding': emb})
            except Exception:
                pass
        else:
            self._tfidf_texts.append(title)
            if len(self._tfidf_texts) > DEDUP_WINDOW:
                self._tfidf_texts = self._tfidf_texts[-DEDUP_WINDOW:]
            self._rebuild_tfidf()

    def _dedup_semantic(self, title: str) -> Tuple[bool, float]:
        if not self._event_buffer or self._model is None:
            return False, 0.0
        try:
            emb     = self._model.encode(
                [title], normalize_embeddings=True, show_progress_bar=False)[0]
            stored  = np.vstack([e['embedding'] for e in self._event_buffer])
            max_sim = float(np.max(stored @ emb))
            return max_sim >= DEDUP_THRESHOLD_SEMANTIC, max_sim
        except Exception as e:
            logger.debug(f'[EmbeddingEngine] dedup_semantic error: {e}')
            return False, 0.0

    def _dedup_tfidf(self, title: str) -> Tuple[bool, float]:
        if not self._tfidf_texts or self._tfidf_vec is None:
            return False, 0.0
        try:
            vw = self._tfidf_vec.transform([title])
            vc = self._tfidf_vec_c.transform([title])
            new_vec = sp.hstack([vw * 0.6, vc * 0.4])
            max_sim = float(np.max(cosine_similarity(new_vec, self._tfidf_matrix)))
            return max_sim >= DEDUP_THRESHOLD_TFIDF, max_sim
        except Exception:
            return False, 0.0

    def _rebuild_tfidf(self):
        if len(self._tfidf_texts) < 2:
            return
        try:
            vec_w = TfidfVectorizer(ngram_range=(1,2), sublinear_tf=True, min_df=1)
            vec_c = TfidfVectorizer(analyzer='char_wb', ngram_range=(3,5),
                                    sublinear_tf=True, min_df=1)
            mw = vec_w.fit_transform(self._tfidf_texts)
            mc = vec_c.fit_transform(self._tfidf_texts)
            self._tfidf_vec    = vec_w
            self._tfidf_vec_c  = vec_c
            self._tfidf_matrix = sp.hstack([mw[:-1] * 0.6, mc[:-1] * 0.4])
        except Exception as e:
            logger.debug(f'[EmbeddingEngine] tfidf rebuild: {e}')

    def classify_post(self, text: str) -> Optional[Tuple[str, float]]:
        if (self._mode != 'semantic' or self._model is None
                or self._cat_embeddings is None):
            return None
        try:
            emb   = self._model.encode(
                [text], normalize_embeddings=True, show_progress_bar=False)[0]
            sims  = self._cat_embeddings @ emb
            best  = int(np.argmax(sims))
            score = float(sims[best])
            if score >= ZERO_SHOT_MIN_CONFIDENCE:
                return self._cat_keys[best], score
            return None
        except Exception as e:
            logger.debug(f'[EmbeddingEngine] classify_post: {e}')
            return None

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def ready(self) -> bool:
        return self._loaded

    def describe(self) -> str:
        s = self._mode
        if self._mode == 'semantic' and self._cat_embeddings is not None:
            s += '+zero-shot'
        return f'EmbeddingEngine({s})'


# ══════════════════════════════════════════════════════════════════════════════
# NLP ENHANCER
# ══════════════════════════════════════════════════════════════════════════════

class NLPEnhancer:
    """
    Drop-in NLP layer for event_detector.py.
    Phase 1: NER + negation  (spaCy or regex)
    Phase 3: Semantic dedup  (sentence-transformers or TF-IDF)
    Phase 4: Zero-shot class (sentence-transformers only)
    """

    def __init__(self):
        self._nlp     = None
        self._p1_mode = 'regex'
        self._load_spacy()
        self._embed   = EmbeddingEngine()

    def _load_spacy(self):
        if not _SPACY_AVAILABLE:
            logger.info('[NLPEnhancer] spaCy not installed — using regex.')
            return
        for model in ('en_core_web_md', 'en_core_web_sm', 'en_core_web_lg'):
            try:
                self._nlp     = _spacy_mod.load(model, disable=['parser','lemmatizer'])
                self._p1_mode = 'spacy'
                logger.info(f'[NLPEnhancer] spaCy loaded: {model}')
                return
            except OSError:
                continue
        logger.warning('[NLPEnhancer] spaCy installed but no model found.')

    # ── Phase 1: Negation ─────────────────────────────────────────────────────

    def is_negated(self, text: str, keyword: str) -> bool:
        if self._p1_mode == 'spacy':
            return self._is_negated_spacy(text, keyword)
        return self._is_negated_regex(text, keyword)

    def _is_negated_spacy(self, text: str, keyword: str) -> bool:
        doc = self._nlp(text)
        kw_lower = keyword.lower()
        for token in doc:
            if kw_lower in token.text.lower():
                for t in [token] + list(token.ancestors):
                    if any(child.dep_ == 'neg' for child in t.children):
                        return True
                if any(child.dep_ == 'neg' for child in token.children):
                    return True
        return self._is_negated_regex(text, keyword)

    def _is_negated_regex(self, text: str, keyword: str) -> bool:
        tl  = text.lower()
        pos = tl.find(keyword.lower())
        if pos == -1:
            return False
        before = tl[max(0, pos-60): pos]
        after  = tl[pos: pos+60]
        return (any(n in before for n in _NEG_BEFORE) or
                any(n in after  for n in _NEG_AFTER))

    # ── Phase 1: Entity extraction ────────────────────────────────────────────

    def extract_entities(self, text: str) -> List[Dict]:
        if self._p1_mode == 'spacy':
            return self._extract_entities_spacy(text)
        return self._extract_entities_regex(text)

    def _extract_entities_spacy(self, text: str) -> List[Dict]:
        doc  = self._nlp(text)
        seen = set()
        out  = []
        for ent in doc.ents:
            if ent.text not in seen and len(ent.text) > 1:
                seen.add(ent.text)
                out.append({'text': ent.text, 'label': ent.label_})
        return out

    def _extract_entities_regex(self, text: str) -> List[Dict]:
        entities: List[Dict] = []
        seen: set = set()

        def add(s: str, label: str):
            s = s.strip()
            if s and s not in seen and len(s) >= 2:
                seen.add(s)
                entities.append({'text': s, 'label': label})

        for m in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text):
            p = m.group(1)
            if not any(w in _CAP_NOISE for w in p.split()):
                add(p, 'PROPER')

        for m in re.finditer(
                r'\b([A-Z][a-z]+(?:\s+(?:of|the|de|del|el|al)\s+[A-Z][a-z]+)+)\b', text):
            add(m.group(1), 'GPE')

        for m in re.finditer(r'\bthe\s+([A-Z][A-Za-z]{2,})\b', text, re.IGNORECASE):
            w = m.group(1)
            if re.match(r'^[A-Z]', w) and w not in _CAP_NOISE:
                add(w, 'ORG')

        first = text.split()[0] if text.split() else ''
        fc    = re.sub(r'[^A-Za-z]', '', first)
        if len(fc) >= 3 and re.match(r'^[A-Z][a-z]{2,}', fc) and fc not in _CAP_NOISE:
            add(fc, 'PERSON')

        for sent in re.split(r'(?<=[.!?:])\s+|\n', text):
            for word in sent.split()[1:]:
                clean = re.sub(r"[^A-Za-z'\-]", '', word)
                if (clean and len(clean) >= 3 and
                        re.match(r'^[A-Z][a-z]{2,}', clean) and
                        clean not in _CAP_NOISE and
                        not any(clean in e['text'] for e in entities)):
                    add(clean, 'PROPER')

        for m in re.finditer(r'\b([A-Z]{2,6})\b', text):
            a = m.group(1)
            if a not in _SKIP_ACRONYMS:
                add(a, 'ORG')

        return entities

    # ── Phase 1: Severity upgrade ─────────────────────────────────────────────

    def entity_severity_check(self, text: str,
                               base_severity: Optional[str],
                               base_keyword: Optional[str]
                               ) -> Tuple[Optional[str], Optional[str]]:
        entities = self.extract_entities(text)
        if not entities:
            return base_severity, base_keyword

        gpe  = [e['text'] for e in entities if e['label'] in ('GPE','NORP')]
        pers = [e['text'] for e in entities if e['label'] == 'PERSON']
        best = (gpe or pers or [e['text'] for e in entities])[0]

        words = set(re.findall(r'\b\w+\b', text.lower()))
        if words & _GEO_VERBS_BENIGN:
            return base_severity, base_keyword

        rank = {'CRITICAL':3,'HIGH':2,'MEDIUM':1,None:0}
        mh = words & _GEO_VERBS_HIGH
        mm = words & _GEO_VERBS_MEDIUM

        if mh and rank.get(base_severity, 0) < 2:
            return 'HIGH', f'ent:{best}+{next(iter(mh))}'
        if mm and rank.get(base_severity, 0) < 1:
            return 'MEDIUM', f'ent:{best}+{next(iter(mm))}'
        return base_severity, base_keyword


    # ── Phase 1: Historical reference detection (#3) ──────────────────────────

    # Past years that clearly mark a reference as historical rather than breaking
    _HISTORICAL_YEAR_RE = re.compile(
        r'\b(19\d{2}|200\d|201[0-9]|202[0-3])\b'
    )
    # Phrases that frame an event as past/retrospective
    _HISTORICAL_PHRASES = frozenset([
        'after the ', 'since the ', 'following the ', 'in the wake of the ',
        'anniversary of', 'years since', 'years after', 'months after',
        'remembering the', 'memorial for', 'marks the', 'marked the',
        'commemorat', 'look back at', 'looking back', 'flashback',
        'happened in', 'occurred in', 'took place in', 'was in ',
        'as it happened', 'on this day', 'this day in',
    ])

    def is_historical_reference(self, text: str, keyword: str) -> bool:
        """
        Return True if the post appears to be referencing a past event rather
        than reporting a breaking one.

        Two checks:
        1. The text contains an explicit year in the range 2000-2023 (i.e. not
           the current year) — e.g. "the October 2023 Hamas attack on Israel"
        2. The keyword appears in a retrospective framing phrase — e.g.
           "after the attack", "since the shooting", "anniversary of the crash"

        Both checks are deliberately conservative: we only suppress when the
        signal is clear, so we don't accidentally drop legitimate breaking news
        that happens to mention a past comparison event.
        """
        import datetime as _dt
        current_year = str(_dt.datetime.now().year)
        text_lower   = text.lower()

        # Check 1: contains an explicit past year near the keyword
        for m in self._HISTORICAL_YEAR_RE.finditer(text):
            year = m.group(1)
            if year == current_year:
                continue   # current year is fine
            # Only flag if the year appears within 80 chars of the keyword
            kw_pos = text_lower.find(keyword.lower())
            if kw_pos == -1:
                continue
            if abs(m.start() - kw_pos) <= 80:
                return True

        # Check 2: keyword appears in a retrospective phrase
        kw_lower = keyword.lower()
        for phrase in self._HISTORICAL_PHRASES:
            # Look for the phrase immediately before or containing the keyword
            idx = text_lower.find(phrase)
            if idx == -1:
                continue
            surrounding = text_lower[idx: idx + len(phrase) + len(kw_lower) + 10]
            if kw_lower in surrounding:
                return True

        return False

    # ── Phase 3: Semantic deduplication ───────────────────────────────────────

    def is_duplicate_event(self, title: str, topic_key: str) -> Tuple[bool, float]:
        """Check if title is semantically similar to a recent event."""
        return self._embed.is_duplicate_event(title, topic_key)

    def register_event(self, title: str, topic_key: str):
        """Register an accepted event in the dedup buffer."""
        self._embed.register_event(title, topic_key)

    # ── Phase 4: Zero-shot classification ─────────────────────────────────────

    def classify_post(self, text: str) -> Optional[Tuple[str, float]]:
        """Returns (category_key, confidence) or None."""
        return self._embed.classify_post(text)

    def zero_shot_enabled(self) -> bool:
        return (self._embed.mode == 'semantic' and
                self._embed._cat_embeddings is not None)

    # ── Status ────────────────────────────────────────────────────────────────

    @property
    def embedding_mode(self) -> str:
        return self._embed.mode

    def describe(self) -> str:
        return f'NLPEnhancer(p1={self._p1_mode}, p3/4={self._embed.describe()})'


# ── Module-level singleton ────────────────────────────────────────────────────
_enhancer: Optional[NLPEnhancer] = None

def get_enhancer() -> NLPEnhancer:
    global _enhancer
    if _enhancer is None:
        _enhancer = NLPEnhancer()
    return _enhancer