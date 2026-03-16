"""
Kalshi Feed Manager
Fetches all open Kalshi prediction markets once per day (at 00:00 UTC).
Persists to data/kalshi_markets.json so data survives server restarts.

Cache logic:
  - On startup: if cache exists and was written since today's 00:00 UTC → use it.
  - On startup: if cache is from a previous UTC day → pull immediately.
  - Daily background thread: pulls at next 00:00 UTC, then every 24h.

Matching engine (pure stdlib, no external NLP dependencies):
  - Token overlap (Jaccard on word sets) + character n-gram similarity.
  - Scores each market title against event titles / post text.
  - Threshold is configurable at query time (low / high confidence).
"""

import json
import os
import re
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
KALSHI_BASE        = 'https://api.elections.kalshi.com/trade-api/v2'
MARKETS_URL        = f'{KALSHI_BASE}/markets'
SERIES_URL         = f'{KALSHI_BASE}/series'
PAGE_LIMIT         = 1000          # max per Kalshi docs

# Series prefixes to exclude from the local market corpus entirely.
# These are auto-generated parlay/multi-leg series that produce hundreds of thousands
# of permutation markets with no meaningful semantic signal for event matching.
# Excluding them cuts corpus size from 648k → ~100k and eliminates noise matches.
_BLOCKED_SERIES_PREFIXES: Tuple[str, ...] = (
    'KXMVE',      # cross-category & multi-outcome parlays (546k markets alone)
)
DATA_DIR           = 'data'
CACHE_FILE         = os.path.join(DATA_DIR, 'kalshi_markets.json')
SERIES_CACHE_FILE  = os.path.join(DATA_DIR, 'kalshi_series.json')
DEBUG_FILE         = os.path.join(DATA_DIR, 'kalshi_sample.json')
SERIES_DEBUG_FILE  = os.path.join(DATA_DIR, 'kalshi_sample_series.json')

# Matching thresholds
THRESHOLD_LOW  = 0.15   # permissive — more matches, more noise
THRESHOLD_HIGH = 0.40   # conservative — fewer, higher-confidence matches

CATEGORIES = [
    'Politics', 'Sports', 'Culture', 'Crypto', 'Climate',
    'Economics', 'Mentions', 'Companies', 'Financial', 'Tech & Science',
]


# ── Text similarity (pure stdlib) ─────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """Lowercase, strip punctuation, split into words. Filter stopwords."""
    _STOP = {
        'a','an','the','is','are','was','were','be','been','being',
        'have','has','had','do','does','did','will','would','could',
        'should','may','might','shall','can','need','dare','ought',
        'and','but','or','nor','for','yet','so','in','on','at','to',
        'of','by','as','if','it','its','this','that','these','those',
        'with','from','into','about','over','after','before','between',
        'during','through','what','who','which','when','where','how',
        'not','no','nor','than','then','there','their','they','them',
        'he','she','we','you','i','me','my','your','our','his','her',
        'up','out','off','all','any','both','each','few','more','most',
        'other','some','such','only','own','same','too','very','just',
        'because','while','although','though','whether','either','neither',
    }
    words = re.findall(r'[a-zA-Z0-9]+', text.lower())
    return [w for w in words if w not in _STOP and len(w) > 1]


def _ngrams(tokens: List[str], n: int = 2) -> set:
    """Character bigrams across all tokens joined."""
    joined = ' '.join(tokens)
    return {joined[i:i+n] for i in range(len(joined) - n + 1)}


def _expand_tokens(text: str) -> frozenset:
    """Tokenize a string and return tokens + word bigrams as a frozenset.

    Word bigrams (adjacent token pairs) give meaningful phrase matching, e.g.
    "iran war", "oil prices". Character bigrams caused false positives because
    single-char bigrams like 'an', 'ar' match almost any text.
    """
    tokens = _tokenize(text)
    if not tokens:
        return frozenset()
    word_bigrams = frozenset(f"{tokens[i]} {tokens[i+1]}" for i in range(len(tokens) - 1))
    return frozenset(tokens) | word_bigrams


def _index_market_tokens(market: Dict) -> None:
    """Pre-compute and cache the token+bigram set on the market dict (in-place)."""
    if '_tok' not in market:
        title    = market.get('title', '') or ''
        subtitle = market.get('subtitle', '') or ''
        market['_tok'] = _expand_tokens(f"{title} {subtitle}")


def score_market_against_corpus(market: Dict, corpus_tokens: frozenset) -> float:
    """
    Score a pre-indexed market against a pre-built corpus frozenset.

    Uses market-side coverage: intersection / len(market_tokens).
    This measures "what fraction of this market's tokens appear in the news corpus",
    which is stable regardless of corpus size — unlike union-Jaccard which approaches
    zero as the corpus grows.  Call _index_market_tokens first.
    """
    mi = market.get('_tok')
    if not mi or not corpus_tokens:
        return 0.0
    return round(len(mi & corpus_tokens) / len(mi), 4)


