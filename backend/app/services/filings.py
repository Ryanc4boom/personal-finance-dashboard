"""Stage 1 of the framework: reading the prose in a 10-K and a DEF 14A.

**Everything in this module is a heuristic and the output says so.** Stages 2-4
read XBRL, where a number is a number the company tagged. There is no equivalent
here: "Item 1. Business" is a heading convention, not a schema, and beneficial
ownership tables are laid out however the filer's counsel felt that year. So
every result carries a `confidence`, the sentences it was drawn from, and the
URL of the filing it came from. A reader can check the evidence in one click,
and an `UNKNOWN` is returned rather than a guess when the evidence is thin.

Two parsing problems are worth naming because the obvious approach fails on
both:

**The table of contents matches every Item heading.** A naive search for
"Item 1. Business" finds the TOC link, and the text between it and the next TOC
entry is about forty characters. `_extract_section` collects *all* candidate
spans and keeps the longest, which discards TOC hits structurally rather than by
guessing at offsets or stripping the TOC first.

**Ownership percentages live in table cells, not sentences.** Stripping tags to
plain text puts the share count and the percentage in the same run of digits,
so "1,234,567" and "1.5" become indistinguishable neighbours. `html_to_text`
therefore preserves cell and row boundaries as `|` and newline, which keeps one
table row on one line and lets the percentage be found positionally.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

# Enough of a section to be worth analysing. Below this the extraction almost
# certainly grabbed a table-of-contents fragment.
MIN_SECTION_CHARS = 1_000

# Sentences longer than this are almost always un-split tables; quoting one as
# evidence would fill the UI with noise.
MAX_EVIDENCE_CHARS = 400

_SCRIPT_STYLE = re.compile(
    r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
_CELL_BREAK = re.compile(r"</\s*(td|th)\s*>", re.IGNORECASE)
_LINE_BREAK = re.compile(
    r"<\s*/?\s*(tr|p|div|br|li|h[1-6]|table)\b[^>]*>", re.IGNORECASE
)
_TAG = re.compile(r"<[^>]+>")
_NBSP = re.compile(r"[\u00a0\u2007\u202f]")
_SPACES = re.compile(r"[ \t]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")


def html_to_text(raw: str) -> str:
    """Filing HTML (or inline XBRL) to text, preserving table structure.

    Cell boundaries survive as `|` and row boundaries as newlines — see the
    module docstring for why that matters for ownership tables.
    """
    text = _SCRIPT_STYLE.sub(" ", raw)
    text = _CELL_BREAK.sub(" | ", text)
    text = _LINE_BREAK.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = _NBSP.sub(" ", text)
    text = _SPACES.sub(" ", text)
    text = _BLANK_LINES.sub("\n", text)
    return text.strip()


def _extract_section(
    text: str, start_pattern: re.Pattern[str], end_pattern: re.Pattern[str]
) -> str | None:
    """Text between the first `start` match and the next `end` match after it.

    Every start is tried and the longest resulting span wins, which is what
    rejects table-of-contents matches (their spans are tiny).
    """
    starts = [m.end() for m in start_pattern.finditer(text)]
    if not starts:
        return None

    best: str | None = None
    for start in starts:
        end_match = end_pattern.search(text, start)
        end = end_match.start() if end_match else len(text)
        candidate = text[start:end]
        if best is None or len(candidate) > len(best):
            best = candidate

    if best is None or len(best) < MIN_SECTION_CHARS:
        return None
    return best


def _spaced(title: str) -> str:
    """Regex for a heading word that may have whitespace anywhere inside it.

    Not paranoia. Microsoft's 10-K renders its heading with a styled drop cap,
    so once the tags are stripped the text reads `ITEM 1. B USINESS` — the word
    is split mid-token by markup that carried the styling. A literal match for
    "business" finds the table of contents and misses the actual section, which
    is how a company with a perfectly ordinary 10-K ends up reporting no
    business description at all.
    """
    words = title.split()
    return r"\s+".join(r"\s*".join(re.escape(c) for c in word) for word in words)


def _item_heading(number: str, title: str) -> re.Pattern[str]:
    """Matcher for an SEC item heading.

    The separator between number and title is anything filers use — period, em
    dash, colon, or nothing.
    """
    return re.compile(
        rf"item\s*{number}\s*[.\-—–:]?\s*{_spaced(title)}", re.IGNORECASE
    )


_ITEM_1 = _item_heading("1", "business")
_ITEM_1A = _item_heading("1A", "risk factors")
_ITEM_1B = _item_heading("1B", "unresolved staff comments")
_ITEM_2 = _item_heading("2", "propert")
_ITEM_3 = _item_heading("3", "legal proceedings")


def extract_business_and_risk(document_text: str) -> tuple[str | None, str | None]:
    """`(Item 1 Business, Item 1A Risk Factors)` from a 10-K.

    Item 1A ends at Item 1B when present and Item 2 otherwise — 1B is frequently
    a one-line "None." that some filers omit entirely.
    """
    item1 = _extract_section(document_text, _ITEM_1, _ITEM_1A)

    item1a = _extract_section(document_text, _ITEM_1A, _ITEM_1B)
    if item1a is None:
        item1a = _extract_section(document_text, _ITEM_1A, _ITEM_2)
    if item1a is None:
        item1a = _extract_section(document_text, _ITEM_1A, _ITEM_3)

    return item1, item1a


# --------------------------------------------------------------------------- #
# Business model: recurring vs one-off
# --------------------------------------------------------------------------- #

# Weighted because the signals are not equally diagnostic. "Annual recurring
# revenue" is a term of art that only a recurring-revenue business uses;
# "contract" appears in every 10-K ever filed.
_RECURRING_SIGNALS: tuple[tuple[str, int], ...] = (
    (r"annual recurring revenue|\bARR\b", 5),
    (r"recurring revenue", 5),
    (r"subscription (?:revenue|model|services|arrangements|fees)", 5),
    (r"software[- ]as[- ]a[- ]service|\bSaaS\b", 4),
    (r"net (?:dollar )?retention rate", 4),
    (r"renewal rate|renewal rates", 3),
    (r"remaining performance obligation", 3),
    (r"deferred revenue|contract liabilit", 2),
    (r"subscribers?\b", 2),
    (r"multi-?year (?:contracts|agreements|arrangements)", 2),
    (r"maintenance and support (?:revenue|contracts)", 2),
    (r"membership fees?|memberships", 2),
    (r"licen[cs]e (?:renewals|term)", 1),
    (r"backlog", 1),
)

# Includes physical-goods indicators, not just explicit one-off revenue
# language. Without them the two lists are not comparable in practice: filers
# describe recurring revenue in named terms of art but almost never write "our
# revenue is transactional", so a chip maker scores zero here while tripping a
# single subscription keyword and reads as a subscription business. Inventory,
# unit pricing and foundry capacity are what a one-off product business talks
# about instead. Weighted below the recurring terms of art, which are far more
# diagnostic when they do appear.
_TRANSACTIONAL_SIGNALS: tuple[tuple[str, int], ...] = (
    (r"point of sale|point-of-sale", 3),
    (r"per-?unit (?:pricing|basis)", 2),
    (r"upon (?:shipment|delivery)", 3),
    (r"when control (?:of the products? )?transfers", 2),
    (r"one-?time (?:sales|fees|purchase)", 3),
    (r"project-?based (?:revenue|work)", 3),
    (r"units? (?:sold|shipped)", 2),
    (r"cyclical(?:ity)? (?:demand|in demand|nature)", 2),
    (r"seasonal(?:ity)? (?:of|in) (?:our )?(?:sales|demand)", 1),
    (r"average selling price", 3),
    (r"finished goods inventor|inventor(?:y|ies) (?:levels|obsolescence|write-?down)", 2),
    (r"foundr(?:y|ies)|wafer|semiconductor fabrication", 2),
    (r"raw materials?", 2),
    (r"original equipment manufacturers?|\bOEMs?\b|\bODMs?\b", 2),
    (r"bottler|bottling|franchise(?:e|d) (?:bottlers|operations)", 2),
    (r"same-?store sales|comparable (?:store )?sales", 2),
)


@dataclass
class Evidence:
    """One quoted passage supporting a finding, with where it came from."""

    text: str
    matched: str


def _sentences(text: str) -> list[str]:
    """Split on sentence terminators and line breaks.

    Line breaks count as boundaries because a filing's table rows and bullet
    lists frequently carry no terminal punctuation at all.
    """
    parts = re.split(r"(?<=[.;!?])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _score_signals(
    text: str, signals: tuple[tuple[str, int], ...]
) -> tuple[int, list[Evidence]]:
    score = 0
    evidence: list[Evidence] = []
    for pattern, weight in signals:
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            continue
        score += weight
        sentence = _sentence_around(text, match.start())
        if sentence:
            evidence.append(Evidence(text=sentence, matched=match.group(0)))
    return score, evidence


def _sentence_around(text: str, index: int) -> str | None:
    start = max(text.rfind(".", 0, index), text.rfind("\n", 0, index)) + 1
    end_candidates = [
        pos for pos in (text.find(".", index), text.find("\n", index)) if pos != -1
    ]
    end = min(end_candidates) + 1 if end_candidates else len(text)
    sentence = _SPACES.sub(" ", text[start:end]).strip()
    if not sentence or len(sentence) > MAX_EVIDENCE_CHARS:
        return None
    return sentence


@dataclass
class BusinessModel:
    classification: str  # RECURRING | TRANSACTIONAL | MIXED | UNKNOWN
    confidence: str  # HIGH | MEDIUM | LOW
    recurring_score: int
    transactional_score: int
    evidence: list[Evidence] = field(default_factory=list)


def classify_business_model(item1: str | None, item1a: str | None) -> BusinessModel:
    """Recurring vs one-off revenue, from the language of Item 1 and 1A."""
    text = "\n".join(part for part in (item1, item1a) if part)
    if not text:
        return BusinessModel("UNKNOWN", "LOW", 0, 0, [])

    recurring, rec_evidence = _score_signals(text, _RECURRING_SIGNALS)
    transactional, txn_evidence = _score_signals(text, _TRANSACTIONAL_SIGNALS)

    total = recurring + transactional
    if total == 0:
        return BusinessModel("UNKNOWN", "LOW", 0, 0, [])

    lead = abs(recurring - transactional)
    # Dominance is measured as a *share* of the evidence, not as an absolute
    # gap. NVIDIA's Item 1 trips one subscription keyword and nothing else; on
    # an absolute-gap rule that lone hit is enough to label a company that sells
    # chips by the unit as a recurring-revenue business. Requiring both a
    # minimum body of evidence and proportional separation makes a thin signal
    # return UNKNOWN, which is the honest answer.
    if total < 4:
        classification, confidence = "UNKNOWN", "LOW"
    elif lead / total < 0.30:
        classification, confidence = "MIXED", "MEDIUM" if total >= 8 else "LOW"
    else:
        classification = "RECURRING" if recurring > transactional else "TRANSACTIONAL"
        if total >= 12:
            confidence = "HIGH"
        elif total >= 8:
            confidence = "MEDIUM"
        else:
            confidence = "LOW"

    evidence = (rec_evidence if recurring >= transactional else txn_evidence)[:4]
    return BusinessModel(
        classification=classification,
        confidence=confidence,
        recurring_score=recurring,
        transactional_score=transactional,
        evidence=evidence,
    )


# --------------------------------------------------------------------------- #
# Customer concentration
# --------------------------------------------------------------------------- #

_CUSTOMER_WORDS = re.compile(
    r"\b(customer|client|reseller|distributor|counterpart(?:y|ies)|purchaser)s?\b",
    re.IGNORECASE,
)
# Revenue only. Trade receivables are deliberately excluded: Apple's 10-K
# discloses one customer at 12% of *total trade receivables* and no customer at
# all above 10% of revenue. Treating the two as interchangeable reports Apple as
# customer-concentrated on revenue, which is false — receivables concentration
# is a working-capital fact, not a demand-dependency one.
_REVENUE_WORDS = re.compile(
    r"\b(revenue|revenues|net sales|total sales|net revenue|net operating revenues)\b",
    re.IGNORECASE,
)

# Geographic and segment disclosures use identical grammar to customer
# concentration — "customers headquartered outside the United States accounted
# for 48% of total revenue" satisfies every other test in this function and is
# not a single-customer fact at all. On NVIDIA it outranks the genuine 22%
# direct-customer disclosure and silently becomes the headline number.
_GEOGRAPHIC = re.compile(
    r"\b(headquartered|based in|located in|geograph\w*|region|regional|country|"
    r"countries|outside (?:of )?the united states|international|domestic|"
    r"united states|china|taiwan|europe|asia|americas|singapore)\b",
    re.IGNORECASE,
)

# Phrasing that unambiguously scopes a percentage to one customer. This is a
# *requirement* for a positive finding, not a ranking bonus: without it,
# "cost of revenue increased 44% driven by growing customer demand" satisfies
# customer-word + revenue-word + percentage and is reported as a 44%
# single-customer dependency. Proximity is not evidence; the grammar is.
_SINGLE_CUSTOMER = re.compile(
    r"\b(?:one|a|a single|our largest|the largest|our single largest|each)\s+"
    r"(?:direct\s+|end\s+|significant\s+|major\s+|individual\s+)?"
    r"(?:customer|client|reseller|distributor)\b"
    # "Customer A accounted for 15% of revenue" — the anonymised form used when
    # the filer does not name the counterparty.
    r"|\bcustomer\s+[A-Z1-9]\b",
    re.IGNORECASE,
)
# "no customer accounted for more than 10%" — the disclosure that concentration
# is absent. Must be detected explicitly, because the sentence contains both a
# customer word and a percentage and would otherwise read as a positive hit.
# "No bottlers or customers represented 10% or more..." — Coca-Cola puts a noun
# between "no" and "customers", so the gap has to be permitted rather than
# anchoring the customer word directly to "no".
_NEGATION = re.compile(
    r"\bno\s+(?:\w+\s+){0,3}?"
    r"(?:customer|client|reseller|distributor|bottler|end\s+customer)s?\b"
    r"|\bnone\s+of\s+(?:our|the)\s+(?:customers|clients)\b"
    r"|\bdid\s+not\s+(?:individually\s+)?(?:exceed|account\s+for|represent)\b",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")


@dataclass
class CustomerConcentration:
    status: str  # CONCENTRATED | DIVERSIFIED | UNKNOWN
    confidence: str
    # Largest single-customer share of revenue found, in basis points.
    max_customer_bps: int | None
    threshold_bps: int
    evidence: list[Evidence] = field(default_factory=list)


# Aggregates ("our ten largest customers accounted for 60%") are real
# disclosures but answer a different question than the framework asks.
_AGGREGATE = re.compile(
    r"\b(top|five|ten|three|two|four|several|combined|aggregate|together|"
    r"collectively)\b",
    re.IGNORECASE,
)
_DISTRIBUTIVE = re.compile(r"\b(each|individually|respectively)\b", re.IGNORECASE)


def _scan_concentration(text: str) -> tuple[list[tuple[int, str]], list[str]]:
    """`(positives, negatives)`, a positive being `(bps, sentence)`."""
    positives: list[tuple[int, str]] = []
    negatives: list[str] = []

    for sentence in _sentences(text):
        if not _CUSTOMER_WORDS.search(sentence) or not _REVENUE_WORDS.search(sentence):
            continue
        percents = _PERCENT.findall(sentence)
        if not percents:
            continue

        # Negations are collected even without single-customer grammar, because
        # "no customer represented 10% or more" is the disclosure of absence and
        # names no customer by construction.
        negated = bool(_NEGATION.search(sentence))
        if not negated and not _SINGLE_CUSTOMER.search(sentence):
            continue

        # A sentence about where customers are, not who they are.
        if _GEOGRAPHIC.search(sentence):
            continue
        if _AGGREGATE.search(sentence) and not _DISTRIBUTIVE.search(sentence):
            continue

        try:
            values = [Decimal(p) for p in percents]
        except InvalidOperation:
            continue
        # Above 90% is never one customer of a listed operating company; it is a
        # mis-scoped total that would otherwise become the headline number.
        values = [v for v in values if 0 < v <= 90]
        if not values:
            continue

        if negated:
            negatives.append(sentence)
        else:
            positives.append((int(max(values) * 100), sentence))

    return positives, negatives


def detect_customer_concentration(
    item1: str | None,
    item1a: str | None,
    full_text: str | None = None,
    threshold_bps: int = 1_000,
) -> CustomerConcentration:
    """Flag any single customer disclosed at more than `threshold_bps` of revenue.

    Reg S-K Item 101 requires disclosure of any customer above 10% of
    consolidated revenue, so when concentration exists the sentence is reliably
    *somewhere* in the 10-K. It is not reliably in Item 1: Apple states its
    position in the notes to the financial statements instead. Item 1 and 1A are
    searched first and a hit there is HIGH confidence; the rest of the document
    is the fallback at MEDIUM.
    """
    scoped = "\n".join(part for part in (item1, item1a) if part)

    for text, confidence in ((scoped, "HIGH"), (full_text or "", "MEDIUM")):
        if not text:
            continue
        positives, negatives = _scan_concentration(text)

        if positives:
            positives.sort(key=lambda row: -row[0])
            max_bps = positives[0][0]
            return CustomerConcentration(
                status="CONCENTRATED" if max_bps > threshold_bps else "DIVERSIFIED",
                confidence=confidence,
                max_customer_bps=max_bps,
                threshold_bps=threshold_bps,
                evidence=[
                    Evidence(text=s[:MAX_EVIDENCE_CHARS], matched=f"{bps / 100:.1f}%")
                    for bps, s in positives[:3]
                ],
            )

        if negatives:
            return CustomerConcentration(
                status="DIVERSIFIED",
                confidence=confidence,
                max_customer_bps=None,
                threshold_bps=threshold_bps,
                evidence=[
                    Evidence(text=s[:MAX_EVIDENCE_CHARS], matched="no single customer")
                    for s in negatives[:2]
                ],
            )

    # Silence is genuinely ambiguous: a filer with no customer above 10% is not
    # required to say anything at all. UNKNOWN, not DIVERSIFIED.
    return CustomerConcentration("UNKNOWN", "LOW", None, threshold_bps, [])


# --------------------------------------------------------------------------- #
# Insider ownership (DEF 14A)
# --------------------------------------------------------------------------- #

# The group-total row label. Anchored to the start of a line or a table cell
# because the identical phrase appears in the paragraph introducing the table
# ("...beneficially owned by each of our directors, all of our directors and
# executive officers as a group, and all known by us to be beneficial owners of
# more than 5% of our common stock"). Scanning forward from that prose match
# finds "5%" and reports the 5%-holder disclosure threshold as NVIDIA's insider
# ownership — a number that is both wrong and plausible.
_GROUP_ROW = re.compile(
    r"(?:^|\|)\s*"
    r"(?:all\s+)?(?:of\s+)?(?:our\s+)?(?:current\s+)?"
    r"(?:executive\s+officers\s+and\s+directors|directors\s+and\s+executive\s+officers|"
    r"directors,?\s+(?:director\s+)?nominees,?\s+and\s+executive\s+officers|"
    r"directors\s+and\s+officers|executive\s+officers\s+and\s+non-?employee\s+directors)"
    # "...of the Company as a group" — filler between the roles and the group
    # phrase is common enough to be worth allowing.
    r"(?:\s+of\s+(?:the\s+)?[\w&.\-]{1,20}(?:\s+[\w&.\-]{1,20}){0,2})?"
    r"\s+as\s+a\s+group"
    r"(?P<count>\s*\(\s*\d{1,3}\s*(?:persons|people|individuals)?\s*\))?",
    re.IGNORECASE | re.MULTILINE,
)
_COUNT_VALUE = re.compile(r"(\d{1,3})")
# How far past the label the percentage column can sit. Blank cells, the share
# count and footnote markers all sit between them.
_ROW_WINDOW_CHARS = 260

# A data cell holding a percentage: a bare number, optionally parenthesised or
# suffixed with %. Anchored, so it will not match inside the "* Less than 1%"
# footnote legend that follows the table — that legend is what makes a naive
# search report Salesforce's insiders at 1.00% instead of 3.5%.
_PCT_WITH_SIGN = re.compile(r"^\(?\s*(\d{1,3}(?:\.\d+)?)\s*\)?\s*%$")
# Same, without a % sign: many tables put the symbol in the column header only.
# A decimal point is required, because the bare integers in this column are
# usually superscript footnote references — Microsoft's row reads
# "2,279,620 | 13 | * ", where 13 is footnote 13, not 13% ownership.
_PCT_BARE = re.compile(r"^(\d{1,2}\.\d+)$")
# A share count: thousands-separated, optionally trailed by a footnote marker.
_SHARE_COUNT = re.compile(r"^([\d,]{4,})(?:\s*\(\d{1,2}\))?$")
# The near-universal footnote for "less than 1%". Must be the whole cell.
_STAR_ONLY = re.compile(r"^\*$")


def _read_ownership_cells(window: str) -> tuple[str, int | None, str] | None:
    """Walk the cells after the group label and return the ownership column.

    Cell-by-cell rather than a regex over the whole row, because the columns can
    only be told apart by their shape and their order: a share count is
    thousands-separated, a footnote marker is a bare small integer, and the
    percentage is either `%`-suffixed or a bare decimal that appears *after* the
    share count. Reading the row as flat text instead picks up the first
    number-like thing it finds, which is a footnote reference about as often as
    it is the answer.

    Returns `(status, bps, matched_text)`, or None when the row is unreadable.
    """
    seen_share_count = False

    for raw_cell in window.split("|"):
        cell = re.sub(r"\s+", " ", raw_cell).strip()
        if not cell:
            continue

        if _STAR_ONLY.match(cell):
            return ("BELOW_ONE_PERCENT", None, "* (less than 1%)")

        signed = _PCT_WITH_SIGN.match(cell)
        if signed:
            value = Decimal(signed.group(1))
            if 0 <= value <= 100:
                return ("FOUND", int(value * 100), f"{value}%")

        if _SHARE_COUNT.match(cell):
            seen_share_count = True
            continue

        if seen_share_count:
            bare = _PCT_BARE.match(cell)
            if bare:
                value = Decimal(bare.group(1))
                if 0 <= value <= 100:
                    return ("FOUND", int(value * 100), f"{value}%")

    return None


@dataclass
class InsiderOwnership:
    status: str  # FOUND | BELOW_ONE_PERCENT | UNKNOWN
    # Percentage held by all directors and executive officers as a group.
    group_bps: int | None
    group_person_count: int | None
    confidence: str
    evidence: list[Evidence] = field(default_factory=list)


def extract_insider_ownership(proxy_text: str) -> InsiderOwnership:
    """Directors-and-officers-as-a-group ownership from a DEF 14A.

    Every proxy carries a "Security Ownership of Certain Beneficial Owners and
    Management" table ending in a group total row, which is the single number
    the framework wants. The row is found by its label and the percentage read
    positionally from the rest of the row — see the module docstring.

    A `*` footnote marker in the percentage column means "less than 1%", which
    is a real answer and is reported as `BELOW_ONE_PERCENT` rather than as
    missing data.
    """
    if not proxy_text:
        return InsiderOwnership("UNKNOWN", None, None, "LOW", [])

    for require_count in (True, False):
        for match in _GROUP_ROW.finditer(proxy_text):
            count_group = match.group("count")
            if require_count and not count_group:
                continue

            person_count = None
            if count_group:
                digits = _COUNT_VALUE.search(count_group)
                person_count = int(digits.group(1)) if digits else None

            window = proxy_text[match.end() : match.end() + _ROW_WINDOW_CHARS]
            # Collapse newlines: this is one row to a reader, but cell markup
            # has left it spread over a dozen lines.
            row = re.sub(r"\s+", " ", match.group(0) + window).strip(" |")

            outcome = _read_ownership_cells(window)
            if outcome is None:
                continue

            status, bps, matched = outcome
            return InsiderOwnership(
                status=status,
                group_bps=bps,
                group_person_count=person_count,
                confidence="HIGH" if person_count is not None else "MEDIUM",
                evidence=[Evidence(text=row[:MAX_EVIDENCE_CHARS], matched=matched)],
            )

    return InsiderOwnership("UNKNOWN", None, None, "LOW", [])
