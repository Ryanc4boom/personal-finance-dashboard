"""Merchant normalisation — layer 2's input.

Bank descriptors are hostile. The same coffee shop arrives as::

    SQ *BLUE BOTTLE COFFEE 0421 OAKLAND CA
    TST* BLUE BOTTLE COFFEE
    BLUE BOTTLE COFFEE #12  SAN FRANCISCO CA

All three must collapse to one `normalized_key` so they categorise together and
aggregate into one merchant in reports.

Two different outputs are produced from the same raw string, deliberately:

* `normalized_key` is aggressive and machine-facing. It strips every digit run,
  every processor prefix and any trailing city/state, because those are the
  tokens that *vary* between sightings of the same merchant. It is never shown
  to the user, so losing the "7" in "7-ELEVEN" costs nothing.
* `display_name` is conservative and human-facing. It keeps the words and their
  order, stripping only the noise that is unambiguously not part of the name.

Order of operations matters: processor prefixes are removed *before* punctuation
is flattened, because the prefixes are defined by their punctuation (`SQ *`).
"""

import logging
import re

from sqlalchemy import select, text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Merchant

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Processor / network noise
# --------------------------------------------------------------------------- #

# Payment-processor prefixes. Matched at the start of the string or after a
# separator, and always anchored so "SQUARE" is not eaten by the "SQ" rule.
_PROCESSOR_PREFIX = re.compile(
    r"""(?:^|\s)(?:
          SQ\s*\*          # Square
        | TST\s*\*         # Toast
        | PP\s*\*          # PayPal
        | SP\s*\*          # Shopify / Stripe-hosted storefronts
        | PY\s*\*
        | IC\s*\*
        | WPY\s*\*
        | PAYPAL\s*\*?
        | VISA\s+DDA\s+PUR
        | POS\s+DEBIT
        | POS\s+PURCHASE
        | DEBIT\s+CARD\s+PURCHASE
        | CHECKCARD
        | ACH\s+(?:DEBIT|CREDIT|PMT|PAYMENT|TRANSFER)?
        | RECURRING\s+PAYMENT
        | PREAUTHORIZED\s+(?:DEBIT|CREDIT)
        | ELECTRONIC\s+(?:DEBIT|CREDIT)
        | PURCHASE\s+AUTHORIZED\s+ON
    )\s*""",
    re.VERBOSE,
)

# Card-network / settlement words that carry no merchant identity.
_NETWORK_NOISE = re.compile(
    r"\b(?:VISA|MASTERCARD|MC|AMEX|DISCOVER|DEBIT|CREDIT|PURCHASE|PAYMENT|"
    r"PENDING|AUTH|TRANSACTION|MERCHANT|USA|US)\b"
)

# Store / terminal / reference numbers. Handled before generic digit runs so the
# marker token ("STORE", "#", "TERM") is removed along with its number.
_STORE_NUMBER = re.compile(
    r"\b(?:STORE|STR|ST#|TERM|TERMINAL|REF|ID|LOC|UNIT|#)\s*[#-]?\s*\d+\b|#\s*\d+"
)

_PUNCT = re.compile(r"[^A-Z0-9 ]+")
_DIGIT_TOKEN = re.compile(r"\b\d+\b")
_LONG_DIGIT_TOKEN = re.compile(r"\b\d{3,}\b")
_TRAILING_DIGITS = re.compile(r"(?:\s+\d+)+$")
# Alphanumeric junk like "X1234", "T-2841", "AB12CD" — noise, not a name.
_ALNUM_JUNK = re.compile(r"\b(?=\w*\d)(?=\w*[A-Z])\w{4,}\b")
_WHITESPACE = re.compile(r"\s+")

US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU",
}