# kept for backwards compat / external callers
def score_market_against_texts(market: Dict, texts: List[str]) -> float:
    """Slow per-text scoring. Use score_market_against_corpus for bulk scoring."""
    _index_market_tokens(market)
    corpus = frozenset().union(*(_expand_tokens(t) for t in texts if t))
    return score_market_against_corpus(market, corpus)


# ── Kalshi API fetch ──────────────────────────────────────────────────────────

def _fetch_all_open_markets() -> Tuple[List[Dict], Optional[str]]:
    """
    Pull all open markets from Kalshi using cursor pagination.
    Returns (markets_list, error_string_or_None).
    """
    import urllib.request
    import urllib.error
    import ssl

    # macOS Python ships without system certs; bypass SSL verification for Kalshi API
    _ssl_ctx = ssl.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode    = ssl.CERT_NONE

    markets = []
    cursor  = None
    page    = 0
    sample_logged = False

    while True:
        params = f'?limit={PAGE_LIMIT}&status=open'
        if cursor:
            params += f'&cursor={cursor}'
        url = MARKETS_URL + params

        # Retry up to 3 times on transient errors (timeout, connection reset)
        data = None
        last_err = None
        for attempt in range(3):
            try:
                req  = urllib.request.Request(url, headers={'Accept': 'application/json'})
                with urllib.request.urlopen(req, context=_ssl_ctx, timeout=60) as resp:
                    data = json.loads(resp.read().decode())
                last_err = None
                break
            except urllib.error.HTTPError as e:
                return markets, f'HTTP {e.code}: {e.reason}'  # don't retry HTTP errors
            except Exception as e:
                last_err = str(e)
                logger.warning(f'[Kalshi] Page {page} attempt {attempt+1} failed: {e} — retrying…')
                time.sleep(2 ** attempt)  # 1s, 2s, 4s back-off
        if data is None:
            # All retries exhausted — return what we have so far
            logger.error(f'[Kalshi] Pagination stopped at page {page}: {last_err}')
            return markets, last_err

        raw_batch = data.get('markets', [])
        # Filter out blocked parlay series — use raw_batch for pagination control
        filtered_batch = [
            m for m in raw_batch
            if not any(
                (m.get('series_ticker') or m.get('event_ticker') or m.get('ticker') or '').upper().startswith(pfx)
                for pfx in _BLOCKED_SERIES_PREFIXES
            )
        ]
        markets.extend(filtered_batch)
        page += 1

        # Log one full market object on first page for inspection
        if not sample_logged and filtered_batch:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(DEBUG_FILE, 'w') as f:
                json.dump(filtered_batch[0], f, indent=2)
            logger.info(f'[Kalshi] Sample market written to {DEBUG_FILE}')
            sample_logged = True

        cursor = data.get('cursor')
        if not cursor or not raw_batch:  # use raw_batch — filtered pages may be empty but more pages remain
            break

        logger.info(f'[Kalshi] Page {page}: {len(markets)} markets so far…')

    logger.info(f'[Kalshi] Fetched {len(markets)} open markets total.')
    return markets, None


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _today_midnight_utc() -> datetime:
    """Return today's 00:00:00 UTC."""
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _cache_is_fresh(path: str) -> bool:
    """True if the cache file was written at or after today's 00:00 UTC."""
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    return mtime >= _today_midnight_utc()


def _load_cache(path: str) -> Optional[List[Dict]]:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def _save_cache(path: str, data) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(path, 'w') as f:
        json.dump(data, f)


def _fetch_series() -> tuple:
    """Fetch the full series list from Kalshi (/series endpoint, no pagination)."""
    import urllib.request
    import ssl

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode    = ssl.CERT_NONE

    body = None
    last_exc = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(SERIES_URL, headers={'Accept': 'application/json'})
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=60) as resp:
                body = json.loads(resp.read().decode())
            break
        except Exception as exc:
            last_exc = exc
            logger.warning(f'[Kalshi] Series fetch attempt {attempt+1} failed: {exc}')
            if attempt < 2:
                time.sleep(2 ** attempt)

    if body is None:
        logger.error(f'[Kalshi] Series fetch failed after 3 attempts: {last_exc}')
        return [], str(last_exc)

    series = body.get('series', [])
    logger.info(f'[Kalshi] Fetched {len(series)} series entries.')

    if series:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(SERIES_DEBUG_FILE, 'w') as f:
            json.dump(series[0], f, indent=2)

    return series, None


