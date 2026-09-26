"""
parser.py
---------
Rule-based (tokenize + classify + regex/gazetteer) parser for messy,
hand-typed CRM addresses.

Design intent (see README.md for full reasoning):
  - No ML / external address-parsing library is used on purpose -- the
    dataset is too small and too irregular (local abbreviations, landmark
    directions, inconsistent word order) for a generic library or a trained
    model, and the task explicitly rewards *explainable* decisions.
  - Every extracted field is produced by a named rule, so every flag can say
    exactly which rule failed and why -- not just "could not parse".
  - The parser NEVER invents a value. If a rule does not find good evidence
    for a field, the field is left blank and (if it matters) contributes to
    the flag reason.
  - The code never looks at the `difficulty` column -- only the raw address
    string, exactly as it would need to on unseen data.

Pipeline per address:
  1. tokenize()   -> list of (text, start, end) tokens, punctuation-aware
  2. classify()   -> tag each token as WORD / CODE / STOP / BKW / SKW
  3. area/city/pincode are pulled out with small gazetteers + regex
  4. building/street are derived from the token classification using a
     handful of explicit, named rules (see extract_building / extract_street)
  5. a small set of flag rules decide whether the record is trustworthy
"""

import re
import difflib
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Gazetteers / vocabularies. Small and local to this dataset on purpose -- in
# production these would live in a config table, not in code (see README,
# "what changes at 20,000 addresses").
# ---------------------------------------------------------------------------

AREA_CANONICAL = [
    "Cuffe Parade", "Bandra West", "Lower Parel", "Worli",
    "Andheri West", "Andheri East", "Powai", "Chembur",
    "Nariman Point", "Santacruz West", "Jogeshwari West", "Mahim",
    "Churchgate", "Goregaon West", "Chandivali",
]

CITY_CANONICAL = ["Mumbai"]

# Marks a token as a strong, explicit building-type word.
BUILDING_KEYWORDS = {
    "tower", "towers", "apartment", "apartments", "apt", "heights",
    "chs", "chsl", "estate", "building", "bldg", "society", "splendor",
    "splendr", "horizon", "isle", "villa", "residency", "complex",
}

# Marks a token as a street-type word.
STREET_KEYWORDS = {
    "road", "rd", "street", "st", "marg", "lane", "gali", "nagar",
}

# Unit / floor / wing / landmark marker words. These never belong to a
# building or street name themselves; they *bound* where a name candidate
# starts or stops.
STOP_WORDS = {
    "flat", "no", "nos", "floor", "flo", "wing", "unit", "room",
    "opp", "near", "nr", "next", "to", "landmark",
}

# Pure filler words: grammatically fine, but meaningless on their own as a
# "name". If a candidate name is made up ONLY of filler words, treat it as
# no name at all.
FILLER_WORDS = {"of", "a", "an", "and"}

# Individual words pulled out of the area/city gazetteers (e.g. "Lower" and
# "Parel" out of "Lower Parel"). Once we know a word IS the area/city, it can
# never also be part of a building or street name, so it acts as a boundary
# just like a STOP word.
GAZETTEER_WORDS = set()
for _phrase in AREA_CANONICAL + CITY_CANONICAL:
    for _w in _phrase.lower().split():
        GAZETTEER_WORDS.add(_w)

# A company/business suffix mid-address (e.g. "Brightpath Technologies Pvt
# Ltd.") marks a business name, not a residential building -- treat it as a
# boundary too so it never gets folded into a building/street candidate.
COMPANY_WORDS = {"pvt", "ltd", "technologies", "limited", "llp"}

# A hyphenated/attached code with 3+ digits ("T2-1804", "B-4702",
# "C-2215-18") is essentially always a flat/wing/tower UNIT reference in
# this kind of CRM shorthand, never part of the building's own name -- unlike
# a bare digit ("1") or bare letter ("B"), which sometimes IS the name
# (e.g. "Harbour View 1 B Tower").
STRONG_CODE_RE = re.compile(r"^[a-z0-9]{1,3}-\d{3,}(-\d+)*[a-z]{0,2}$")

