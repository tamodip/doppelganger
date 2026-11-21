# Same Person or Not? A deterministic identity-resolution engine

**Code decides, every decision cites its evidence, uncertain cases go to a human, and the output is byte-identical on every run.**

```
system/run <data folder> <output file>
```

Python 3 standard library only. No language model, no network, no randomness.

| | Development set |
|---|---|
| Practice labels (validate.py) | 50/50 right decisions, **0 unsafe clearances**, decision score 100 |
| Reason code / customer record / evidence on practice hits | 100% / 100% / 50 of 50 exact |
| Decisions on all 819 hits | 465 DIFFERENT_ENTITY, 124 SAME_ENTITY, 230 REVIEW (28% review rate) |
| Run time | about 8 seconds for the full development set (72k customers) |
| Cost | $0 (no model calls) |
| Reproducibility | 5 runs, byte-identical output |

## Architecture

```
alerts.jsonl ──► 1. RESOLVE THE CUSTOMER ─────────────────────────────┐
                 account_ref / login_handle / email → records          │
                 no link → search records by screened name,            │
                   DOB (every part both give), country, city           │
                 one person = same phone + same full DOB,              │
                   or same document number on approved checks          │
                   (union-find over all 72k records, transitive)       │
                 >1 person fits → decide each; disagree → AMBIGUOUS    │
                                                                       ▼
                 2. CUSTOMER PICTURE (one person, one picture)    4. COMPARE (POLICY.md 6–8)
                 records + accounts → onboarding → KYC checks     clear only on a contradiction:
                 + verification notes (by email)                    type · death · too young (<16)
                 most recent APPROVED check = legal name,           · DOB · verified name · location
                   DOB, document country (verified > declared)    confirm only on agreement:
                 rejected/expired checks ignored                    person: name + DOB
                 countries: residence, nationality, ID document,    company: name + country
                   places the notes say they lived                otherwise → REVIEW
                                                                       ▲
hits.jsonl ────► 3. HIT PICTURE                                         │
                 names + aliases; countries from countries,            │
                   nationality, citizenship, addresses (last part),    │
                   place of birth ("International"/"Europe" ignored)   │
                 DOB: dobs field → first paragraph of profile notes    │
                   ("aged 45 in March 2019" → 1973 or 1974)            │
                   → web results that match the listing phrase AND     │
                   a country tied to the hit                           │
                 earliest dated report: "According to …; Month D, YYYY:"
                 date of death ───────────────────────────────────────┘
```

Everything is in `engine.py` (about 1163 lines). `countries.json` maps country names, ISO codes and nationalities (e.g. "Nigerian", "GB") to one canonical country.