def _build_series_prefix_index(series_tickers: list) -> list:
    """Sort series tickers longest-first so prefix matching finds the most specific match."""
    return sorted(series_tickers, key=len, reverse=True)


def _resolve_series_ticker(event_ticker: str, prefix_index: list) -> str:
    """Find the series ticker for a market by prefix-matching its event_ticker."""
    for st in prefix_index:
        if event_ticker == st or event_ticker.startswith(st + '-'):
            return st
    parts = event_ticker.rsplit('-', 1)
    return parts[0] if len(parts) == 2 else event_ticker


# ── Manager class ─────────────────────────────────────────────────────────────

# ── Category inference ────────────────────────────────────────────────────────
_CAT_LOOKUP_FEED = []
_CAT_RAW_FEED = [
    ('Politics',       ['SENATE','HOUSE','GOVPARTY','CONTROLH','CONTROLS','POWER',
                        'KXSENATE','KXHOUSE','KXGOV','KXPRES','KXTRUMP','KXBIDEN',
                        'KXKAMALA','KXELECT','KXVOTE','KXIMPEACH','KXPARDON',
                        'KXCONGRESS','KXSCOTUS','KXBALANCE','KXATTYGEN','KXSECSTATE',
                        'KXLTGOV','KXVPRES','KXNEXTSPEAKER','KXVETOCOUNT','KXVETOOVER',
                        'KXDRAINTHESWAMP','KXTERMLIMITS','KXLOSEMAJORITY','KXLOSEPRIMARY',
                        'KXLOSEREELECTION','KXRUNBYMIDTERM','KXHISTORICPACT','KXCLOSE',
                        'KXFIRSTPRIMARY','KXJOIN','KXLEAVE','KXMOV','KXEXPELL','KXCENSURE',
                        'KXFRENCH','KXUK','KXCANADA','KXAUSTRALIA','KXISRAEL',
                        'KXIRAN','KXGREECE','KXDENMARK','KXFINLAND','KXHUNGARY',
                        'KXPOLAND','KXSPAIN','KXITALY','KXTURKEY','KXNIGERIA',
                        'KXGHANA','KXKENYA','KXBRAZILPRES','KXBRPRES','KXARGEN',
                        'KXCOLOMBIA','KXPERU','KXVENEZUELA','KXMEXICODE','KXPHILIPPINES',
                        'KXMALAYSIA','KXTHAILAND','KXMOLDOVA','KXMONGOLIA','KXLATVIA',
                        'KXLEBANON','KXBULGARIA','KXSLOVAKIA','KXSLOVENIA','KXNEWZEALAND',
                        'KXALBERTA','KXQUEBEC','KXSCOTPARLI','KXWALESPARLI',
                        'KXGUATEMALA','KXPARAGUAY','KXDOMINICANREP','KXNEPAL','KXZAMBIA']),
    ('Economics',      ['KXCPI','KXPCE','KXGDP','KXFED','KXEFFR','KXRATECUT','KXLARGECUT',
                        'KXEMERCUTS','KXFEDDECISION','KXFOMCD','KXJOBLESS','KXPAYROLLS',
                        'KXHOUSING','KXMORTGAGE','KXNASDAQ','KXSP','KXBOND','KXDEBT',
                        'KXTARIFF','KXEFFTARIFF','KXNEWTARIFFS','KXADP','KXISMPMI',
                        'KXEHSALES','KXNHSALES','KXHOUSINGSTART','KXBUILDPERMS',
                        'KXRECESSION','KXRECSSNBER','KXINFLATION','KXHIGHINFLATION',
                        'KXCHINAUSGDP','KXGOLDMON','KXGOLDW','KXSILVERMON','KXSILVERW',
                        'KXCOPPERMON','KXWTI','KXBRENT','KXOIL','KXCREDITRATING',
                        'KXCREDITC','KXCORPTAXCUT','KXTAXWAIVE','KXIPO','KXBANKRUPTCY',
                        'KXBOEING','KXDOGE','KXGOVTSPEND','KXGOVTCUTS','KXUSDEBT',
                        'KXUSTYLD','KXUSDBRL','KXUSDIRR','KXUSDJPY','KXEURUSD',
                        'KXEGGS','KXCOSTCOHOTDOG','KXSUBWAY','KXNICKELSTOP','KXSTEAMPRICE',
                        'KXFTCNEXT','KXAMAZON','KXAPPLE','KXMETA','KXTESLA',
                        'KXTECHLAYOFF','KXCOMPANYACTION','KXEVSHARE','KXELECTRICM',
                        'KXQATARLNG','KXMEXCUBOIL','KXNORDSTREAM','KXTRADEDEFICIT',
                        'KXDEBTGROWTH','KXRECCOUNT','WRECSS','CHINAUSGDP',
                        'KXJPMCEO','KXAAPLCEO','KXOPENAICEO']),
    ('Crypto',         ['KXBTC','KXETH','KXDOGE','KXSOL','KXXRP','KXSHIBA','KXINX',
                        'KXZEC','KXCRYPTO','KXTOKENLAUNCH','KXBTCHALF','KXBTCRESERVE',
                        'KXBTCVSGOLD','KXFDV','KXTREASBLOCKCHAIN','KXCRYPTOPAY',
                        'KXCRYPTOCAPGAIN','KXKRAKENBANKPUBLIC','KXUPONLY']),
    ('Sports',         ['KXNBA','KXLEADERNBA','KXRECORDNBA','KXQUADRUPLED','KXNBAWINS',
                        'KXNBATEAM','KXNBAGAME','KXNBAPLAYOFF','KXNBAFINMVP','KXNBAMVP',
                        'KXNBAROY','KXNBADPOY','KXNBACLUTCH','KXNBADRAFT','KXNBAMIMP',
                        'KXNBACOY','KXNBASIXTH','KXNBALOTTERY','KXSHAI','KXLBJRETIRE',
                        'KXNFL','KXSUPERBOWL','KXSTARTINGQB','KXRECORDNFL','KXNFLDRAFT',
                        'KXNFLMVP','KXNFLOPOTY','KXNFLDPOTY','KXNFLPLAYOFF','KXNFLTRADE',
                        'KXNFLPRIME','KXBILLS','KXMLB','KXLEADERMLB','KXNEXTTEAMMLB',
                        'KXMLBGAME','KXMLBWINS','KXMLBWORLD','KXMLBPLAYOFFS','KXTEAMSINWS',
                        'KXNHL','KXNHLADAMS','KXNHLCALDER','KXNHLHART','KXNHLNORRIS',
                        'KXNHLPRES','KXNHLRICHARD','KXNHLROSS','KXNHLVEZINA',
                        'KXEPL','KXLALIGA','KXSERIEA','KXBUNDESLIGA','KXLIGUE','KXMLS',
                        'KXUCL','KXUEL','KXUECL','KXUCLW','KXFACUP','KXCOPADELREY',
                        'KXDFBPOKAL','KXLIGAMX','KXLIGAPORTUGAL','KXWC','KXPREMIERLEAGUE',
                        'KXFINALISSIMAGAME','KXCANADACUP','KXBRASILEIRO','KXDENSUPERLIGA',
                        'KXSUPERLIG','KXARGPREMDIV','KXMENSWORLD','KXBELGIANPL',
                        'KXEKSTRAKLASA','KXEREDIVISIE','KXKNVBCUP','KXEFLCHAMP','KXEFLCUP',
                        'KXSAUDIP','KXNRLCHAMP','KXWINSTREAKMANU','KXPGA','KXPGAR',
                        'KXPGAH','KXGOLF','KXLIVR','KXLIVTOP','KXDPWORLD','KXBRYSONCOURSE',
                        'KXPFAPOY','KXRYDERCUP','KXPGACURRY','KXPGAMAJOR','KXGOLFTENNISMAJORS',
                        'KXATP','KXWTA','KXGRANDSLAM','KXGRANDSLAMJF','KXSWISS','KXSIXNATIONS',
                        'KXUFC','KXBOXING','KXFLOYDTYSON','KXMCGREGOR','KXWBC','KXDIMAYORG',
                        'KXNCAAF','KXNCAAMB','KXNCAAWB','KXNCAABB','KXNCAAHOCKEY',
                        'KXMARMAD','KXWMARMAD','KXHEISMAN','KXCOACH','KXNASCAR','KXINDYCAR',
                        'KXWNBA','KXWNBADRAFT','KXNBAW','KXCBA','KXKBL','KXKHL','KXNBL',
                        'KXAFL','KXAHL','KXBBL','KXBSL','KXCOD','KXVALORANT','KXDOTA',
                        'KXLOL','KXCS','KXESVI','KXIPL','KXCRICKET','KXMWREG','KXBIGEAST',
                        'KXSPORTSOWNER','KXKLEAGUE','KXJBLEAGUE','KXDARTSMATCH','KXPREMDARTS',
                        'KXFIBACHAMP','KXFIBAECUP','KXEUROLEAGUE','KXRODMAN','KXTGLCHAMP',
                        'KXLAXTEWAR','KXNCAALAX','KXWSOPEN','KXMVENBASING','KXSOCCERTRANSFER',
                        'KXKELCERETIRE','KXARODGRETIRE','KXLIIGAGAME','KXSHLGAME',
                        'KXNBLGAME','KXVTBGAME','KXSSHIELD','KXAHLGAME','KXMVESPORTS']),
    ('Entertainment',  ['KXOSCARS','KXOSCAR','KXGRAMMYN','KXGRAMMY','KXBILLBOARD',
                        'KXNETFLIX','KXSPOTIFY','KXRANKL','KXTOPALBUM','KXTOPSONG',
                        'KXTOPARTIST','KXALBUM','KXSONGRELEASE','KXMOVIE','KXTVSEASON',
                        'KXTVSHOW','KXSNL','KXSNLHOST','KXAMERICANIDOL','KXTOPCHEF',
                        'KXTOPMODEL','KXSURVIVOR','KXBACHELOR','KXBIGBROTHER',
                        'KXDAILYSHOW','KXTHEMASK','KXNEXTLEVELCHEF','KXGAMEAWARDS',
                        'KXGAMERELEASE','KXGTA','KXFALLOUT','KXFASTANDFURIOUS',
                        'KXINTERSTELLAR','KXJUMANJI','KXTSAW','KXSTARWARS','KXMARVEL',
                        'KXDIRECTORMARVEL','KXLUCASFILM','KXMEDIARELEASE','KXMEDIAGUEST',
                        'KXMUSICALGUESTSNL','KXROGANGUEST','KXKXMEDIAGUEST','KXKXMOVIEDELAY',
                        'KXKXROLE','KXPERFORM','KXROLEIN','KXROLEATE','KXVENUEPERF',
                        'KXENGAGEMENT','KXMARRIAGE','KXBEYONCE','KXSWIFT','KXTAYLORSWIFT',
                        'KXDRAKE','KXTRAVIS','KXKANYE','KXKANYEISRAEL','KXPOPESWIFT',
                        'KXFEATURE','KXSPOTIFYW','KXSPOTIFYD','KXSPOTIFYARTIST',
                        'KXSPOTIFYALBUM','KXROLLINGSTONE','KXBALLONDOR','KXSEXYMAN',
                        'KXMETGALA','KXPODCAST','KXTWITCHSUBS','KXYTUBESUBS',
                        'KXFOLLOWERCOUNT','KXNETWORTH','BEYONCEGENRE','GTA','MOON',
                        'NYTOAI','OAIAGI','AUCTIONPRICETREY','SCOTREF',
                        'STARSHIPMARS','TESLACEOCHANGE','TESLAOPTIMUS',
                        'LEAVEPOWELL','JPMCEOCHANGE']),
    ('Tech & Science', ['KXAI','KXLLM','KXLLAMA','KXCLAUDE','KXOAI','KXGPT','KXGROK',
                        'KXBESTLLM','KXCODINGMODEL','KXTOPAI','KXTECHRANKL','KXSPACEX',
                        'KXSTARSHIP','KXNEWGLENN','KXMOON','KXMARS','KXCOLONIZE',
                        'KXROBOTMARS','KXELONMARS','KXBLUESPACEX','KXIPHONE','KXAIPLAUSE',
                        'KXAILEGIS','KXQUANTUM','KXFUSION','KXFDAAPPROVAL','KXFDATYPE',
                        'KXREACTOR','KXDATACENTER','KXDATASET','KXJENSEN','KXMETAHEADCOUNT',
                        'KXSNAPRESTRICT','KXSOCIALMEDIABAN','KXLIVENATION']),
    ('Climate',        ['KXWARMING','KXGTEMP','KXARCTICICE','KXSOLAR','KXEUCLIMATE',
                        'KXUSCLIMATE','KXINDIACLIMATE','KXEARTHQUAKE','KXTORNADO',
                        'KXHIGH','KXLOWT','KXRAIN','KXSNOW','KXTHAIL','KXMETEOR',
                        'KXERUPT','KXHMON','USCLIMATE','INDIACLIMATE','EUCLIMATE']),
]
for cat, prefixes in _CAT_RAW_FEED:
    for p in prefixes:
        _CAT_LOOKUP_FEED.append((p.upper(), cat))
