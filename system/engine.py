#!/usr/bin/env python3
"""Same Person or Not? -- deterministic identity resolution engine.

Pure Python 3 standard library. No randomness, no network, no LLM:
the same input folder always gives byte-identical output.

Pipeline per alert:
  1. resolve the customer  (alert link -> records -> one-person groups)
  2. build the customer picture (records + approved KYC + notes)
  3. build the hit picture (fields + profile notes + matching web results)
  4. compare, following POLICY.md principles 6-8, and explain
"""
import json, os, re, sys, unicodedata
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))

# --------------------------------------------------------------------------- io

def read_jsonl(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def as_list(v):
    """Fields may arrive as lists or as '; '-joined strings."""
    if v is None:
        return []
    if isinstance(v, list):
        res = []
        for x in v:
            res.extend(as_list(x) if isinstance(x, list) else [x])
        return [x for x in res if x not in (None, "")]
    if isinstance(v, dict):
        return [v]
    s = str(v).strip()
    if not s:
        return []
    return [p.strip() for p in s.split(";") if p.strip()]


def sval(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x) for x in v if x is not None)
    return str(v).strip()

# ---------------------------------------------------------------- normalising

def strip_accents(s):
    s = unicodedata.normalize("NFKD", s)
    return "".join(c for c in s if not unicodedata.combining(c))


def norm_text(s):
    s = strip_accents(sval(s)).lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()

# ------------------------------------------------------------------ countries

with open(os.path.join(HERE, "countries.json"), encoding="utf-8") as _f:
    _C = json.load(_f)
COUNTRY_NAMES = _C["names"]
COUNTRY_CODES = _C["codes"]
NOT_COUNTRIES = {"international", "europe", "global", "worldwide", "unknown", "n a", "na", "none", ""}
# free text: multi-word first, skip ambiguous short words
_FREE_SKIP = {"us", "u s", "in", "chad", "jordan", "georgia", "american", "english", "guinea", "turkey", "niger", "dominican"}
_FREE_KEYS = sorted([k for k in COUNTRY_NAMES if k not in _FREE_SKIP and len(k) > 2 or k in ("uk", "uae")],
                    key=lambda k: (-len(k), k))
_FREE_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in _FREE_KEYS) + r")\b")


def country_of(value):
    """Structured field -> canonical country (or None)."""
    v = re.sub(r"\[.*?\]|\(.*?\)", " ", sval(value))
    k = norm_text(v)
    if k in NOT_COUNTRIES:
        return None
    if k in COUNTRY_NAMES:
        return COUNTRY_NAMES[k]
    if k in COUNTRY_CODES:
        return COUNTRY_CODES[k]
    # "town, region, Country": the last part
    if "," in v:
        last = norm_text(v.split(",")[-1])
        if last in COUNTRY_NAMES:
            return COUNTRY_NAMES[last]
        if last in COUNTRY_CODES:
            return COUNTRY_CODES[last]
    # e.g. "Republic of X" or trailing words: try free text scan
    found = countries_in_text(v)
    return sorted(found)[0] if len(found) == 1 else None


def countries_in_text(text):
    t = " " + norm_text(text) + " "
    found = set()
    for m in _FREE_RE.finditer(t):
        found.add(COUNTRY_NAMES[m.group(1)])
    return found


def last_part_country(addr):
    a = re.sub(r"\[.*?\]|\(.*?\)", " ", sval(addr))
    parts = [p.strip() for p in a.split(",") if p.strip()]
    if not parts:
        return None
    c = country_of(parts[-1])
    if c is None and len(parts) == 1:
        c = None
    return c

# ---------------------------------------------------------------------- dates

MONTHS = {m: i + 1 for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"])}
for _k, _v in list(MONTHS.items()):
    MONTHS[_k[:3]] = _v
MONTHS["sept"] = 9
_MON = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"


class D:
    """Partial date: year required, month/day optional. Hashable, comparable."""
    __slots__ = ("y", "m", "d")

    def __init__(self, y, m=None, d=None):
        self.y, self.m, self.d = y, m, d

    def key(self):
        return (self.y, self.m or 0, self.d or 0)

    def __eq__(self, o):
        return isinstance(o, D) and self.key() == o.key()

    def __hash__(self):
        return hash(self.key())

    def full(self):
        return self.m is not None and self.d is not None

    def __str__(self):
        if self.m is None:
            return "%04d" % self.y
        if self.d is None:
            return "%04d-%02d" % (self.y, self.m)
        return "%04d-%02d-%02d" % (self.y, self.m, self.d)

    __repr__ = __str__


def _ok(y, m=None, d=None):
    if not (1850 <= y <= 2100):
        return None
    if m is not None and not (1 <= m <= 12):
        return None
    if d is not None and not (1 <= d <= 31):
        return None
    return D(y, m, d)


def parse_date(s):
    """Parse one date as written by a person or provider. Returns D or None."""
    s = sval(s).strip()
    if not s:
        return None
    t = strip_accents(s).lower().strip()
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[t ].*)?", t)
    if m:
        return _ok(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})", t)
    if m:
        return _ok(int(m.group(1)), int(m.group(2)))
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if a > 12 and b <= 12:
            return _ok(y, b, a)
        if b > 12 and a <= 12:
            return _ok(y, a, b)
        # both <= 12: this data room writes slash dates day-first (no month-first
        # date was found among 12,000+), so read DD/MM/YYYY
        return _ok(y, b, a)
    m = re.fullmatch(r"(\d{1,2})[ -]" + _MON + r"[ ,-]+(\d{4})", t)
    if m:
        return _ok(int(m.group(3)), MONTHS[m.group(2)[:3] if m.group(2)[:4] != "sept" else "sep"], int(m.group(1)))
    m = re.fullmatch(_MON + r"[ -]+(\d{1,2}),?[ -]+(\d{4})", t)
    if m:
        return _ok(int(m.group(3)), MONTHS[m.group(1)[:3]], int(m.group(2)))
    m = re.fullmatch(_MON + r",?[ -]+(\d{4})", t)
    if m:
        return _ok(int(m.group(2)), MONTHS[m.group(1)[:3]])
    m = re.fullmatch(r"(?:c\.?|circa|about|approx\.?)?\s*(\d{4})", t)
    if m:
        return _ok(int(m.group(1)))
    return None