# Trailing-city stripping is list-driven on purpose. The alternative — "drop the
# N tokens before the state code" — mangles real names ("SHELL OIL SAN JOSE CA"
# would become "SHELL OIL SAN"). A bounded list of the cities that actually show
# up in card descriptors is less clever and much safer. Multi-word entries are
# matched longest-first.
US_CITIES = {
    "NEW YORK", "LOS ANGELES", "SAN FRANCISCO", "SAN JOSE", "SAN DIEGO",
    "SANTA CLARA", "SANTA MONICA", "SANTA BARBARA", "SAN ANTONIO", "SAN MATEO",
    "SALT LAKE CITY", "KANSAS CITY", "OKLAHOMA CITY", "LAS VEGAS", "NEW ORLEANS",
    "LONG BEACH", "VIRGINIA BEACH", "COLORADO SPRINGS", "FORT WORTH",
    "FORT LAUDERDALE", "ST LOUIS", "ST PAUL", "ST PETERSBURG", "WASHINGTON DC",
    "MOUNTAIN VIEW", "PALO ALTO", "MENLO PARK", "REDWOOD CITY", "DALY CITY",
    "UNION CITY", "FOSTER CITY", "CULVER CITY", "JERSEY CITY", "SIOUX FALLS",
    "GRAND RAPIDS", "DES MOINES", "BATON ROUGE", "CHULA VISTA", "CHAPEL HILL",
    "ANN ARBOR", "BOCA RATON", "CORAL GABLES", "WEST PALM", "SAN RAFAEL",
    "WALNUT CREEK", "SANTA ROSA", "SAN BRUNO", "SOUTH SAN FRAN", "EL SEGUNDO",
    "NEWPORT BEACH", "HUNTINGTON BEACH", "MANHATTAN BEACH", "BROOKLYN",
    "QUEENS", "BRONX", "STATEN ISLAND", "CAMBRIDGE", "SOMERVILLE", "BERKELEY",
    "OAKLAND", "SEATTLE", "PORTLAND", "DENVER", "AUSTIN", "DALLAS", "HOUSTON",
    "PHOENIX", "TUCSON", "CHICAGO", "BOSTON", "ATLANTA", "MIAMI", "ORLANDO",
    "TAMPA", "NASHVILLE", "MEMPHIS", "DETROIT", "MINNEAPOLIS", "MILWAUKEE",
    "CLEVELAND", "COLUMBUS", "CINCINNATI", "PITTSBURGH", "PHILADELPHIA",
    "BALTIMORE", "RICHMOND", "CHARLOTTE", "RALEIGH", "JACKSONVILLE",
    "SACRAMENTO", "FRESNO", "BAKERSFIELD", "ANAHEIM", "IRVINE", "PASADENA",
    "GLENDALE", "BURBANK", "TORRANCE", "STOCKTON", "MODESTO", "SUNNYVALE",
    "CUPERTINO", "FREMONT", "HAYWARD", "MILPITAS", "CAMPBELL", "SARATOGA",
    "LOS GATOS", "BOULDER", "AURORA", "ARLINGTON", "ALEXANDRIA", "BETHESDA",
    "ROCKVILLE", "SILVER SPRING", "HERNDON", "RESTON", "PLANO", "IRVING",
    "FRISCO", "SUGAR LAND", "THE WOODLANDS", "SCOTTSDALE", "TEMPE", "MESA",
    "CHANDLER", "GILBERT", "HENDERSON", "RENO", "BOISE", "SPOKANE", "TACOMA",
    "BELLEVUE", "REDMOND", "KIRKLAND", "EVERETT", "OLYMPIA", "EUGENE", "SALEM",
    "HONOLULU", "ANCHORAGE", "OMAHA", "WICHITA", "TULSA", "LITTLE ROCK",
    "BIRMINGHAM", "MONTGOMERY", "MOBILE", "JACKSON", "SHREVEPORT", "LEXINGTON",
    "LOUISVILLE", "KNOXVILLE", "CHATTANOOGA", "GREENSBORO", "DURHAM",
    "WINSTON SALEM", "COLUMBIA", "CHARLESTON", "SAVANNAH", "AUGUSTA", "MACON",
    "TALLAHASSEE", "GAINESVILLE", "SARASOTA", "NAPLES", "CLEARWATER",
    "HOLLYWOOD", "HIALEAH", "PEMBROKE PINES", "CAPE CORAL", "PORT ST LUCIE",
    "SPRINGFIELD", "PEORIA", "ROCKFORD", "NAPERVILLE", "EVANSTON", "SCHAUMBURG",
    "MADISON", "GREEN BAY", "TOLEDO", "AKRON", "DAYTON", "FORT WAYNE",
    "INDIANAPOLIS", "SOUTH BEND", "LANSING", "FLINT", "TRENTON", "NEWARK",
    "PATERSON", "HOBOKEN", "PRINCETON", "STAMFORD", "HARTFORD", "NEW HAVEN",
    "BRIDGEPORT", "PROVIDENCE", "WORCESTER", "SPRINGFIELD MA", "MANCHESTER",
    "PORTLAND ME", "BURLINGTON", "ALBANY", "BUFFALO", "ROCHESTER", "SYRACUSE",
    "YONKERS", "WHITE PLAINS", "NEW ROCHELLE", "MOUNT VERNON", "SCARSDALE",
}