## How we find the customer
1. Follow the alert's link: `account_ref` → accounts (joint accounts give several holders), else `login_handle`, else `email`.
2. If the link leads to more than one person, the screened details (name per principle 8, DOB, country, city) pick between them.
3. Without a link, we search the system of record. Candidates come from a token index (exact tokens, name-variant groups, and tokens within the policy's edit distance), then each must fit the screened name, DOB, country and city. A missing field never rules a record out.
4. Records are merged into **one person** by union-find: same phone and same full date of birth, or the same document number on approved KYC checks. The links are transitive.
5. If several people still fit, the hit is decided for each. If they all agree, we give that decision; otherwise REVIEW with `AMBIGUOUS_CUSTOMER_RECORD`. Either way `customer_record_id` is `AMBIGUOUS`.

## How we find the listed person
Names and aliases; countries from every field the policy lists; DOB from `dobs`, else the first paragraph of the profile notes, else search results. A search result only counts if it contains the listing phrase for the hit's category ("named in adverse media", "a politically exposed person", …) and names a country tied to the hit. An age at a date always gives **two** possible birth years.

## How evidence is represented
Every fact carries the IDs of the records it came from (`Fact(value, src)`), for example the customer's DOB `1968-03-05` from `NOTE-14E25A`, or a country from `CUS-377745`. A decision cites only the sources of the facts that decided it: for a DOB mismatch only the DOB source; for a location match only the records that give the shared country. When several contradictions apply, the evidence for each is cited.

## Safety design (an unsafe clearance costs 60, a review costs 2–3)
- **DIFFERENT_ENTITY only on a named contradiction from the policy.** There is no "probably different".
- **Verified beats declared, and a failed read never falls back to a guess.** If a verification note exists but its birth date can't be read, the customer's DOB is treated as unknown rather than trusting the self-declared date. (A self-declared DOB once caused an unsafe clearance during development; this rule removes the whole class.)
- **Location never clears a name + DOB agreement** (policy 6), and a country difference also prevents confirming it: the hit goes to REVIEW. If any country value on either side can't be read, location never clears.
- **Several unverified customer DOBs:** we clear on DOB only if every one of them contradicts.
- **Names with fewer than two real parts** (placeholders like N/A or 0000 removed) are neither confirmed nor cleared on name.

## Determinism
No model, no randomness, sorted iteration everywhere, sets sorted before output. Five runs produce byte-identical files (`cmp`).

## Free text: built for unseen wording
- **Verification notes:** the name is the longest run of CAPITALISED words (Unicode-aware, so ÉŁĆ work). The DOB is the date nearest a birth keyword (born, DOB, birth date …), else the one date at least 10 years before the note. A note counts as an approved check unless it explicitly says rejected, failed, expired or similar. In the development set this reads a name and a DOB from all 21,220 verification notes.
- **Ages:** "aged 45", "he was 46", "reached the age of 23 upon…", "then 37", "at the age of 33", "45-year-old", "Name, 51,", "(46)", each paired with the nearest date in the same sentence. Numbers followed by a unit ("37 million", "46 days") are not ages.
- **Dates:** ISO, day-first slashes (the data room never writes month-first; the engine reads month-first only when the second number is above 12), "10 Sep 1962", "September 10, 1962", "Sep 1962", year only.
- We fixed every practice-label mismatch by generalising a rule, never by matching an ID or a specific sentence.

## Three cases

**A clear mismatch: H-D861DA → DIFFERENT_ENTITY / DOB_MISMATCH.**
The hit has no `dobs`, but its profile notes say "when investigators questioned Michael Earl Tilley in January 2023, he was 46", so it was born in 1976 or 1977. The customer's verified DOB is 1979-11-08 (NOTE-8548C1), more than one year outside both. Evidence: `NOTE-8548C1`.

**A match: H-582410 → SAME_ENTITY / NAME_DOB_LOCATION_MATCH.**
`account_ref ACC-C4CA34` has two joint holders. The screened name and DOB pick CUS-377745. Their verification note NOTE-14E25A gives the DOB 1968-03-05, which agrees with the hit's 1967 or 1968. Both are linked to Spain, and the name agrees with "Elvira Velasco Morillo". Evidence: `CUS-377745`, `NOTE-14E25A`.

**Left for review: H-FF3096 → REVIEW / INSUFFICIENT_DATA.**
The customer's name agrees with "Abdul Rahman Yasin", and a support note (NOTE-10AEAB) says they lived in the United States, a country tied to the hit, so location can't clear it. The hit gives no date of birth anywhere (fields, notes or matching search results), so it can be neither cleared nor confirmed. A human decides.

## Cross-checked against an independent implementation
We compared all 819 decisions with a second, independently written rules engine. They disagreed on only 8 hits. We traced each one to the policy text and fixed the 2 where we were wrong (an age wording we missed, and a name + DOB agreement with a country difference, which the policy says is REVIEW). One disagreement was the other engine trusting a self-declared birth date over a verified note, which would have been an unsafe clearance.

## What we can't do (honest limits)
- Free text worded very differently from anything above (for example a birth date given only as "born the year the Berlin Wall fell") won't be read. The engine then falls back to REVIEW, never to a clearance.
- Countries named only by a city or region in free text ("lived in Lagos") aren't mapped to a country, so the customer may be linked to fewer countries. That can only reduce location clearances, never add unsafe ones.
- Some judgement calls in the policy (what counts as "living somewhere" in a support note) are read conservatively: any country a note mentions is linked to the customer.

## Files
- `run`: entry point
- `engine.py`: the engine
- `countries.json`: country lookup
- `../tools/score.py`: cost-matrix scorer that lists every practice mismatch