_DATE_IN_TEXT = re.compile(
    r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{4}|\d{1,2}\.\d{1,2}\.\d{4}|"
    r"\d{1,2}(?:st|nd|rd|th)?\s+" + _MON + r",?\s+\d{4}|" + _MON + r"\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}|"
    + _MON + r"\s+\d{4}|\b\d{4}\b)", re.I)


def dates_in_text(text):
    out = []
    for m in _DATE_IN_TEXT.finditer(sval(text)):
        d = parse_date(m.group(0))
        if d:
            out.append((m.start(), m.end(), d))
    return out


def screened_date(s):
    try:
        return parse_date(s)
    except Exception:
        return None

# --------------------------------------------------------------------- names

PLACEHOLDERS = {"na", "n a", "test", "unknown", "xxx", "none", "null", "nil", "tbd", "tba", "unk", "nn", "fnu", "lnu", "xx", "x", "abc", "dummy", "sample", "noname"}


class Names:
    def __init__(self, path):
        data = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        groups = []
        legal = []
        if isinstance(data, dict):
            for k, v in data.items():
                kl = k.lower()
                if "legal" in kl or "company" in kl or "form" in kl:
                    legal.extend(v if isinstance(v, list) else [v])
                elif isinstance(v, list):
                    if v and isinstance(v[0], list):
                        groups.extend(v)
                    elif v and isinstance(v[0], dict):
                        for g in v:
                            groups.append(g.get("forms") or g.get("names") or g.get("variants") or list(g.values())[0])
                    else:
                        groups.append(v)
                elif isinstance(v, dict):
                    for gk, gv in v.items():
                        groups.append([gk] + (gv if isinstance(gv, list) else [gv]))
        elif isinstance(data, list):
            groups = data
        self.var = defaultdict(set)
        for gi, g in enumerate(groups):
            for n in g:
                self.var[norm_text(n)].add(gi)
        self.legal = {norm_text(x) for x in legal} or {
            "ab", "ag", "as", "asa", "bv", "co", "company", "corp", "corporation", "gmbh", "inc", "kg", "limited", "llc", "llp",
            "lp", "ltd", "na", "nv", "oy", "oyj", "plc", "sa", "sarl", "sas", "se", "sf", "sl", "spa", "srl"}

    def variant(self, a, b):
        ga, gb = self.var.get(a), self.var.get(b)
        return bool(ga and gb and ga & gb)


NAMES = None


def name_parts(name, company=False):
    s = re.sub(r"\[.*?\]|\(.*?\)|\{.*?\}", " ", sval(name))
    s = norm_text(s)
    parts = []
    for p in s.split():
        if len(p) == 1:
            continue                       # initials are not parts
        if p in PLACEHOLDERS or p.isdigit() or re.fullmatch(r"\d+[a-z]?|[a-z]?\d+", p):
            continue
        if company and p in NAMES.legal:
            continue
        if p not in parts:
            parts.append(p)
    return parts


def lev(a, b, cap):
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        best = cur[0]
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
            if cur[j] < best:
                best = cur[j]
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def part_match(a, b):
    if a == b:
        return True
    if NAMES.variant(a, b):
        return True
    n = min(len(a), len(b))
    if n < 4:
        return False
    cap = 1 if n <= 6 else 2
    return lev(a, b, cap) <= cap


def _units(parts):
    """single parts and adjacent pairs (written apart) as candidate units."""
    u = [((i,), p) for i, p in enumerate(parts)]
    u += [((i, i + 1), parts[i] + parts[i + 1]) for i in range(len(parts) - 1)]
    return u


def name_agree(pa, pb):
    """Principle 8 on two lists of parts. Returns (agree, n_matched)."""
    if not pa or not pb:
        return False, 0
    ua, ub = _units(pa), _units(pb)
    edges = []
    for ia, sa in ua:
        for ib, sb in ub:
            if len(ia) == 2 and len(ib) == 2:
                continue
            if part_match(sa, sb):
                edges.append((ia, ib))
    # maximum set of non-overlapping matches: tiny, so exhaustive search
    best = (0, set(), set())

    def rec(k, used_a, used_b, cnt):
        nonlocal best
        if cnt > best[0] or (cnt == best[0] and len(used_a) + len(used_b) > len(best[1]) + len(best[2])):
            best = (cnt, set(used_a), set(used_b))
        if k >= len(edges):
            return
        if cnt + (len(edges) - k) < best[0]:
            return
        ia, ib = edges[k]
        if not (set(ia) & used_a) and not (set(ib) & used_b):
            rec(k + 1, used_a | set(ia), used_b | set(ib), cnt + 1)
        rec(k + 1, used_a, used_b, cnt)

    if len(edges) > 18:
        edges = edges[:18]
    rec(0, set(), set(), 0)
    cnt, ua_used, ub_used = best
    if cnt >= 2:
        return True, cnt
    # every part of the shorter name matches
    if len(pa) <= len(pb):
        short_all = len(ua_used) == len(pa)
    else:
        short_all = len(ub_used) == len(pb)
    if cnt >= 1 and short_all:
        return True, cnt
    return False, cnt


