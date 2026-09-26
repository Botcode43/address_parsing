# Address Parsing — Messy CRM Addresses

Rule-based (regex + gazetteer + a small tokenizer) parser that splits 30
hand-typed CRM address strings into **building, street, area, city,
pincode**, and flags the ones it can't parse reliably with a specific reason.

## Why rule-based, not a library or a model

The brief says outright that no library solves this, and that's borne out
in practice: general address-parsing libraries (e.g. `libpostal`) are built
for standardised, structured formats and don't have prior exposure to
Mumbai-specific abbreviations, colloquial floor/wing shorthand, or
landmark-based directions. A trained model is a non-starter with 30 rows —
there's nothing to train on. What this task actually rewards is **judgment**
(knowing what you don't know) and **explainability** (saying exactly why a
field is missing), and a hand-written rule engine is the only approach where
every decision traces back to a named rule you can point to. That also
matches the brief's instruction not to overengineer: this is regex and a
tokenizer, not a pipeline.

## How it works

1. **Tokenize** the raw string into words/codes, keeping attached
   punctuation (`R.K`, `T2-1804`, `18St`).
2. **Classify** each token as:
   - `BKW` — an explicit building-type word (Tower, CHS, Apartment, Heights, Estate, Building, Society, Horizon, Isle, Villa, Residency, Complex, …)
   - `SKW` — a street-type word (Road, Street, Marg, Lane, Gali, Nagar, …)
   - `STOP` — a unit/floor/wing/landmark marker (Flat, Floor, Wing, No, Opp, Near, Landmark, …), plus any word that is itself part of a recognised area/city name, plus company-suffix words (Pvt, Ltd, Technologies) — these can never be part of a building/street name, they only bound where one starts or stops
   - `CODE` — a flat/unit/tower number (`74`, `17C`, `B-4702`, a bare single letter like a wing code)
   - `WORD` — everything else (candidate name content)
3. **Pincode / city / area** are pulled out with a 6-digit regex, a fixed
   city list, and a small area gazetteer with typo-tolerant fuzzy matching
   (`cuff parade` → `Cuffe Parade`, `Jogeshwar West` → `Jogeshwari West`).
4. **Building name**: if a `BKW` token exists, walk backward from it
   collecting `WORD`/`CODE` tokens until a `STOP`/another-`BKW` boundary,
   trimming off leading unit-style codes (`T2-1804`, `B-4702`) but *keeping*
   a bare digit/letter that's genuinely part of the name (`Harbour View 1 B
   Tower`). If there's no usable keyword, fall back to the first run of
   plain words after skipping any leading unit-number/marker prefix.
5. **Street**: the first `SKW` token, plus up to two preceding `WORD`
   tokens.
6. **Flag + reason**: see Q2 below.

Run it:
```bash
cd address_parsing
python src/main.py
# or explicitly:
python src/main.py --input data/addresses.csv --output output/parsed_addresses.csv
```
No third-party packages required (Python 3.8+, standard library only —
`re`, `difflib`, `csv`, `dataclasses`, `argparse`).

## Files

```
address_parsing/
├── README.md                     <- this file
├── data/
│   └── addresses.csv             <- input (as given)
├── src/
│   ├── parser.py                 <- all extraction/flagging logic
│   └── main.py                   <- reads CSV, runs parser, writes CSV
└── output/
    └── parsed_addresses.csv      <- generated output (5 fields + flag + reason)