_MAX_CITY_WORDS = max(len(c.split()) for c in US_CITIES)


def _strip_trailing_location(text: str) -> str:
    """Remove a trailing "<CITY> <ST>" / "<ST>" / "<CITY>" tail.

    Only ever strips from the end, and never strips the string down to nothing —
    a descriptor that is *only* a location keeps its tokens rather than
    normalising to the empty key (which would merge every such row together).
    """
    tokens = text.split()

    if tokens and tokens[-1] in US_STATES and len(tokens) > 1:
        tokens = tokens[:-1]

    # Longest city match wins, so "SAN JOSE" beats a bare "SAN".
    for span in range(min(_MAX_CITY_WORDS, len(tokens) - 1), 0, -1):
        candidate = " ".join(tokens[-span:])
        if candidate in US_CITIES:
            tokens = tokens[:-span]
            break

    return " ".join(tokens) if tokens else text


def normalized_key(raw: str | None) -> str:
    """Machine-facing merchant key. Stable across processor and location noise."""
    if not raw:
        return ""

    text = raw.upper()
    text = _PROCESSOR_PREFIX.sub(" ", text)
    text = _STORE_NUMBER.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()

    text = _ALNUM_JUNK.sub(" ", text)
    text = _DIGIT_TOKEN.sub(" ", text)
    text = _NETWORK_NOISE.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()

    text = _strip_trailing_location(text)
    text = _WHITESPACE.sub(" ", text).strip()

    # Everything was noise (e.g. "POS DEBIT 4471"). Fall back to the punctuation
    # -stripped form so distinct descriptors do not all collapse onto "".
    if not text:
        text = _WHITESPACE.sub(" ", _PUNCT.sub(" ", raw.upper())).strip()

    return text[:255]


def display_name(raw: str | None, provider_merchant_name: str | None = None) -> str:
    """Human-facing merchant label.

    Plaid's own `merchant_name` is already clean when present, so it wins.
    Otherwise fall back to a light clean of the descriptor that keeps word order
    and only drops unambiguous noise.
    """
    if provider_merchant_name and provider_merchant_name.strip():
        return provider_merchant_name.strip()[:255]

    if not raw:
        return "(unknown merchant)"

    text = raw.upper()
    text = _PROCESSOR_PREFIX.sub(" ", text)
    text = _STORE_NUMBER.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    text = _ALNUM_JUNK.sub(" ", text)
    # Unlike the key, short numbers survive here so "7 Eleven" and "76" keep the
    # digits that are genuinely part of the name. Long runs are always store or
    # terminal numbers, and any trailing run is too.
    text = _LONG_DIGIT_TOKEN.sub(" ", text)
    text = _TRAILING_DIGITS.sub("", text.strip())
    text = _WHITESPACE.sub(" ", text).strip()
    text = _strip_trailing_location(text)
    text = _WHITESPACE.sub(" ", text).strip()

    # Nothing recognisable survived (e.g. "POS DEBIT 4471"). A label of "4471"
    # is worse than the untouched descriptor.
    if not text or text.isdigit():
        return raw.strip()[:255] or "(unknown merchant)"

    return _title_case(text)[:255]