def names_agree_any(cust_names, hit_names, company=False):
    """Return the first hit name that agrees with any customer name, else None."""
    for hn in hit_names:
        hp = name_parts(hn, company)
        for cn in cust_names:
            cp = name_parts(cn, company)
            ok, _ = name_agree(cp, hp)
            if ok:
                return hn
    return None

# ------------------------------------------------------------------- the data


class Data:
    def __init__(self, folder):
        j = lambda n: read_jsonl(os.path.join(folder, n))
        self.alerts = j("alerts.jsonl")
        for a in self.alerts:   # the screened customer arrives nested
            c = a.get("customer")
            if isinstance(c, dict):
                a.setdefault("customer_name", c.get("name"))
                for k in ("entity_type", "dob", "country", "city"):
                    a.setdefault(k, c.get(k))
        self.hits = {h["hit_id"]: h for h in j("hits.jsonl")}
        self.customers = {c["record_id"]: c for c in j("customers.jsonl")}
        self.accounts = j("accounts.jsonl")
        self.onboarding = j("onboarding.jsonl")
        self.kyc = j("kyc_checks.jsonl")
        self.notes = j("notes.jsonl")
        self.search = j("search_results.jsonl")
        global NAMES
        NAMES = Names(os.path.join(folder, "name_variants.json"))
        self._index()

    def _index(self):
        self.acc_by_id = defaultdict(list)
        self.acc_by_login = defaultdict(list)
        self.acc_by_cust = defaultdict(list)
        for a in self.accounts:
            self.acc_by_id[sval(a.get("account_id"))].append(a)
            if sval(a.get("login_handle")):
                self.acc_by_login[sval(a.get("login_handle")).lower()].append(a)
            self.acc_by_cust[sval(a.get("customer_id"))].append(a)
        self.cust_by_email = defaultdict(list)
        self.cust_by_login = defaultdict(list)
        for rid, c in self.customers.items():
            if sval(c.get("email")):
                self.cust_by_email[sval(c.get("email")).lower()].append(rid)
            if sval(c.get("login_name")):
                self.cust_by_login[sval(c.get("login_name")).lower()].append(rid)
        self.onb_by_cust = defaultdict(list)
        for o in self.onboarding:
            self.onb_by_cust[sval(o.get("customer_id"))].append(o)
        self.kyc_by_app = defaultdict(list)
        for k in self.kyc:
            self.kyc_by_app[sval(k.get("applicant_id"))].append(k)
        self.notes_by_email = defaultdict(list)
        for n in self.notes:
            self.notes_by_email[sval(n.get("requester_email")).lower()].append(n)
        self.search_by_q = defaultdict(list)
        for r in self.search:
            self.search_by_q[" ".join(sorted(norm_text(r.get("query")).split()))].append(r)
        # name token index for unlinked searches
        self.tok_index = defaultdict(set)
        for rid, c in self.customers.items():
            for fld in ("full_name", "preferred_name", "login_name"):
                for p in name_parts(c.get(fld), c.get("entity_type") == "Company"):
                    self.tok_index[p].add(rid)
        self.vocab_by_len = defaultdict(list)
        for t in self.tok_index:
            self.vocab_by_len[len(t)].append(t)
        self._persons()

    # ---- principle 3: one person, one picture
    def approved_checks(self, rid):
        out = []
        for o in self.onb_by_cust.get(rid, []):
            for k in self.kyc_by_app.get(sval(o.get("kyc_applicant_id")), []):
                if norm_text(k.get("result")) in ("approved", "passed", "clear", "verified", "pass"):
                    out.append(k)
        return out

    def _persons(self):
        parent = {rid: rid for rid in self.customers}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                if ra < rb:
                    parent[rb] = ra
                else:
                    parent[ra] = rb

        by_phone_dob = defaultdict(list)
        by_doc = defaultdict(list)
        for rid, c in sorted(self.customers.items()):
            d = parse_date(c.get("dob"))
            phones = set()
            ph = re.sub(r"\D", "", sval(c.get("phone")))
            if len(ph) >= 6:
                phones.add(ph)
            if d and d.full():
                for ph in phones:
                    by_phone_dob[(ph, d.key())].append(rid)
            for k in self.approved_checks(rid):
                dn = re.sub(r"[^A-Z0-9]", "", sval(k.get("document_number")).upper())
                if len(dn) >= 4:
                    by_doc[dn].append(rid)
        for grp in list(by_phone_dob.values()) + list(by_doc.values()):
            for r in grp[1:]:
                union(grp[0], r)
        self.person_of = {rid: find(rid) for rid in self.customers}
        self.members = defaultdict(list)
        for rid in sorted(self.customers):
            self.members[self.person_of[rid]].append(rid)

    def person_records(self, rid):
        return self.members[self.person_of[rid]]

    # ---- principle 2: finding the customer
    def fits_screened(self, rid, alert):
        c = self.customers[rid]
        company = sval(alert.get("entity_type")).lower().startswith("comp")
        sn = name_parts(alert.get("customer_name"), company)
        if sn:
            ok = False
            for fld in ("full_name", "preferred_name", "login_name"):
                if name_agree(name_parts(c.get(fld), company), sn)[0]:
                    ok = True
                    break
            if not ok:
                return False
        sd = parse_date(alert.get("dob"))
        cd = parse_date(c.get("dob"))
        if sd and cd:
            if sd.y != cd.y or (sd.m and cd.m and sd.m != cd.m) or (sd.d and cd.d and sd.d != cd.d):
                return False
        sc = country_of(alert.get("country"))
        if sc:
            cc = {country_of(c.get("residence_country")), country_of(c.get("nationality"))} - {None}
            if cc and sc not in cc:
                return False
        scity = norm_text(alert.get("city"))
        if scity:
            ccity = norm_text(c.get("city")) + " " + norm_text(c.get("address"))
            if ccity.strip() and (" " + scity + " ") not in (" " + ccity + " "):
                return False
        return True

    def search_customers(self, alert):
        company = sval(alert.get("entity_type")).lower().startswith("comp")
        sn = name_parts(alert.get("customer_name"), company)
        cands = set()
        for p in sn:
            cands |= self.tok_index.get(p, set())
            for gi in NAMES.var.get(p, ()):  # variants
                for t, gs in NAMES.var.items():
                    if gi in gs:
                        cands |= self.tok_index.get(t, set())
            if len(p) >= 4:
                cap = 1 if len(p) <= 6 else 2
                for L in range(len(p) - cap, len(p) + cap + 1):
                    for t in self.vocab_by_len.get(L, ()):
                        if t != p and min(len(t), len(p)) >= 4 and part_match(p, t):
                            cands |= self.tok_index[t]
        return sorted(r for r in cands if self.fits_screened(r, alert))

    def resolve(self, alert):
        """Return (list of person record-lists, path description)."""
        linked = []
        how = None
        acc = sval(alert.get("account_ref"))
        login = sval(alert.get("login_handle")).lower()
        email = sval(alert.get("email")).lower()
        if acc and acc in self.acc_by_id:
            linked = sorted({sval(a.get("customer_id")) for a in self.acc_by_id[acc]})
            how = "account_ref %s" % acc
        elif login and (login in self.acc_by_login or login in self.cust_by_login):
            linked = sorted({sval(a.get("customer_id")) for a in self.acc_by_login.get(login, [])} | set(self.cust_by_login.get(login, [])))
            how = "login_handle %s" % login
        elif email and email in self.cust_by_email:
            linked = sorted(self.cust_by_email[email])
            how = "email %s" % email
        linked = [r for r in linked if r in self.customers]
        if linked:
            persons = self._group(linked)
            if len(persons) > 1:
                fit = [p for p in persons if any(self.fits_screened(r, alert) for r in p)]
                if fit:
                    persons = fit
            return persons, how
        found = self.search_customers(alert)
        return self._group(found), "screened details search"

    def _group(self, rids):
        seen = {}
        for r in rids:
            seen.setdefault(self.person_of[r], None)
        return [self.members[p] for p in sorted(seen)]