```

---

## Q1. What should the correct output be for each address?

Columns below are exactly what the script outputs. "Confidence / notes"
is my own judgment call, for the cases where I'm not 100% sure the script's
answer is the *best possible* answer a human would give.

| ID | Building | Street | Area | Pincode | Flag | Confidence / notes |
|----|----------|--------|------|---------|------|----|
| A01 | Crest Tower.K | — | Cuffe Parade | 400005 | No | High. "Opp Harbour View.3" is a landmark, correctly excluded from building. |
| A02 | 304-Sameer tower | Hill crest road | Bandra West | 400050 | No | High. |
| A03 | Harbour View 1 B Tower | — | Cuffe Parade | 400005 | No | High — the tower number and wing letter are genuinely part of the identity here, and the parser keeps them. |
| A04 | Aurora Kalpa-Park | — | Lower Parel | 400013 | No | Medium. No generic keyword (Tower/CHS/etc.) matched "Aurora Kalpa-Park", so it's a positional guess, but it's the only sensible reading. |
| A05 | Harbour View | — | Cuffe Parade | 400005 | No | Medium. Same building family as A03; here the "1" trailing digit ends up part of the low-confidence phrase rather than the keyword-anchored one because there's no explicit "Tower" token before the unit info. A human would likely write "Harbour View 1". |
| A06 | Marigold Apartment | R.K Bhosle Road | Cuffe Parade | 400005 | No | High. |
| A07 | Crest Tower | — | Cuffe Parade | 400005 | No | High for "Crest Tower"; the wing letter "A" that follows the keyword is not captured (see Q1 limitations below). |
| A08 | Crest Tower.A | — | Cuffe Parade | 400005 | No | High. |
| A09 | Nirvana Apt | — | Worli | 400030 | No | High — flat number/wing letter correctly stripped from in front of the building name. |
| A10 | Nirvana IC | — | Lower Parel | 400009 | No | Medium. "IC" is likely a wing/tower code that's part of the building's own identity, not stripped, which I think is correct here since it sits directly next to "Nirvana" with no marker word between them. |
| B01 | Emerald Heights | JVN Nagar | Andheri West | 400053 | No | High. "MHADA" (a housing-scheme identifier) is correctly dropped — it's neither the building name nor the street. |
| B02 | Anjali CHS | Anand Nagar | Chembur | — | No | Pincode: "Mumbai-71" is a colloquial short form, not a real 6-digit PIN — left blank rather than guessed as "400071". |
| B03 | Oakridge Main | Oakridge Main St | Powai | 400076 | No | Low-medium. No keyword found "Oakridge" as a building name, so the code can't cleanly separate the society name ("Oakridge") from the street it's on ("Main St[reet]") — the boundary between the two is genuinely ambiguous from the text alone. A human would probably say building=Oakridge, street=Main Street. |
| B04 | Sarita Building | — | Nariman Point | 400021 | No | High. |
| B05 | Orchid Forest | Orchid Forest Street | Powai | 400076 | No | Low-medium, same "building name vs. street name" ambiguity as B03 — "Orchid Forest Street" reads as the building's own street address inside the Hiranandani complex. |
| B06 | MAJESTIC HE | — | Goregaon West | 400104 | No | Low. "HE" is almost certainly a truncated abbreviation (likely "Heights"), but I chose not to expand it — that would be inventing a value the CRM never actually recorded. |
| B07 | Skyline Sunrise | Koli Gali | Andheri East | 400093 | No | Medium — no explicit keyword, positional guess, but a clean one. |
| B08 | raheja Horizon | — | Powai | 400072 | No | High — correctly resolves through "Flat no. 902 k wing" to the real name "Raheja Horizon" (also a real, well-known Powai complex, which is a nice sanity check, though the parser has no knowledge of that — it's purely from the token pattern). |
| B09 | Nalanda Tapovan Caves | Tapovan Caves Road | Andheri East | 400093 | No | Low-medium. Same building-vs-street ambiguity: "Nalanda Tapovan" is very likely the society name and "Caves Road" the street, but there's no delimiter in the text to prove where one ends and the other begins. |
| B10 | Sea Havan | Harbur Road | — (none) | 400005 | No | Medium-high. "Building No. 6" is a generic in-complex block reference, correctly not folded into the name. No area/locality is actually stated in this address at all — that's a genuine gap in the source, not a parsing failure, so the field is correctly left blank; the record isn't flagged because building+street+pincode are all otherwise solid. |
| C01 | — | — | — | — | **Yes** | Correct. "47, mumbai" is a bare number and a city — there is no building, street, area, or pincode information in it at all. |
| C02 | — | — | Worli | 400013 | **Yes** | Correct. Area and pincode are fine, but there's no building/unit information whatsoever — nothing to hand a technician. |
| C03 | — | — | Chandivali | 400072 | **Yes** | Correct, and deliberately so: "D wing", "Bldg no. 31" and "MHADA" are all *generic* identifiers (a wing letter, a numbered block, a housing-scheme acronym), not a proper name. I chose to flag rather than output "Bldg" or "MHADA" as if either were the building's name. |
| C04 | C 12 Building | — | Powai | 400076 | No | Judgment call, could go either way. "Building No 3" (inside IIT Campus) is also a generic numbered reference like C03, but here it IS still a specific, locatable identifier ("Building No. 3 in the IIT Powai campus") rather than a bare scheme name, so I let it through as low-confidence rather than hard-flagging it. A stricter rule could flag this one too — see Q2. |
| C05 | Everst Crys Keshavbaug | Crys Keshavbaug Lane | Santacruz West | — | No | Low confidence and missing pincode, but not flagged — see Q2 for why a missing pincode alone doesn't trigger a full flag when building+area are usable. Building/street boundary is fuzzy here for the same reason as B03/B05/B09. |
| C06 | Harbour View.1 | — | Cuffe Parade | 400005 | No | High, same family as A03/A05. |
| C07 | K R Sapphire isle | — | Powai | — | **Yes** | Correct and the flagship case: the code detects **two distinct building names** ("K R Sapphire isle" and "Silverline Garden Estate") separated by an unrelated company name ("Brightpath Technologies Pvt Ltd"), meaning two different addresses/references are concatenated into one field. Reported both, along with why. |
| C08 | vasnt splendr | — | Jogeshwari West | — | No | Medium — building keyword "splendr" matched despite the typo. Street "jvlr" (JVLR — a real, well-known Mumbai road) is not recognised and left blank rather than guessed, since it's not in the street-keyword gazetteer. |
| C09 | Nova Kiran CHSL | Road (dropped — see note) | Mahim | 400016 | **Yes** | Flagged because the address explicitly relies on a "Landmark:" reference ("Frosty Cones Ice Cream Lane") rather than a verifiable building number — even though a real building name ("Nova Kiran CHSL") IS present and correctly extracted, the presence of a landmark-only qualifier means part of the record can't be trusted at face value. |
| C10 | The Lighthouse Rani Tara | Rani Tara road | Churchgate | — | No | Low confidence, same building/street boundary ambiguity as B03/B05/B09/C05 ("The Lighthouse" vs. "Rani Tara Road"), and missing pincode. Not flagged — see Q2. |

**Two things I'd flag to a human reviewer even though the code doesn't
force a hard flag on them:** C04's "Building No. 3" and C08/B02's missing
pincodes are borderline. Both are defensible either way, and I've called
out the specific rule that decides each in the table above and in Q2.

**Known limitation, called out honestly rather than hidden:** when there's
no explicit building-type keyword (Tower/CHS/Apartment/etc.) *and* a street
keyword follows shortly after (B03, B05, B09, C05, C10), the code cannot
reliably tell where the building/society name ends and the street name
begins, because the source text has no delimiter marking that boundary.
It reports its best-effort split for both fields rather than guessing which
words belong where — this is exactly the kind of case the brief says
"cannot be parsed reliably," even though it happened to land in the
medium/hard groups rather than being uniformly one or the other.

### Extra fields I'd add (and why)

The brief asks for 5 fields + flag + reason; I output exactly that, but if
this were going into production I'd add:

- **`unit_number`** (flat/floor/wing, e.g. "Flat 208, Wing B") — currently
  this information is detected and *discarded* as noise so it doesn't
  contaminate the building name. But it's real, useful data (a technician
  still needs the flat number!) and should be its own field rather than
  thrown away.
- **`confidence` (high/medium/low)** — right now confidence is implied by
  whether a "low-confidence: derived positionally" reason string is
  present. A dedicated column would make it queryable/sortable without
  parsing the reason text.
- **`normalized_raw`** — the CRM already has typos in area names; a
  cleaned-up, standardized version of the *whole* original string (not just
  the area) would help a human reviewer spot-check faster.

I did **not** add a "confidence score" (0–100, say) because a fake-precise
number would overstate how principled the scoring is — a three-level
high/medium/low bucket is honest about the actual granularity of the
evidence.

---

## Q2. What rule decides whether an address is flagged?

A record is flagged (`flag = YES`) when a **load-bearing** field — one
needed to actually locate and identify the property — is missing or
unreliable. Concretely, any one of:

1. **No building name could be identified at all** (C01, C02, C03). Without
   *some* identifier for the specific unit, a technician has nothing to go
   on regardless of how good the area/pincode are.
2. **Two or more distinct building names detected in one field** (C07) —
   a strong sign that two separate addresses/references got typed into a
   single CRM entry.
3. **The address is too sparse to mean anything** — just a number and a
   city (C01).
4. **The only "building" reference is an explicit landmark** ("Landmark:
   Frosty Cones...", C09) rather than a verifiable name/number — a landmark
   can move, close, or be subjective, so it isn't a reliable identifier on
   its own.
5. **Both pincode AND area are missing** — if neither locator is present,
   there's no way to place the record geographically at all, regardless of
   how good the building name is.

**What does *not* trigger a flag by itself:** a missing street, a missing
pincode *alone* (with area still present), or a low-confidence/positionally
-derived building name. These get a specific reason recorded in the
`reason` column (so nothing is silently dropped), but the record is still
usable — e.g. C05, C08, C10 are missing a pincode but still have a usable
building + area, and in practice a technician (or a follow-up call to the
customer) could resolve them. I designed the `reason` column and the `flag`
column to carry different signal on purpose: **`reason` records every piece
of uncertainty, `flag` is reserved for "a human needs to review this before
it's usable."** Rolling every uncertainty into the flag would drown out the
genuinely unusable records among a much larger set of merely-imperfect
ones.

This is a judgment call, and I'd happily make the rule stricter (e.g. flag
on ANY missing pincode, which would also catch C04, C05, C08, C10) if the
company's operational reality is "we cannot dispatch a technician without
a pincode, full stop." That's a one-line change to `parse_one()` in
`parser.py` — I've written the rule as a short, named list specifically so
it's easy to see and adjust.

---

## Q3. What would change at 20,000 addresses instead of 30?

- **Gazetteers move out of code and into data.** `AREA_CANONICAL`,
  `BUILDING_KEYWORDS`, etc. are Python lists right now because 30 rows
  don't justify anything else. At 20,000 rows spanning many
  neighbourhoods/cities, these need to be a proper reference table (a
  DB table or a maintained CSV) — likely sourced from India Post's official
  pincode/locality data — so it can be updated without touching code, and
  so it covers areas this sample never saw.
- **Confidence thresholds need to be tuned on labeled data, not by eye.**
  The fuzzy-match threshold (0.82) and the handful of hand-picked rules
  here were tuned by reading 30 addresses. At scale, I'd hold out a
  labeled sample, measure precision/recall of the flag decision against it,
  and tune thresholds against that — otherwise the flag rate is just a
  guess.
- **Performance.** The current fuzzy-area-match does an O(words × areas)
  scan per address; fine for 30 rows, needs indexing (e.g. a trigram index
  or a proper fuzzy-matching library) once the area gazetteer and address
  count both grow.
- **Review workflow, not just a CSV.** At 30 rows a human can eyeball every
  flagged record. At 20,000, flagged records need to be routed into a
  proper review queue (ideally ranked by flag-reason type, since "two
  addresses concatenated" needs different handling than "no pincode"), with
  the parser's reason string pre-filled to speed up manual correction.
- **Track corrections as feedback.** Every time a human resolves a flagged
  address, that correction is a data point. At scale it's worth logging
  those and periodically mining them for new gazetteer entries or new
  abbreviation patterns (e.g. discovering "JVLR" should be a recognised
  street keyword) — turning manual review into gazetteer growth rather than
  repeating the same manual fix indefinitely.
- **What deliberately would NOT change:** the core philosophy (explainable
  rules over a black-box model, never invent a value, flag rather than
  guess). Scale changes the vocabulary size and the tooling around the
  parser, not the design principle.