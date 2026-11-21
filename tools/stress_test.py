#!/usr/bin/env python3
"""Reworded-data stress test: imitate the unseen set and check decisions hold.

    python3 tools/stress_test.py <dev folder>

Builds a perturbed copy of the data room, with the same facts written
differently, runs the engine on both, and reports how many hits keep the same
decision and reason code. Every perturbation keeps the meaning, so a robust
system should not change its answers. Seeded, so the test itself is repeatable.

Perturbations (each applied to a random share of records):
  notes      verification notes rewritten into new sentence templates; support
             notes about past residence rewritten
  dates      ISO / day-first slashes / "10 June 1962" / "June 10, 1962"
  names      "First Last" <-> "LAST, First", casing changes, accents stripped
  countries  country names <-> ISO codes / nationality adjectives
  ages       profile and search ages reworded ("aged 45" <-> "45-year-old")
"""
import json, os, random, re, shutil, subprocess, sys, tempfile, unicodedata
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "system"))
import engine  # noqa: E402

rng = random.Random(20261121)
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
CODE = {"United Kingdom": "GB", "United States": "US", "Nigeria": "NG", "Germany": "DE", "France": "FR", "Spain": "ES", "Italy": "IT",
        "Bangladesh": "BD", "Pakistan": "PK", "India": "IN", "Brazil": "BR", "Netherlands": "NL", "Canada": "CA", "Australia": "AU",
        "Ireland": "IE", "Singapore": "SG", "South Africa": "ZA", "China": "CN", "Russia": "RU", "Ghana": "GH", "Mexico": "MX"}
ADJ = {"United Kingdom": "British", "United States": "American", "Nigeria": "Nigerian", "Germany": "German", "France": "French",
       "Spain": "Spanish", "Italy": "Italian", "Bangladesh": "Bangladeshi", "Pakistan": "Pakistani", "India": "Indian",
       "Brazil": "Brazilian", "Netherlands": "Dutch", "Canada": "Canadian", "Australia": "Australian", "Ireland": "Irish"}