# ------------------------------------------------------------ customer picture

NOTE_OK = re.compile(r"\b(verified|confirmed|validated|approved|passed|completed|successful(?:ly)?|cleared)\b", re.I)
NOTE_BAD = re.compile(r"\b(reject(?:ed)?|fail(?:ed|ure)?|declined|denied|expired|unsuccessful|could not|couldn't|unable|not (?:be )?(?:verified|confirmed|approved)|mismatch|pending|invalid|suspected fake|forged)\b", re.I)
LIVED = re.compile(r"\b(lived|living|moved|relocat\w*|resid\w*|based|previous(?:ly)?|former(?:ly)?|used to|grew up|originally|from|born in|home)\b", re.I)


class Fact:
    def __init__(self, value, src):
        self.value, self.src = value, src


_NAME_STOP = {"DOB", "ID", "KYC", "UK", "USA", "UAE", "AML", "PEP", "ON", "THE", "AND", "OF", "FOR", "BY", "VIA", "D.O.B", "D.O.B."}


def _is_cap_token(w):
    core = re.sub(r"^[\"'(\[]+|['’]s$|[\"'),.;:\]]+$", "", w)
    core = re.sub(r"['’]s$", "", core)
    if not core:
        return None
    if core.upper() in ("N/A", "NA") or core.isdigit():
        return core                       # placeholder inside a name
    letters = [c for c in core if c.isalpha()]
    if len(letters) >= 2 and all(c.isupper() for c in letters) and re.fullmatch(r"[^\W\d_]+(?:[-'’][^\W\d_]+)*", core):
        return core
    return None


def _upper_name(text):
    """The person's name in a verification note: the longest run of CAPITALISED words."""
    best, cur = [], []
    for w in text.split():
        t = _is_cap_token(w)
        if t is not None and t.upper() not in _NAME_STOP:
            cur.append(t)
            ended = bool(re.search(r"[,.;:)]$|['’]s[,.;:]?$", w))
        else:
            ended = True
        if ended:
            real = [x for x in cur if any(c.isalpha() for c in x) and x.upper() not in ("N/A", "NA")]
            if real and len(cur) > len(best):
                best = cur
            if t is None or t.upper() in _NAME_STOP:
                cur = []
            else:
                cur = [] if ended else cur
    real = [x for x in cur if any(c.isalpha() for c in x)]
    if real and len(cur) > len(best):
        best = cur
    if best:
        return " ".join(best)
    m = re.search(r"(?:for|of|named|customer|applicant|subject|holder as)\s+([A-Z][a-z'\-]+(?:\s+[A-Z][a-z'\-]+){1,3})", text)
    return m.group(1) if m else None


BIRTH_KW = re.compile(r"\b(born|birth|dob|d\.o\.b|date of birth|birthday)\b", re.I)


def note_dob(text, created):
    """Date of birth in a verification note, however it is worded.
    1) a date shortly after a birth keyword; 2) otherwise the one date that is
    clearly in the past (>= 10 years before the note) and is not the check date."""
    ds = [x for x in dates_in_text(text) if not (len(str(x[2])) == 4 and re.search(r"[A-Z]\d*" + str(x[2].y), text))]
    for kw in BIRTH_KW.finditer(text):
        after = [x for x in ds if 0 <= x[0] - kw.end() <= 45]
        if after:
            after.sort(key=lambda x: x[0])
            return after[0][2]
        before = [x for x in ds if 0 <= kw.start() - x[1] <= 6]
        if before:
            return before[-1][2]
    old = [x[2] for x in ds if not (created and x[2] == created) and (created is None or x[2].y <= created.y - 10)]
    old = sorted(set(old), key=lambda d: d.key())
    if len({d.y for d in old}) == 1:
        full = [d for d in old if d.m]
        return full[0] if full else old[0]
    return None