_LOWER_WORDS = {"AND", "OF", "THE", "FOR", "AT", "IN", "ON", "TO", "A", "AN"}
_KEEP_UPPER = {"USA", "US", "LLC", "INC", "ATM", "DMV", "IRS", "AT&T", "CVS", "BP"}


def _title_case(text: str) -> str:
    words = text.split()
    out = []
    for i, word in enumerate(words):
        if word in _KEEP_UPPER:
            out.append(word)
        elif i > 0 and word in _LOWER_WORDS:
            out.append(word.lower())
        else:
            out.append(word.capitalize())
    return " ".join(out)


# --------------------------------------------------------------------------- #
# Merchant lookup & deduplication
# --------------------------------------------------------------------------- #

# Trigram score above which two keys are treated as the same merchant. Set high
# on purpose: a wrong merge silently misfiles every future transaction for both
# merchants and the user has no obvious way to notice. 0.88 only catches
# near-identical strings ("TRADER JOES" / "TRADER JOE S"), not merely similar
# ones ("SHELL" / "SHELLS", "CHEVRON" / "CHEVROLET").
AUTO_LINK_THRESHOLD = 0.88
MIN_KEY_LENGTH_FOR_FUZZY = 6


def _first_token(value: str) -> str:
    parts = value.split()
    return parts[0] if parts else ""


def find_similar_merchant(db: Session, key: str) -> tuple[Merchant, int] | None:
    """Best canonical merchant for `key` above the auto-link threshold.

    Only canonical rows (`canonical_merchant_id IS NULL`) are candidates, so
    aliases never chain. Returns the merchant plus its score in permille.
    """
    if len(key) < MIN_KEY_LENGTH_FOR_FUZZY:
        return None

    row = db.execute(
        sql_text(
            """
            SELECT id, normalized_key, similarity(normalized_key, :key) AS score
            FROM merchant
            WHERE canonical_merchant_id IS NULL
              AND similarity(normalized_key, :key) >= :threshold
            ORDER BY score DESC, normalized_key ASC
            LIMIT 1
            """
        ),
        {"key": key, "threshold": AUTO_LINK_THRESHOLD},
    ).first()

    if row is None:
        return None

    # Trigram similarity is bag-of-characters and has no notion of word order,
    # so it rates "OIL SHELL" and "SHELL OIL" identically. Requiring the leading
    # word to agree is a cheap guard against that class of false merge.
    if _first_token(row.normalized_key) != _first_token(key):
        return None

    merchant = db.get(Merchant, row.id)
    if merchant is None:
        return None
    return merchant, int(round(float(row.score) * 1000))


def canonical_of(merchant: Merchant | None, db: Session) -> Merchant | None:
    """Follow the single alias hop to the merchant that owns the defaults."""
    if merchant is None:
        return None
    if merchant.canonical_merchant_id is None:
        return merchant
    return db.get(Merchant, merchant.canonical_merchant_id) or merchant


def resolve_merchant(
    db: Session,
    description_raw: str | None,
    provider_merchant_name: str | None = None,
) -> tuple[Merchant | None, str]:
    """Map a raw descriptor to a merchant row, creating one if needed.

    Returns `(merchant, normalized_key)`. The key is returned even when no
    merchant could be derived so the caller can still cache it on the
    transaction for debugging.
    """
    key = normalized_key(provider_merchant_name or description_raw)
    if not key:
        return None, ""

    existing = db.scalar(select(Merchant).where(Merchant.normalized_key == key))
    if existing is not None:
        return existing, key

    label = display_name(description_raw, provider_merchant_name)
    similar = find_similar_merchant(db, key)

    merchant = Merchant(
        normalized_key=key,
        display_name=label,
        canonical_merchant_id=similar[0].id if similar else None,
        link_method="TRIGRAM" if similar else "EXACT",
        link_score=similar[1] if similar else None,
    )
    db.add(merchant)

    try:
        # Nested transaction so a concurrent sync that inserted the same key
        # first loses only this insert, not the whole page of work around it.
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.expunge(merchant)
        raced = db.scalar(select(Merchant).where(Merchant.normalized_key == key))
        if raced is None:
            raise
        return raced, key

    return merchant, key