def strip_acc(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def fmt_date(d, style):
    if d is None or d.m is None or d.d is None:
        return str(d) if d else None
    return {0: "%04d-%02d-%02d" % (d.y, d.m, d.d),
            1: "%02d/%02d/%04d" % (d.d, d.m, d.y),
            2: "%d %s %d" % (d.d, MONTHS[d.m - 1], d.y),
            3: "%s %d, %d" % (MONTHS[d.m - 1], d.d, d.y),
            4: "%d %s %d" % (d.d, MONTHS[d.m - 1][:3], d.y)}[style]


def redate(v, p=0.5):
    if not v or rng.random() > p:
        return v
    d = engine.parse_date(v)
    if d is None or not d.full():
        return v
    return fmt_date(d, rng.randrange(5))


def recountry(v, p=0.4):
    if not v or rng.random() > p:
        return v
    c = engine.country_of(v)
    for canon, code in CODE.items():
        if c and engine.country_of(canon) == c:
            return rng.choice([code, ADJ.get(canon, canon), canon.upper()])
    return v


def rename(v, p=0.4):
    if not v or rng.random() > p:
        return v
    parts = v.split()
    if len(parts) >= 2 and "," not in v and rng.random() < 0.5:
        v = "%s, %s" % (parts[-1].upper(), " ".join(parts[:-1]))
    elif rng.random() < 0.5:
        v = v.upper()
    if rng.random() < 0.5:
        v = strip_acc(v)
    return v


KYC_TEMPLATES = [
    "Identity confirmed for {NAME}: {prov} checked a {doc} issued in {country} on {when}; date of birth {dob}.",
    "{prov} report dated {when} - {doc} ({country}) belongs to {NAME}, who was born {dob}. Outcome: approved.",
    "Onboarding team: verified {NAME} (birth date {dob}) with a {country} {doc} via {prov} on {when}.",
    "KYC passed on {when}. Subject {NAME}, d.o.b. {dob}, document: {doc} from {country}, provider {prov}.",
    "We approved {NAME}'s {doc} from {country} through {prov} on {when}. The document gives {dob} as the birth date.",
    "{when}: {prov} cleared {NAME}. Born {dob}. Document {doc}, country of issue {country}.",
]
LIVED_TEMPLATES = [
    "Customer said they spent several years living in {country} before moving here.",
    "During the call the client mentioned a previous address in {country}.",
    "Client explained they were resident in {country} for a while, which is why they send money there.",
]


def reword_note(n):
    text = n.get("text") or ""
    chk = engine.note_check(n)
    if chk and chk.get("ok") and chk.get("name") and chk.get("dob") and rng.random() < 0.7:
        cs = sorted(chk["countries"])
        if len(cs) == 1:
            prov = (re.search(r"\b(Onfido|Persona|Sumsub|Veriff|Jumio|Trulioo)\b", text) or [None, "the provider"])[1]
            doc = (re.search(r"(passport|national ID|driving licence|residence permit)", text) or [None, "ID document"])[1]
            n = dict(n)
            n["text"] = rng.choice(KYC_TEMPLATES).format(
                NAME=chk["name"].upper(), prov=prov, doc=doc, country=cs[0], when=n.get("created_at"),
                dob=fmt_date(chk["dob"], rng.randrange(5)) if chk["dob"].full() else str(chk["dob"]))
            return n, "note"
    if chk is None and rng.random() < 0.7:
        cs = sorted(engine.countries_in_text(text))
        if len(cs) == 1 and re.search(r"\b(liv|resid|home|moved|work)", text, re.I):
            n = dict(n)
            n["text"] = rng.choice(LIVED_TEMPLATES).format(country=cs[0])
            return n, "lived-note"
    return n, None


AGE_FORMS = ["aged {a}", "{a}-year-old", "who was {a} at the time", "then {a}", "at the age of {a}"]


def reword_ages(text):
    if not text or rng.random() > 0.6:
        return text

    def sub(m):
        a = m.group(1) or m.group(2)
        return rng.choice(AGE_FORMS).format(a=a)
    return re.sub(r"\baged (\d{2,3})\b|\b(\d{2,3})-year-old\b", sub, text)


def perturb(src, dst):
    os.makedirs(dst, exist_ok=True)
    stats = Counter()

    def rw(name, fn):
        with open(os.path.join(src, name)) as f, open(os.path.join(dst, name), "w") as g:
            for line in f:
                r = json.loads(line)
                r = fn(r)
                g.write(json.dumps(r, ensure_ascii=False) + "\n")

    def cust(r):
        for k in ("dob",):
            v = redate(r.get(k)); stats["date"] += v != r.get(k); r[k] = v
        for k in ("nationality", "residence_country"):
            v = recountry(r.get(k)); stats["country"] += v != r.get(k); r[k] = v
        v = rename(r.get("full_name")); stats["name"] += v != r.get("full_name"); r["full_name"] = v
        return r

    def kyc(r):
        v = redate(r.get("verified_dob")); stats["date"] += v != r.get("verified_dob"); r["verified_dob"] = v
        v = recountry(r.get("document_country")); stats["country"] += v != r.get("document_country"); r["document_country"] = v
        v = rename(r.get("verified_name"), 0.3); stats["name"] += v != r.get("verified_name"); r["verified_name"] = v
        return r

    def note(r):
        r2, kind = reword_note(r)
        if kind:
            stats[kind] += 1
        return r2

    def hit(r):
        new = [redate(x, 0.4) for x in (r.get("dobs") or [])]
        stats["date"] += new != (r.get("dobs") or []); r["dobs"] = new
        pn = r.get("profile_notes")
        if pn:
            parts = pn.split("\n\n")
            parts[0] = reword_ages(parts[0])           # dated report lines are left as they are (policy: same wording)
            r["profile_notes"] = "\n\n".join(parts)
            stats["age"] += r["profile_notes"] != pn
        return r

    def sr(r):
        v = reword_ages(r.get("snippet")); stats["age"] += v != r.get("snippet"); r["snippet"] = v
        return r

    rw("customers.jsonl", cust)
    rw("kyc_checks.jsonl", kyc)
    rw("notes.jsonl", note)
    rw("hits.jsonl", hit)
    rw("search_results.jsonl", sr)
    for f in os.listdir(src):
        if not os.path.exists(os.path.join(dst, f)):
            shutil.copy(os.path.join(src, f), os.path.join(dst, f))
    return stats


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "dev"
    tmp = tempfile.mkdtemp()
    base_out, pert_out = os.path.join(tmp, "base.jsonl"), os.path.join(tmp, "pert.jsonl")
    run = os.path.join(HERE, "..", "system", "run")
    subprocess.run([run, src, base_out], check=True, stderr=subprocess.DEVNULL)
    stats = perturb(src, os.path.join(tmp, "room"))
    subprocess.run([run, os.path.join(tmp, "room"), pert_out], check=True, stderr=subprocess.DEVNULL)
    a = {json.loads(l)["hit_id"]: json.loads(l) for l in open(base_out)}
    b = {json.loads(l)["hit_id"]: json.loads(l) for l in open(pert_out)}
    same = sum(a[h]["decision"] == b[h]["decision"] for h in a)
    same_code = sum(a[h]["decision"] == b[h]["decision"] and a[h]["reason_code"] == b[h]["reason_code"] for h in a)
    new_clear = [h for h in a if b[h]["decision"] == "DIFFERENT_ENTITY" and a[h]["decision"] != "DIFFERENT_ENTITY"]
    print("perturbations applied:", dict(stats))
    print("decision stable: %d / %d (%.1f%%)" % (same, len(a), 100.0 * same / len(a)))
    print("decision + reason code stable: %d / %d" % (same_code, len(a)))
    print("NEW clearances caused by rewording (the dangerous direction): %d" % len(new_clear))
    flips = Counter((a[h]["decision"], b[h]["decision"]) for h in a if a[h]["decision"] != b[h]["decision"])
    print("changes:", dict(flips))
    for h in [h for h in a if a[h]["decision"] != b[h]["decision"]][:15]:
        print("  %s %s -> %s %s | %s" % (h, a[h]["decision"], b[h]["decision"], b[h]["reason_code"], b[h]["reason"][:150]))
    return 0 if not new_clear else 1


if __name__ == "__main__":
    sys.exit(main())