def note_check(n):
    """Parse an onboarding/KYC note into a verification fact, or None."""
    text = sval(n.get("text"))
    ch = norm_text(n.get("channel"))
    if not re.search(r"\b(identity|passport|national id|id card|driving licen[cs]e|driver'?s licen[cs]e|document|kyc|verif|residence permit)\b", text, re.I):
        return None
    if NOTE_BAD.search(text):
        return {"ok": False}             # rejected / failed / expired: not evidence
    name = _upper_name(text)
    dob = note_dob(text, parse_date(n.get("created_at")))
    # document country: country mentioned in the text
    doc_c = countries_in_text(text)
    when = parse_date(n.get("created_at"))
    return {"ok": True, "name": name, "dob": dob, "countries": doc_c, "date": when, "id": n.get("note_id"), "channel": ch}


class Customer:
    """Everything known about one person (one or more records)."""

    def __init__(self, data, rids, alert):
        self.rids = rids
        self.records = [data.customers[r] for r in rids]
        self.company = any(sval(c.get("entity_type")).lower().startswith("comp") for c in self.records) or (
            not self.records and sval(alert.get("entity_type")).lower().startswith("comp"))
        # approved checks: KYC provider + onboarding notes; most recent wins
        checks = []
        for r in rids:
            for k in data.approved_checks(r):
                checks.append({"date": parse_date(k.get("checked_at")), "id": k.get("check_id"),
                               "name": sval(k.get("verified_name")) or None, "dob": parse_date(k.get("verified_dob")),
                               "countries": {country_of(k.get("document_country"))} - {None}})
        emails = sorted({sval(c.get("email")).lower() for c in self.records if sval(c.get("email"))})
        self.notes = []
        for e in emails:
            self.notes.extend(data.notes_by_email.get(e, []))
        self.notes.sort(key=lambda n: (sval(n.get("created_at")), sval(n.get("note_id"))))
        for n in self.notes:
            nc = note_check(n)
            if nc and nc["ok"]:
                checks.append(nc)
        checks.sort(key=lambda c: ((c["date"].key() if c["date"] else (0, 0, 0)), sval(c["id"])))
        self.verified = checks[-1] if checks else None
        # countries tied to the customer, with their sources
        self.countries = defaultdict(set)   # country -> set(source ids)
        self.unreadable_country = False
        for c in self.records:
            for f in ("residence_country", "nationality"):
                cc = country_of(c.get(f))
                if cc:
                    self.countries[cc].add(c["record_id"])
                elif sval(c.get(f)) and norm_text(c.get(f)) not in NOT_COUNTRIES:
                    self.unreadable_country = True
        if self.verified:
            for cc in self.verified["countries"]:
                self.countries[cc].add(self.verified["id"])
        for n in self.notes:
            if self.verified and n.get("note_id") == self.verified["id"]:
                continue
            txt = sval(n.get("text"))
            if note_check(n) is not None and not (note_check(n) or {}).get("ok"):
                continue  # rejected checks are not evidence
            for cc in countries_in_text(txt):
                self.countries[cc].add(n.get("note_id"))
        # name and date of birth
        if self.verified and self.verified.get("name"):
            self.legal_name = Fact(self.verified["name"], [self.verified["id"]])
        else:
            self.legal_name = None
        self.record_names = [sval(c.get("full_name")) for c in self.records if sval(c.get("full_name"))]
        if self.verified and self.verified.get("dob"):
            self.dobs = [Fact(self.verified["dob"], [self.verified["id"]])]
        elif self.verified and self.verified.get("channel") is not None:
            # a verification note we could not read a birth date from:
            # verified beats declared, so the declared date must not decide.
            self.dobs = []
        else:
            ds = {}
            for c in self.records:
                d = parse_date(c.get("dob"))
                if d:
                    ds.setdefault(d, []).append(c["record_id"])
            self.dobs = [Fact(d, sorted(s)) for d, s in sorted(ds.items(), key=lambda x: x[0].key())]
        if not self.records:
            sd = parse_date(alert.get("dob"))
            self.dobs = [Fact(sd, [])] if sd else []
            self.record_names = [sval(alert.get("customer_name"))]

    def names_for_agreement(self):
        if self.legal_name:
            return [self.legal_name.value], list(self.legal_name.src)
        return self.record_names, list(self.rids)

    def country_srcs(self, countries=None):
        s = set()
        for c, src in self.countries.items():
            if countries is None or c in countries:
                s |= src
        return s

# ----------------------------------------------------------------- hit picture

CATEGORY_PHRASE = {
    "sanctions": "a sanctioned individual",
    "pep": "a politically exposed person",
    "enforcement": "the subject of enforcement action",
    "adverse media": "named in adverse media",
    "soe": "a state-owned enterprise official",
}
REPORT_RE = re.compile(r"^\s*According to [^;\n]+;\s*([A-Z][a-z]+ \d{1,2}, \d{4})\s*:", re.M)


def age_years(age, when):
    """aged `age` at date `when` -> two possible birth years."""
    return {when.y - age, when.y - age - 1}