PINCODE_RE = re.compile(r"\b(\d{6})\b")
# A short numeric fragment right after the city, e.g. "Mumbai-71" -- reads
# like a truncated / colloquial pincode, NOT a valid one. Flagged rather
# than guessed (guessing the missing leading digits would be inventing data).
SHORT_PIN_RE = re.compile(r"mumbai\s*-?\s*(\d{1,3})\b", re.IGNORECASE)

TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[./'&\-][A-Za-z0-9]+)*")


@dataclass
class Token:
    text: str
    start: int
    end: int
    cls: str = "WORD"  # WORD, CODE, STOP, BKW, SKW


@dataclass
class ParsedAddress:
    id: str
    raw_address: str
    building: str = ""
    street: str = ""
    area: str = ""
    city: str = ""
    pincode: str = ""
    flag: bool = False
    reasons: list = field(default_factory=list)

    def reason_text(self):
        return "; ".join(self.reasons)


# ---------------------------------------------------------------------------
# Tokenization / classification
# ---------------------------------------------------------------------------

def tokenize(text):
    tokens = []
    for m in TOKEN_RE.finditer(text):
        tokens.append(Token(m.group(0), m.start(), m.end()))
    return tokens


def _looks_like_code(low):
    """Numeric / alnum unit-code tokens: '74', '104', '17c', 'b-4702',
    'a1203', 't2-1804', 'c-2215-18', or a bare single letter (block/wing
    letters like 'A', 'B', 'K')."""
    if re.fullmatch(r"\d+[a-z]{0,2}", low):
        return True
    if re.fullmatch(r"[a-z]{1,2}[-/]?\d+([a-z]{0,2}|(?:-\d+)*[a-z]{0,2})", low):
        return True
    if len(low) == 1 and low.isalpha():
        return True
    return False


def classify(tokens):
    for t in tokens:
        low = t.text.lower().strip(".")
        if low in STREET_KEYWORDS:
            t.cls = "SKW"
        elif low in BUILDING_KEYWORDS:
            t.cls = "BKW"
        elif low in STOP_WORDS or low in GAZETTEER_WORDS or low in COMPANY_WORDS:
            t.cls = "STOP"
        elif _looks_like_code(low):
            t.cls = "CODE"
        else:
            t.cls = "WORD"

    # Context-sensitive fix: a lone "rd"/"st"/"nd"/"th" straight after a
    # digit ("3 rd flo") is a split-up ordinal ("3rd"), not the street-type
    # word "Rd"/"St" -- reclassify it as a STOP (floor/unit marker) instead.
    for i, t in enumerate(tokens):
        low = t.text.lower().strip(".")
        if low in ("st", "nd", "rd", "th") and i > 0:
            prev_low = tokens[i - 1].text.lower()
            if tokens[i - 1].cls == "CODE" and prev_low.isdigit():
                t.cls = "STOP"
    return tokens


def _span_text(tokens_subset):
    """Rebuild a readable name from a run of tokens by rejoining their
    original text with single spaces -- avoids dragging along stray commas
    or other punctuation that sat between the tokens in the raw string."""
    if not tokens_subset:
        return ""
    return " ".join(t.text for t in tokens_subset).strip(" ,.-")


# ---------------------------------------------------------------------------
# Area / city / pincode
# ---------------------------------------------------------------------------

def extract_area(text, reasons):
    lowered = text.lower()
    for area in AREA_CANONICAL:
        pattern = re.escape(area).replace(r"\ ", r"\s+")
        m = re.search(pattern, lowered, re.IGNORECASE)
        if m:
            return area, m.span()

    words = re.findall(r"[A-Za-z]+", text)
    best = (None, 0.0, None)
    for n in (2, 3, 1):
        for i in range(len(words) - n + 1):
            chunk = " ".join(words[i:i + n])
            for area in AREA_CANONICAL:
                ratio = difflib.SequenceMatcher(None, chunk.lower(), area.lower()).ratio()
                if ratio > best[1]:
                    best = (area, ratio, chunk)
    if best[1] >= 0.82:
        m = re.search(re.escape(best[2]), text, re.IGNORECASE)
        if m:
            return best[0], m.span()
    reasons.append("area/locality not identifiable")
    return "", None


def extract_city(text, reasons):
    for city in CITY_CANONICAL:
        m = re.search(re.escape(city), text, re.IGNORECASE)
        if m:
            return city
    reasons.append("city missing")
    return ""


def extract_pincode(text, reasons):
    m = PINCODE_RE.search(text)
    if m:
        return m.group(1)
    short = SHORT_PIN_RE.search(text)
    if short:
        reasons.append(
            f"pincode incomplete/non-standard (found '{short.group(1)}', not a "
            "valid 6-digit PIN; not auto-completed to avoid inventing data)"
        )
        return ""
    reasons.append("pincode missing")
    return ""


# ---------------------------------------------------------------------------
# Building name
# ---------------------------------------------------------------------------

def _candidate_from_bkw(tokens, bkw_index):
    """Walk backward from a BKW token, collecting WORD/CODE tokens, stopping
    at a STOP/SKW/other-BKW token or the start of the token list. Leading
    "strong" numeric codes (flat/tower unit refs like 'T2-1804') are always
    trimmed off; a plain digit or single letter right next to the keyword is
    kept, since that IS sometimes part of the building's own name (e.g.
    'Harbour View 1 B Tower'). If nothing meaningful is left, the whole
    candidate is invalid (a bare keyword like 'Building' on its own is not a
    name)."""
    collected = []
    i = bkw_index - 1
    while i >= 0 and tokens[i].cls in ("WORD", "CODE"):
        collected.append(tokens[i])
        i -= 1
    stopped_by_stop = i >= 0 and tokens[i].cls == "STOP"
    collected.reverse()

    if stopped_by_stop:
        while collected and collected[0].cls == "CODE":
            collected.pop(0)
    while collected and collected[0].cls == "CODE" and STRONG_CODE_RE.match(collected[0].text.lower()):
        collected.pop(0)

    non_filler = [t for t in collected if t.text.lower() not in FILLER_WORDS]
    if not non_filler:
        return ""

    return _span_text(collected + [tokens[bkw_index]])


def _fallback_candidate(tokens):
    """No usable building-keyword. Skip a leading run of CODE/STOP tokens
    (unit/flat/wing prefixes), then take the following run of WORD tokens,
    stopping at the first STOP/SKW/CODE token."""
    i = 0
    while i < len(tokens) and tokens[i].cls in ("CODE", "STOP"):
        i += 1
    collected = []
    while i < len(tokens) and tokens[i].cls == "WORD":
        collected.append(tokens[i])
        i += 1
    return _span_text(collected)


def extract_building(text, reasons):
    tokens = classify(tokenize(text))
    bkw_indices = [i for i, t in enumerate(tokens) if t.cls == "BKW"]

    if bkw_indices:
        candidates = []
        for idx in bkw_indices:
            name = _candidate_from_bkw(tokens, idx)
            if name:
                candidates.append(name)
        # de-duplicate: drop any candidate that's a substring of another
        distinct = []
        for c in candidates:
            if not any(c.lower() in d.lower() or d.lower() in c.lower() for d in distinct):
                distinct.append(c)
        if len(distinct) >= 2:
            reasons.append(
                "multiple distinct building names found in one field "
                f"({' | '.join(distinct)}) -- looks like two addresses "
                "concatenated together"
            )
            return distinct[0]
        if distinct:
            return distinct[0]
        # every BKW occurrence collapsed to nothing (e.g. filler-only) --
        # fall through to the positional heuristic below

    candidate = _fallback_candidate(tokens)
    if not candidate:
        first_content = next((t for t in tokens if t.cls == "WORD"), None)
        if first_content and first_content.text.lower() in STOP_WORDS:
            reasons.append("no building name identifiable (only a landmark reference given)")
        else:
            reasons.append("no building name identifiable")
        return ""

    reasons.append(
        f"building name low-confidence: derived positionally ('{candidate}'), "
        "no recognised building-type keyword (Tower/CHS/Apartment/etc.) found"
    )
    return candidate


def detect_landmark_only(text, reasons):
    if re.search(r"landmark\s*:", text, re.IGNORECASE):
        reasons.append(
            "address relies on an explicit landmark reference rather than a "
            "verifiable building name/number"
        )


# ---------------------------------------------------------------------------
# Street
# ---------------------------------------------------------------------------

def extract_street(text, area_span):
    scope_text = text if not area_span else text[:area_span[0]]
    tokens = classify(tokenize(scope_text))
    skw_indices = [i for i, t in enumerate(tokens) if t.cls == "SKW"]
    if not skw_indices:
        return ""
    idx = skw_indices[0]
    collected = []
    i = idx - 1
    # word-only: a bare number/code right before "Road" is almost always a
    # building/plot number, not part of the street's own name.
    while i >= 0 and len(collected) < 2 and tokens[i].cls == "WORD":
        collected.append(tokens[i])
        i -= 1
    collected.reverse()
    if not collected:
        # a bare street-type keyword with no name in front of it ("...CHSL
        # Road, Mahim") isn't a usable street name on its own.
        return ""
    return _span_text(collected + [tokens[idx]])


# ---------------------------------------------------------------------------
# Sparse-address check
# ---------------------------------------------------------------------------

def detect_sparse_address(text, building, area, street, reasons):
    tokens = re.sub(r"[^A-Za-z]", " ", text).split()
    if not building and not area and not street and len(tokens) <= 2:
        reasons.append(
            "address too sparse to parse (only a number and a city name, no "
            "building/street/area information present)"
        )


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def parse_one(row_id, raw_address):
    reasons = []
    text = raw_address.strip()

    area, area_span = extract_area(text, reasons)
    city = extract_city(text, reasons)
    pincode = extract_pincode(text, reasons)
    building = extract_building(text, reasons)
    street = extract_street(text, area_span)

    detect_landmark_only(text, reasons)
    detect_sparse_address(text, building, area, street, reasons)

    result = ParsedAddress(
        id=row_id, raw_address=raw_address, building=building, street=street,
        area=area, city=city, pincode=pincode, reasons=reasons,
    )

    # --- Flagging rule (full rationale in README Q2) ---
    # Flag whenever a *load-bearing* field is missing/unreliable:
    #   - both pincode AND area are missing (no way to locate the record), OR
    #   - no building name could be identified at all, OR
    #   - two+ distinct building names were detected in one field, OR
    #   - the address is too sparse to mean anything, OR
    #   - the only "building" reference is a landmark, not a real name.
    # An optional field being blank (e.g. street) does NOT by itself flag a
    # record -- only the fields needed to actually locate/identify it do.
    hard_fail_markers = (
        "no building name identifiable",
        "multiple distinct building names",
        "too sparse to parse",
        "landmark reference rather than a verifiable building",
    )
    reasons_joined = " | ".join(reasons)
    missing_both_locators = (not pincode) and (not area)
    if missing_both_locators or any(m in reasons_joined for m in hard_fail_markers):
        result.flag = True

    return result


def parse_all(rows):
    """rows: iterable of (id, address) tuples. Deliberately ignores any other
    column (e.g. `difficulty`) that might be present in the source file."""
    return [parse_one(rid, addr) for rid, addr in rows]