_CAT_LOOKUP_FEED.sort(key=lambda x: -len(x[0]))

def _infer_category(series_ticker: str) -> str:
    st = (series_ticker or '').upper()
    for prefix, cat in _CAT_LOOKUP_FEED:
        if st.startswith(prefix):
            return cat
    return 'Other'


class KalshiFeedManager:
    def __init__(self):
        self._markets:  List[Dict]     = []
        self._series:   List[Dict]     = []   # raw series objects from /series
        self._cat_map:  Dict[str, str] = {}   # series_ticker → category
        self._lock    = threading.Lock()
        self.last_updated: Optional[str] = None
        self.status   = 'initializing'
        self._error:  Optional[str] = None

        # Background match scoring state
        self._match_lock    = threading.Lock()
        self._match_running = False
        self._pending_texts: List[str] = []
        self._match_cache:  Dict      = {}   # {markets, texts_len, scored_at}
        self._last_corpus_hash: int   = 0    # hash of last scored texts

        # Bootstrap: try cache first, pull immediately if stale
        if _cache_is_fresh(CACHE_FILE):
            cached = _load_cache(CACHE_FILE)
            if cached is not None:
                series_cached = _load_cache(SERIES_CACHE_FILE)

                if series_cached:
                    # Both caches fresh — stamp series_ticker + category and start
                    # Prune blocked parlay series from cache
                    before = len(cached)
                    cached = [m for m in cached if not any(
                        (m.get('series_ticker') or m.get('event_ticker') or m.get('ticker') or '').upper().startswith(pfx)
                        for pfx in _BLOCKED_SERIES_PREFIXES
                    )]
                    if len(cached) < before:
                        logger.info(f'[Kalshi] Pruned {before - len(cached):,} blocked-series markets from cache.')
                    cat_map      = {s.get('ticker',''): s.get('category','') for s in series_cached if s.get('ticker')}
                    prefix_index = _build_series_prefix_index(list(cat_map.keys()))
                    for m in cached:
                        if not m.get('series_ticker'):
                            et = m.get('event_ticker', '')
                            if et:
                                st = _resolve_series_ticker(et, prefix_index)
                                m['series_ticker'] = st
                                if not m.get('category') and st in cat_map:
                                    m['category'] = cat_map[st]
                        elif not m.get('category'):
                            st = m.get('series_ticker', '')
                            if st in cat_map:
                                m['category'] = cat_map[st]

                    # Pre-index tokens for fast semantic scoring
                    for m in cached:
                        _index_market_tokens(m)

                    self._markets     = cached
                    self._series      = series_cached
                    self._cat_map     = cat_map
                    self.last_updated = datetime.fromtimestamp(
                        os.path.getmtime(CACHE_FILE), tz=timezone.utc
                    ).isoformat()
                    self.status = 'ok'
                    logger.info(f'[Kalshi] Loaded {len(cached)} markets + {len(series_cached)} series from cache.')
                    self._start_daily_thread(pull_now=False)
                    return
                else:
                    # Markets cached but no series yet — serve markets immediately,
                    # fetch series in background to stamp categories.
                    logger.info('[Kalshi] Market cache found but no series cache — fetching series now.')
                    # Prune blocked parlay series from cache
                    before = len(cached)
                    cached = [m for m in cached if not any(
                        (m.get('series_ticker') or m.get('event_ticker') or m.get('ticker') or '').upper().startswith(pfx)
                        for pfx in _BLOCKED_SERIES_PREFIXES
                    )]
                    if len(cached) < before:
                        logger.info(f'[Kalshi] Pruned {before - len(cached):,} blocked-series markets from cache.')
                    self._markets     = cached
                    self.last_updated = datetime.fromtimestamp(
                        os.path.getmtime(CACHE_FILE), tz=timezone.utc
                    ).isoformat()
                    self.status = 'ok'

                    def _enrich_then_loop():
                        series_list, _ = _fetch_series()
                        if series_list:
                            cat_map    = {s.get('ticker',''): s.get('category','') for s in series_list if s.get('ticker')}
                            prefix_idx = _build_series_prefix_index(list(cat_map.keys()))
                            with self._lock:
                                stamped = 0
                                for m in self._markets:
                                    if not m.get('series_ticker'):
                                        et = m.get('event_ticker', '')
                                        if et:
                                            st = _resolve_series_ticker(et, prefix_idx)
                                            m['series_ticker'] = st
                                    st = m.get('series_ticker', '')
                                    if st and st in cat_map and not m.get('category'):
                                        m['category'] = cat_map[st]
                                        stamped += 1
                                self._series  = series_list
                                self._cat_map = cat_map
                            _save_cache(SERIES_CACHE_FILE, series_list)
                            logger.info(f'[Kalshi] Series enrichment done: {stamped} categories stamped.')
                        self._start_daily_thread(pull_now=False)

                    threading.Thread(target=_enrich_then_loop, daemon=True).start()
                    return

        # Cache missing or stale → pull everything now in background
        self.status = 'fetching'
        self._start_daily_thread(pull_now=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_markets(self) -> List[Dict]:
        with self._lock:
            return list(self._markets)

    def get_status(self) -> Dict:
        with self._lock:
            return {
                'status':       self.status,
                'count':        len(self._markets),
                'last_updated': self.last_updated,
                'error':        self._error,
            }

    def get_series(self) -> List[Dict]:
        """Return series list for the browse UI (ticker, title, category, frequency)."""
        with self._lock:
            return [
                {k: s.get(k, '') for k in ('ticker', 'title', 'category', 'frequency')}
                for s in self._series
            ]

    def force_refresh(self) -> Dict:
        """Trigger an immediate re-pull (blocking). Returns status dict."""
        self._pull()
        return self.get_status()

    def filter_markets(
        self,
        category:      Optional[str]  = None,
        series_ticker: Optional[str]  = None,
        event_ticker:  Optional[str]  = None,
        min_price:     float          = 0,
        max_price:     float          = 100,
        max_days:      Optional[float] = None,
        min_days:      Optional[float] = None,
    ) -> List[Dict]:
        """Return markets matching the given filters.
        Prices are in cents (0-100). Category is inferred from series_ticker if blank.
        """
        now = datetime.now(timezone.utc)
        results = []
        with self._lock:
            snapshot = list(self._markets)

        # Build prefix index once for series resolution
        with self._lock:
            cat_map      = dict(self._cat_map)
        prefix_index = _build_series_prefix_index(list(cat_map.keys())) if cat_map else []

        for m in snapshot:
            # Use stamped series_ticker or resolve from event_ticker
            raw_st = m.get('series_ticker') or ''
            if not raw_st:
                et = m.get('event_ticker', '') or m.get('ticker', '')
                if et and prefix_index:
                    raw_st = _resolve_series_ticker(et, prefix_index)
                elif et:
                    parts = et.rsplit('-', 1)
                    raw_st = parts[0] if len(parts) == 2 else et

            # Series filter
            if series_ticker:
                if raw_st.upper() != series_ticker.upper():
                    continue

            # Event filter
            if event_ticker:
                if (m.get('event_ticker', '') or '').upper() != event_ticker.upper():
                    continue

            # Category filter — use stamped category, then cat_map, then infer
            if category:
                inferred_cat = m.get('category') or cat_map.get(raw_st, '') or _infer_category(raw_st)
                if inferred_cat.lower() != category.lower():
                    continue

            # Price filter — prices are cents (0-100)
            # Try all price fields in priority order; convert dollar values to cents
            yes_price = None
            for field in ('yes_ask', 'last_price', 'yes_ask_dollars', 'last_price_dollars'):
                raw = m.get(field)
                if raw is not None:
                    try:
                        v = float(raw)
                        if v > 0:
                            yes_price = v
                            break
                    except (TypeError, ValueError):
                        pass
            if yes_price is None:
                yes_price = 0.0
            # Convert dollar values (0.0–1.0) to cents
            if 0 < yes_price <= 1.0:
                yes_price = yes_price * 100
            # Allow zero-priced markets through when min_price is 0
            if yes_price == 0 and min_price == 0:
                pass
            elif not (min_price <= yes_price <= max_price):
                continue

            # Time-remaining filter
            close_raw = m.get('close_time') or m.get('expiration_time')
            if close_raw:
                try:
                    close_dt  = datetime.fromisoformat(close_raw.replace('Z', '+00:00'))
                    days_left = (close_dt - now).total_seconds() / 86400
                    if min_days is not None and days_left < min_days:
                        continue
                    if max_days is not None and days_left > max_days:
                        continue
                except Exception:
                    pass

            results.append(m)
        return results

    # Bump this version string whenever the scoring formula changes, to
    # force a rescore and discard stale cached results.
    _SCORER_VERSION = 'v2-coverage'

    def update_match_corpus(self, texts: List[str]) -> None:
        """
        Schedule a background re-score with the given texts.
        Returns immediately — results available via get_match_results().
        Skips if texts are identical to last scored corpus.
        Drops duplicate requests if a score is already running.
        """
        h = hash((self._SCORER_VERSION, tuple(texts)))
        with self._match_lock:
            if h == self._last_corpus_hash and self._match_cache:
                return  # same corpus, cached results still valid
            self._pending_texts = list(texts)
            if self._match_running:
                return   # already running, it will pick up _pending_texts when done
        threading.Thread(target=self._run_match_loop, daemon=True).start()

    def get_match_results(self) -> Dict:
        """Return the most recently scored results (never blocks)."""
        with self._match_lock:
            return dict(self._match_cache)

    def _run_match_loop(self) -> None:
        """Background thread: consume pending texts, score, cache, repeat."""
        while True:
            with self._match_lock:
                texts = list(self._pending_texts)
                self._pending_texts = []
                if not texts:
                    self._match_running = False
                    return
                self._match_running = True

            # Score outside the lock — this is the expensive part
            scored = self._score_markets(texts)

            with self._match_lock:
                self._match_cache = {
                    'markets':   scored,
                    'texts_len': len(texts),
                    'scored_at': datetime.now(timezone.utc).isoformat(),
                }
                self._last_corpus_hash = hash(tuple(texts))
                # Loop back if more texts arrived while we were scoring
                if not self._pending_texts:
                    self._match_running = False
                    return

    def _score_markets(self, texts: List[str]) -> List[Dict]:
        """Score all markets against texts. Runs in background thread."""
        with self._lock:
            snapshot   = list(self._markets)
            cat_map    = dict(self._cat_map)

        prefix_index = _build_series_prefix_index(list(cat_map.keys())) if cat_map else []

        # Build unified corpus token set once — O(T) instead of O(N*T)
        corpus_tokens = frozenset().union(*(_expand_tokens(t) for t in texts if t))

        scored = []
        for m in snapshot:
            _index_market_tokens(m)   # no-op if already indexed
            score = score_market_against_corpus(m, corpus_tokens)
            if score >= THRESHOLD_LOW:
                entry = {k: v for k, v in m.items() if not k.startswith('_')}
                if not entry.get('series_ticker') and prefix_index:
                    et = entry.get('event_ticker', '')
                    if et:
                        entry['series_ticker'] = _resolve_series_ticker(et, prefix_index)
                if not entry.get('category'):
                    st = entry.get('series_ticker', '')
                    if st and st in cat_map:
                        entry['category'] = cat_map[st]
                    elif st:
                        entry['category'] = _infer_category(st)
                entry['_score'] = score
                scored.append(entry)

        scored.sort(key=lambda x: x['_score'], reverse=True)
        logger.info(f'[Kalshi] Match scored: {len(scored)} results from {len(snapshot)} markets, {len(texts)} texts.')
        return scored

    # kept for backwards compat — synchronous callers get cached results
    def match_markets(
        self,
        texts:     List[str],
        threshold: float = THRESHOLD_LOW,
        top_n:     int   = 50,
    ) -> List[Dict]:
        self.update_match_corpus(texts)           # kick off background rescore
        cached = self.get_match_results()
        results = [m for m in cached.get('markets', []) if m.get('_score', 0) >= threshold]
        return results[:top_n]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _pull(self) -> None:
        self.status = 'fetching'
        logger.info('[Kalshi] Pulling open markets…')
        markets, err = _fetch_all_open_markets()
        with self._lock:
            if err and not markets:
                self._error  = err
                self.status  = 'error'
                logger.error(f'[Kalshi] Pull failed: {err}')
                return
            # Sanity check: don't overwrite a healthy cache with a tiny partial result
            # (can happen if the API times out after only a few pages)
            existing_count = len(self._markets)
            if len(markets) < max(1000, existing_count // 2):
                logger.warning(
                    f'[Kalshi] Pull returned only {len(markets)} markets '
                    f'(had {existing_count}) — keeping existing data to avoid partial overwrite.'
                )
                self.status = 'ok'
                return
            self._markets     = markets
            self._error       = err  # partial error (some pages ok)
            self.last_updated = datetime.now(timezone.utc).isoformat()
            self.status       = 'ok'
        # Fetch series and stamp markets
        series_list, _ = _fetch_series()
        if series_list:
            cat_map    = {s.get('ticker',''): s.get('category','') for s in series_list if s.get('ticker')}
            prefix_idx = _build_series_prefix_index(list(cat_map.keys()))
            stamped = 0
            for m in markets:
                if not m.get('series_ticker'):
                    et = m.get('event_ticker', '')
                    if et:
                        st = _resolve_series_ticker(et, prefix_idx)
                        m['series_ticker'] = st
                if not m.get('category'):
                    st = m.get('series_ticker', '')
                    if st and st in cat_map:
                        m['category'] = cat_map[st]
                        stamped += 1
            with self._lock:
                self._series  = series_list
                self._cat_map = cat_map
            _save_cache(SERIES_CACHE_FILE, series_list)
            logger.info(f'[Kalshi] Stamped {stamped} markets with series/category.')

        # Pre-index tokens on all markets for fast semantic scoring
        logger.info(f'[Kalshi] Pre-indexing tokens for {len(markets)} markets…')
        for m in markets:
            _index_market_tokens(m)

        _save_cache(CACHE_FILE, markets)
        logger.info(f'[Kalshi] Cache saved: {len(markets)} markets.')

    def _start_daily_thread(self, pull_now: bool) -> None:
        t = threading.Thread(target=self._daily_loop, args=(pull_now,), daemon=True)
        t.start()

    def _daily_loop(self, pull_now: bool) -> None:
        if pull_now:
            self._pull()

        while True:
            # Sleep until next 00:00 UTC
            now      = datetime.now(timezone.utc)
            tomorrow = _today_midnight_utc() + timedelta(days=1)
            wait     = (tomorrow - now).total_seconds()
            logger.info(f'[Kalshi] Next pull in {wait/3600:.1f}h (at {tomorrow.isoformat()})')
            time.sleep(max(wait, 1))
            self._pull()