_AGE_END = r"(?=\s*(?:years?(?:[- ]old|\s+of\s+age)?\b|yrs?\b|[.,;:)!?]|$|\s+(?:and|when|at|in|on|as|after|while|but|during|following|before|then)\b))"
AGE_RE = re.compile(
    r"(?:\b(?:aged?|age of|was|is|were|turned|turning|reached|reaching|being|at)\s+(?:about\s+|around\s+|approximately\s+|just\s+)?(\d{2,3})" + _AGE_END +
    r"|\b(\d{2,3})[- ]years?[- ]old\b"
    r"|\b(\d{2,3})\s+years\s+of\s+age\b"
    r"|,\s*(\d{2,3})\s*,"
    r"|\(\s*(?:aged?\s*)?(\d{2,3})\s*\))", re.I)


class HitDob:
    def __init__(self, years, m=None, d=None, src=None, how=""):
        self.years = set(years)
        self.m, self.d, self.src, self.how = m, d, src, how

    def __str__(self):
        ys = sorted(self.years)
        if len(ys) == 1:
            return str(D(ys[0], self.m, self.d))
        return " or ".join(str(y) for y in ys)


class Hit:
    def __init__(self, data, h):
        self.h = h
        self.id = h["hit_id"]
        self.company = sval(h.get("entity_type")).lower().startswith(("comp", "org", "entity"))
        self.type_known = bool(sval(h.get("entity_type")))
        self.names = [x for x in [sval(h.get("name"))] + [sval(a) for a in as_list(h.get("aliases"))] if x]
        cats = [norm_text(c) for c in as_list(h.get("categories"))]
        self.category = norm_text(h.get("primary_category")) or (cats[0] if cats else "")
        # countries
        self.countries = set()
        for f in ("countries", "nationality", "citizenship"):
            for v in as_list(h.get(f)):
                c = country_of(v)
                if c:
                    self.countries.add(c)
        for v in as_list(h.get("addresses")):
            c = last_part_country(v)
            if c:
                self.countries.add(c)
        for v in as_list(h.get("place_of_birth")):
            c = last_part_country(v)
            if c:
                self.countries.add(c)
        # a country value we cannot read might be the shared one: never clear on location then
        self.unreadable_country = False
        for f in ("countries", "nationality", "citizenship"):
            for v in as_list(h.get(f)):
                if country_of(v) is None and norm_text(re.sub(r"\[.*?\]", "", sval(v))) not in NOT_COUNTRIES:
                    self.unreadable_country = True
        notes = sval(h.get("profile_notes"))
        self.first_par = re.split(r"\n\s*\n", notes.strip())[0] if notes.strip() else ""
        # dated reports
        self.reports = []
        for m in REPORT_RE.finditer(notes):
            d = parse_date(m.group(1))
            if d:
                self.reports.append(d)
        self.earliest_report = min(self.reports, key=lambda d: d.key()) if self.reports else None
        # death
        self.death = parse_date(h.get("deceased_date"))
        if self.death is None and norm_text(h.get("life_status")) == "deceased":
            m = re.search(r"(?:died|deceased|death)[^.]*?(\d{4}[^.]*?|\b[A-Z][a-z]+ \d{1,2}, \d{4})", notes)
            if m:
                ds = dates_in_text(m.group(0))
                if ds:
                    self.death = ds[-1][2]
        # dates of birth
        self.dobs = []
        self.dob_source = None
        for v in as_list(h.get("dobs")):
            d = parse_date(v)
            if d:
                self.dobs.append(HitDob({d.y}, d.m, d.d, None, "listed"))
        if self.dobs:
            self.dob_source = "listed"
        if not self.dobs and not self.company:
            self.dobs = self._dobs_from_text(self.first_par)
            if self.dobs:
                self.dob_source = "profile notes"
        self.search_used = []
        if not self.dobs and not self.company:
            self.dobs, self.search_used = self._dobs_from_search(data)
            if self.dobs:
                self.dob_source = "search results"

    def _dobs_from_text(self, text, src=None):
        out = []
        t = sval(text)
        # explicit birth
        for m in re.finditer(r"\b(?:born|b\.|date of birth|dob|birth date)\b[:\s]*(?:on|in)?\s*([^.;()]{0,40})", t, re.I):
            seg = m.group(1)
            if re.match(r"\s*(?:to|into|and raised)\b", seg, re.I):
                continue
            ds = dates_in_text(seg)
            if ds:
                d = ds[0][2]
                out.append(HitDob({d.y}, d.m, d.d, src, "born"))
        # aged N ... at/in DATE ; N-year-old ; age N
        for m in AGE_RE.finditer(t):
            age = next(int(g) for g in m.groups() if g)
            if not (10 <= age <= 110):
                continue
            # the date of that statement: nearest date in the same sentence
            sent_start = max(t.rfind(".", 0, m.start()) + 1, 0)
            sent_end = t.find(". ", m.end())
            sent_end = len(t) if sent_end < 0 else sent_end + 1
            ds = [x for x in dates_in_text(t[sent_start:sent_end])]
            if not ds:
                continue
            # prefer the date closest after the age mention
            pos = m.start() - sent_start
            ds.sort(key=lambda x: (0 if x[0] >= pos else 1, abs(x[0] - pos)))
            when = ds[0][2]
            out.append(HitDob(age_years(age, when), None, None, src, "age %d in %s" % (age, when)))
        # de-duplicate
        uniq, seen = [], set()
        for x in out:
            k = (tuple(sorted(x.years)), x.m, x.d)
            if k not in seen:
                seen.add(k)
                uniq.append(x)
        return uniq

    def _dobs_from_search(self, data):
        q = " ".join(sorted(norm_text(self.h.get("name")).split()))
        phrase = CATEGORY_PHRASE.get(self.category, "named on a watchlist")
        out, used = [], []
        for r in sorted(data.search_by_q.get(q, []), key=lambda r: sval(r.get("result_id"))):
            text = sval(r.get("title")) + ". " + sval(r.get("snippet"))
            if phrase not in strip_accents(text).lower():
                continue
            rc = countries_in_text(text)
            if not self.countries or not (rc & self.countries):
                continue
            ds = self._dobs_from_text(sval(r.get("snippet")), r.get("result_id"))
            if ds:
                out.extend(ds)
                used.append(r.get("result_id"))
        return out, used

# ------------------------------------------------------------------ comparing


def dob_agree(cd, hd):
    if cd.y not in hd.years:
        return False
    if cd.m and hd.m and cd.m != hd.m:
        return False
    if cd.d and hd.d and cd.d != hd.d:
        return False
    return True


def dob_contradicts(cd, hd):
    if all(abs(cd.y - y) > 1 for y in hd.years):
        return True
    if cd.y in hd.years and len(hd.years) == 1:
        if cd.m and hd.m and cd.m != hd.m:
            return True
        if cd.m and hd.m and cd.m == hd.m and cd.d and hd.d and cd.d != hd.d:
            return True
    return False


def fmt_list(xs):
    xs = sorted(xs)
    return ", ".join(xs)


class Decision:
    def __init__(self, decision, code, reason, evidence, rid):
        self.decision, self.code, self.reason = decision, code, reason
        self.evidence = sorted({e for e in evidence if e})
        self.rid = rid


def decide(cust, hit, alert, screened_at):
    rid = cust.rids[0] if cust.rids else None
    cust_names, name_src = cust.names_for_agreement()
    hit_srcs = list(hit.search_used)
    contradictions = []   # (code, reason, evidence)

    # ---- type
    if hit.type_known and (cust.records or sval(alert.get("entity_type"))) and cust.company != hit.company:
        contradictions.append(("ENTITY_TYPE_MISMATCH",
                               "The customer is %s but the hit is %s." % ("a company" if cust.company else "an individual",
                                                                          "a company" if hit.company else "an individual"),
                               list(cust.rids)))
    # ---- death
    if hit.death and screened_at and hit.death.key() < screened_at.key():
        contradictions.append(("DECEASED", "The hit's profile gives a date of death of %s, before the customer was screened on %s." % (hit.death, screened_at), []))
    # ---- time
    if hit.earliest_report and cust.dobs:
        rep = hit.earliest_report
        if cust.company:
            inc = cust.dobs
            if all(f.value.key() > rep.key() and (f.value.y > rep.y or (f.value.m and rep.m)) for f in inc):
                f = inc[0]
                contradictions.append(("TEMPORAL_IMPOSSIBILITY",
                                       "The customer was incorporated on %s, after the hit's earliest dated report on %s." % (f.value, rep),
                                       f.src))
        else:
            too_young = []
            for f in cust.dobs:
                d = f.value
                if d.m and d.d and rep.m and rep.d:
                    age = rep.y - d.y - ((rep.m, rep.d) < (d.m, d.d))
                elif d.m and rep.m:
                    age = rep.y - d.y - (rep.m < d.m)
                else:
                    age = rep.y - d.y          # at most this old
                too_young.append(age < 16)
            if too_young and all(too_young):
                f = cust.dobs[0]
                contradictions.append(("TEMPORAL_IMPOSSIBILITY",
                                       "The customer was born in %s (%s), so they were under 16 when the hit's profile reports activity on %s." % (f.value, fmt_list(f.src), rep),
                                       f.src))
    # ---- date of birth (company: incorporation date)
    dob_agrees = False
    agree_pair = None
    if cust.dobs and hit.dobs:
        all_contra = True
        for f in cust.dobs:
            for hd in hit.dobs:
                if not dob_contradicts(f.value, hd):
                    all_contra = False
                if dob_agree(f.value, hd) and agree_pair is None:
                    agree_pair = (f, hd)
        dob_agrees = agree_pair is not None and len(cust.dobs) == 1
        if all_contra:
            f = cust.dobs[0]
            where = {"listed": "listed date of birth is", "profile notes": "profile notes put its birth year at",
                     "search results": "search results put its birth year at"}.get(hit.dob_source, "dates of birth are")
            hits_txt = " or ".join(str(x) for x in hit.dobs)
            if cust.company:
                txt = "The customer's incorporation date is %s (%s) but the hit's incorporation date is %s." % (f.value, fmt_list(f.src), hits_txt)
            else:
                txt = "The customer's date of birth is %s (%s) but the hit's %s %s." % (f.value, fmt_list(f.src), where, hits_txt)
            contradictions.append(("DOB_MISMATCH", txt, list(f.src) + hit_srcs))
    # ---- names
    agreeing_name = names_agree_any(cust_names, hit.names, cust.company)
    cust_parts_ok = any(len(name_parts(n, cust.company)) >= 2 for n in cust_names) or cust.company
    if cust.legal_name and not cust.company:
        lp = name_parts(cust.legal_name.value)
        if len(lp) >= 2 and names_agree_any([cust.legal_name.value], hit.names) is None:
            contradictions.append(("NAME_MISMATCH_VERIFIED",
                                   "The customer's verified name is %s (%s), which does not match the hit's name %s." % (
                                       cust.legal_name.value, fmt_list(cust.legal_name.src), hit.names[0]),
                                   cust.legal_name.src))
    elif cust.legal_name and cust.company:
        lp = name_parts(cust.legal_name.value, True)
        if lp and names_agree_any([cust.legal_name.value], hit.names, True) is None:
            contradictions.append(("NAME_MISMATCH_VERIFIED",
                                   "The customer's verified name is %s (%s), which does not match the hit's name %s." % (
                                       cust.legal_name.value, fmt_list(cust.legal_name.src), hit.names[0]),
                                   cust.legal_name.src))
    # ---- location
    cc = set(cust.countries)
    shared = cc & hit.countries
    if cc and hit.countries and not shared and not hit.unreadable_country and not cust.unreadable_country:
        if agreeing_name and dob_agrees:
            pass  # a country difference does not clear a name+DOB agreement
        else:
            contradictions.append(("LOCATION_MISMATCH",
                                   "The customer is linked only to %s but the hit is linked only to %s." % (fmt_list(cc), fmt_list(hit.countries)),
                                   sorted(cust.country_srcs())))

    if contradictions:
        order = ["ENTITY_TYPE_MISMATCH", "DECEASED", "TEMPORAL_IMPOSSIBILITY", "DOB_MISMATCH", "NAME_MISMATCH_VERIFIED", "LOCATION_MISMATCH"]
        contradictions.sort(key=lambda c: order.index(c[0]))
        code, txt, ev = contradictions[0]
        extra = [c[1] for c in contradictions[1:2]]
        ev = set(ev)
        for c in contradictions[1:]:
            ev |= set(c[2])          # cite what decided every contradiction found
        return Decision("DIFFERENT_ENTITY", code, " ".join([txt] + extra), ev, rid)

    # ---- confirm only on agreement
    loc_txt = ("Both the customer and the hit are linked to %s. " % fmt_list(shared)) if shared else ""
    name_txt = ("The customer's name agrees with the hit's name %s." % agreeing_name) if agreeing_name else ""
    if cust.company:
        if agreeing_name and shared:
            ev = set(name_src) | cust.country_srcs(shared)
            return Decision("SAME_ENTITY", "NAME_LOCATION_MATCH", loc_txt + name_txt, ev, rid)
    else:
        if agreeing_name and dob_agrees and cust_parts_ok:
            f, hd = agree_pair
            dtxt = "The customer's date of birth %s (%s) agrees with the hit's %s. " % (f.value, fmt_list(f.src) or "screened", hd)
            ev = set(name_src) | set(f.src) | cust.country_srcs(shared) | set(hit_srcs)
            code = "NAME_DOB_LOCATION_MATCH" if shared else "NAME_DOB_MATCH"
            return Decision("SAME_ENTITY", code, dtxt + loc_txt + name_txt, ev, rid)

    # ---- review
    bits = []
    if loc_txt:
        bits.append(loc_txt.strip())
    if name_txt:
        bits.append(name_txt)
    missing = []
    if not cust.company:
        if not agreeing_name:
            missing.append("the names do not clearly agree")
        elif not cust_parts_ok:
            missing.append("the customer's name has fewer than two real parts")
        if not hit.dobs:
            missing.append("the hit gives no date of birth to compare")
        elif not cust.dobs:
            missing.append("the customer's date of birth is unknown")
        elif not dob_agrees:
            missing.append("the dates of birth neither agree nor contradict")
    else:
        if not agreeing_name:
            missing.append("the names do not clearly agree")
        if not shared:
            missing.append("no country is shared")
    tail = "Nothing in the customer's records contradicts the hit"
    tail += (", but " + " and ".join(missing) if missing else "") + ", so the hit can be neither cleared nor confirmed."
    bits.append(tail)
    ev = cust.country_srcs(shared) if shared else (set(name_src) | cust.country_srcs(None))
    if cust.dobs and hit.dobs and not shared:
        for f in cust.dobs:
            ev |= set(f.src)
    return Decision("REVIEW", "INSUFFICIENT_DATA", " ".join(bits), ev, rid)


def flatten_alert(a):
    """The screened customer may sit in a nested 'customer' object."""
    a = dict(a)
    c = a.get("customer")
    if isinstance(c, dict):
        for k_src, k_dst in (("name", "customer_name"), ("entity_type", "entity_type"), ("dob", "dob"),
                             ("country", "country"), ("city", "city")):
            if a.get(k_dst) in (None, "") and c.get(k_src) not in (None, ""):
                a[k_dst] = c.get(k_src)
    return a


def run(folder, out_path):
    data = Data(folder)
    rows = []
    for alert in sorted(data.alerts, key=lambda a: sval(a.get("alert_id"))):
        alert = flatten_alert(alert)
        screened_at = parse_date(alert.get("screened_at"))
        persons, how = data.resolve(alert)
        custs = [Customer(data, p, alert) for p in persons] or [Customer(data, [], alert)]
        for hid in as_list(alert.get("hit_ids")):
            h = data.hits.get(hid)
            if h is None:
                continue
            hit = Hit(data, h)
            ds = [decide(c, hit, alert, screened_at) for c in custs]
            if len(ds) == 1:
                d = ds[0]
                rid = d.rid
            else:
                decs = {x.decision for x in ds}
                if len(decs) == 1:
                    d = ds[0]
                    ev = set()
                    for x in ds:
                        ev |= set(x.evidence)
                    d = Decision(d.decision, d.code, d.reason, ev, "AMBIGUOUS")
                else:
                    ev = set()
                    for x in ds:
                        ev |= set(x.rid and [x.rid] or [])
                    parts = "; ".join("record %s would be %s" % (x.rid, x.decision) for x in ds)
                    d = Decision("REVIEW", "AMBIGUOUS_CUSTOMER_RECORD",
                                 "%d different people in the system of record fit the screened details and they lead to different decisions (%s), so a person must decide." % (len(ds), parts),
                                 ev, "AMBIGUOUS")
                rid = "AMBIGUOUS"
            rows.append({"hit_id": hid, "alert_id": alert.get("alert_id"), "decision": d.decision,
                         "reason_code": d.code, "customer_record_id": rid, "reason": d.reason,
                         "evidence": d.evidence})
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=False) + "\n")
    return rows


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: run <data folder> <output file>", file=sys.stderr)
        sys.exit(2)
    rows = run(sys.argv[1], sys.argv[2])
    print("wrote %d hits to %s" % (len(rows), sys.argv[2]), file=sys.stderr)
