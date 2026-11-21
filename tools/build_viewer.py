#!/usr/bin/env python3
"""Doppelganger console: one self-contained HTML page for reviewing and demoing decisions.

    python3 tools/build_viewer.py <data folder> <output.jsonl> <viewer.html>

For every hit it shows a mirror: the customer on the left, the listed person on the right, and between
them each fact the policy compares (name, date of birth, countries, type, death, earliest report) marked
as agreeing, contradicting or unknown, with the fact that decided the hit highlighted. Below that is the
trail an analyst would follow (alert, link, records, identity checks, notes, hit sources), with every
record ID openable to its raw data.

The page embeds data from the data folder: keep it local (it is git-ignored),
because the dev data is for event participants only.
"""
import json, os, sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "system"))
import engine  # noqa: E402

S = engine.sval

DECISIVE = {
    "DOB_MISMATCH": "dob", "NAME_MISMATCH_VERIFIED": "name", "LOCATION_MISMATCH": "countries",
    "ENTITY_TYPE_MISMATCH": "type", "DECEASED": "status", "TEMPORAL_IMPOSSIBILITY": "report",
    "NAME_DOB_LOCATION_MATCH": "name dob countries", "NAME_DOB_MATCH": "name dob", "NAME_LOCATION_MATCH": "name countries",
}


def mark_rows(cust, hit, screened_at, alert):
    """The facts the policy compares, each marked '=', '≠' or '?' with a short note."""
    rows = []
    names, _ = cust.names_for_agreement()
    agreeing = engine.names_agree_any(names, hit.names, cust.company) if names and hit.names else None
    if agreeing:
        rows.append(("name", "=", "agrees with “%s”" % agreeing))
    elif cust.legal_name and not engine.non_latin(cust.legal_name.value):
        rows.append(("name", "≠", "verified name agrees with none of the hit's names"))
    elif names:
        rows.append(("name", "?", "record name differs, but no verified name to rely on"))
    else:
        rows.append(("name", "?", "no usable name"))
    # date of birth
    if cust.dobs and hit.dobs:
        agree = any(engine.dob_agree(f.value, h) for f in cust.dobs for h in hit.dobs)
        contra = all(engine.dob_contradicts(f.value, h) for f in cust.dobs for h in hit.dobs)
        if getattr(hit, "conflict_note", None):
            rows.append(("dob", "?", "the hit's own sources disagree"))
        elif agree:
            rows.append(("dob", "=", "within the hit's possible birth dates"))
        elif contra:
            rows.append(("dob", "≠", "outside every birth date the hit gives"))
        else:
            rows.append(("dob", "?", "close but not the same"))
    else:
        rows.append(("dob", "?", "missing on one side"))
    cc, hc = set(cust.countries), set(hit.countries)
    if cc and hc:
        shared = cc & hc
        rows.append(("countries", "=" if shared else "≠", ("both tied to " + ", ".join(sorted(shared))) if shared else "no country in common"))
    else:
        rows.append(("countries", "?", "missing on one side"))
    if hit.type_known:
        same_type = cust.company == hit.company
        rows.append(("type", "=" if same_type else "≠", "both companies" if same_type and cust.company else
                     "both individuals" if same_type else "a person and a company"))
    else:
        rows.append(("type", "?", "hit type not given"))
    if hit.death:
        before = screened_at and hit.death.key() < screened_at.key()
        rows.append(("status", "≠" if before else "?", "died %s, before screening" % hit.death if before else "died %s" % hit.death))
    else:
        rows.append(("status", "=", "no death recorded"))
    if hit.earliest_report and cust.dobs:
        d = cust.dobs[0].value
        age = hit.earliest_report.y - d.y
        rows.append(("report", "≠" if age < 16 else "=", "customer about %d at the earliest report (%s)" % (age, hit.earliest_report)))
    else:
        rows.append(("report", "?", "no dated report" if not hit.earliest_report else "customer birth date unknown"))
    return rows


def customer_side(cust):
    names, src = cust.names_for_agreement()
    return {
        "name": names[0] if names else "",
        "name_src": src[:3],
        "verified": bool(cust.legal_name),
        "dob": " or ".join(str(f.value) for f in cust.dobs),
        "dob_src": sorted({x for f in cust.dobs for x in f.src})[:3],
        "countries": [{"c": c, "src": sorted(s)[:3]} for c, s in sorted(cust.countries.items())],
        "type": "Company" if cust.company else "Individual",
    }


def hit_side(hit, h):
    return {
        "name": hit.names[0] if hit.names else "",
        "aliases": hit.names[1:8],
        "dob": " or ".join(str(x) for x in hit.dobs),
        "dob_from": hit.dob_source or "",
        "dob_src": list(hit.search_used),
        "alt_dob": " or ".join(str(x) for x in hit.alt_dobs) if getattr(hit, "conflict_note", None) else "",
        "countries": sorted(hit.countries),
        "type": S(h.get("entity_type")) or "not given",
        "category": S(h.get("primary_category")) or ", ".join(engine.as_list(h.get("categories"))),
        "position": S(h.get("position_or_allegation")),
        "death": str(hit.death or ""),
        "report": str(hit.earliest_report or ""),
        "score": h.get("match_score"),
    }


def build(folder, out_path):
    data = engine.Data(folder)
    decisions = {}
    for l in open(out_path, encoding="utf-8"):
        if l.strip():
            r = json.loads(l)
            decisions[r["hit_id"]] = r
    raw = {}
    kyc_by_id = {k.get("check_id"): k for k in data.kyc}
    notes_by_id = {n.get("note_id"): n for n in data.notes}
    sr_by_id = {r.get("result_id"): r for r in data.search}
    items = []
    for alert in sorted(data.alerts, key=lambda a: S(a.get("alert_id"))):
        fa = engine.flatten_alert(alert)
        screened_at = engine.parse_date(fa.get("screened_at"))
        persons, how = data.resolve(fa)
        anchors = list(data.last_anchors)
        custs = [engine.Customer(data, p, fa) for p in persons] or [engine.Customer(data, [], fa)]
        raw[fa.get("alert_id")] = {k: v for k, v in alert.items()}
        people = []
        for c in custs:
            checks = []
            for r in c.rids:
                for o in data.onb_by_cust.get(r, []):
                    for k in data.kyc_by_app.get(S(o.get("kyc_applicant_id")), []):
                        raw[k.get("check_id")] = k
                        checks.append({"id": k.get("check_id"), "name": S(k.get("verified_name")), "dob": S(k.get("verified_dob")),
                                       "country": S(k.get("document_country")), "provider": S(k.get("provider")),
                                       "doc": S(k.get("document_type")), "result": S(k.get("result")), "date": S(k.get("checked_at"))})
            for x in c.records:
                raw[x["record_id"]] = x
            for n in c.notes:
                raw[n.get("note_id")] = n
            people.append({
                "records": [{"id": x["record_id"], "name": S(x.get("full_name")), "dob": S(x.get("dob")),
                             "nat": S(x.get("nationality")), "res": S(x.get("residence_country")), "city": S(x.get("city")),
                             "status": S(x.get("status")), "reached": x["record_id"] in anchors} for x in c.records],
                "checks": sorted(checks, key=lambda k: k["date"]),
                "verified_id": c.verified and c.verified.get("id"),
                "notes": [{"id": n.get("note_id"), "date": S(n.get("created_at")), "channel": S(n.get("channel")),
                           "text": S(n.get("text"))[:420]} for n in c.notes][-8:],
            })
        for hid in engine.as_list(alert.get("hit_ids")):
            h = data.hits.get(hid)
            if not h or hid not in decisions:
                continue
            hit = engine.Hit(data, h)
            engine.decide(custs[0], hit, fa, screened_at)      # sets hit.conflict_note for the mirror
            raw[hid] = h
            for sid in list(hit.search_used) + list(hit.alt_used):
                if sid in sr_by_id:
                    raw[sid] = sr_by_id[sid]
            d = decisions[hid]
            items.append({
                "hit": hid, "alert": fa.get("alert_id"),
                "screened": {"name": S(fa.get("customer_name")), "dob": S(fa.get("dob")), "country": S(fa.get("country")),
                             "city": S(fa.get("city")), "type": S(fa.get("entity_type")), "at": S(fa.get("screened_at"))},
                "link": how or "", "people": people,
                "cust": customer_side(custs[0]), "watch": hit_side(hit, h),
                "rows": mark_rows(custs[0], hit, screened_at, fa),
                "decisive": DECISIVE.get(d["reason_code"], ""),
                "notes": (S(h.get("profile_notes")) or "")[:700],
                "decision": d,
            })
    used = set()
    for it in items:
        used |= set(it["decision"].get("evidence") or [])
        used.add(it["hit"]); used.add(it["alert"])
        for p in it["people"]:
            used |= {r["id"] for r in p["records"]} | {k["id"] for k in p["checks"]} | {n["id"] for n in p["notes"]}
        used |= set(it["cust"]["name_src"]) | set(it["cust"]["dob_src"]) | set(it["watch"]["dob_src"])
    raw = {k: v for k, v in raw.items() if k in used}
    return items, raw


def summary(items, labels_path):
    dec = Counter(i["decision"]["decision"] for i in items)
    codes = Counter((i["decision"]["decision"], i["decision"]["reason_code"]) for i in items)
    practice = None
    if labels_path and os.path.exists(labels_path):
        cost = {("DIFFERENT_ENTITY", "DIFFERENT_ENTITY"): 0, ("DIFFERENT_ENTITY", "SAME_ENTITY"): 3, ("DIFFERENT_ENTITY", "REVIEW"): 3,
                ("SAME_ENTITY", "DIFFERENT_ENTITY"): 60, ("SAME_ENTITY", "SAME_ENTITY"): 0, ("SAME_ENTITY", "REVIEW"): 2,
                ("REVIEW", "DIFFERENT_ENTITY"): 60, ("REVIEW", "SAME_ENTITY"): 2, ("REVIEW", "REVIEW"): 0}
        by = {i["hit"]: i["decision"]["decision"] for i in items}
        labs = [json.loads(l) for l in open(labels_path) if l.strip()]
        c = sum(cost[(x["decision"], by.get(x["hit_id"], "REVIEW"))] for x in labs)
        base = sum(cost[(x["decision"], "REVIEW")] for x in labs)
        right = sum(by.get(x["hit_id"]) == x["decision"] for x in labs)
        unsafe = sum(by.get(x["hit_id"]) == "DIFFERENT_ENTITY" and x["decision"] != "DIFFERENT_ENTITY" for x in labs)
        practice = {"n": len(labs), "right": right, "cost": c, "base": base, "unsafe": unsafe,
                    "labelled": [x["hit_id"] for x in labs]}
    return {"total": len(items), "decisions": dec, "codes": [[a, b, n] for (a, b), n in sorted(codes.items(), key=lambda x: -x[1])],
            "practice": practice}


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Doppelganger console</title>
<style>
:root{
  --fog:#E8ECF1; --panel:#FFFFFF; --ink:#172033; --mute:#5D687B; --line:#D2D9E2; --soft:#F3F5F8;
  --agree:#2D5BA8; --contra:#A3313C; --open:#8A6A1F;
  --diff:#22684C; --same:#A3313C; --rev:#8A6A1F;
  --serif:"Iowan Old Style","Charter","Sitka Text","Cambria","Georgia",serif;
  --sans:ui-sans-serif,-apple-system,"Segoe UI","Helvetica Neue",Arial,sans-serif;
}
*{box-sizing:border-box}
html,body{height:100%}
body{margin:0;overflow-x:hidden;background:var(--fog);color:var(--ink);font:14px/1.5 var(--sans);font-variant-numeric:tabular-nums}
button{font:inherit;color:inherit}
:focus-visible{outline:2px solid var(--agree);outline-offset:2px}
.app{display:grid;grid-template-rows:auto 1fr;height:100vh}
header{display:flex;align-items:center;gap:28px;padding:14px 24px;background:var(--ink);color:#fff}
.brand{font:600 22px/1 var(--serif);letter-spacing:.01em}
.brand small{display:block;font:400 12.5px/1.4 var(--sans);color:#B9C3D3;margin-top:4px;letter-spacing:0}
.mix{flex:1;max-width:560px}
.mixbar{display:flex;height:10px;border-radius:5px;overflow:hidden;background:#2A3550}
.mixbar button{border:0;padding:0;cursor:pointer;height:100%}
.mixkey{display:flex;gap:18px;font-size:12.5px;color:#C9D1DE;margin-top:7px}
.mixkey b{color:#fff;font-weight:600}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.d-DIFFERENT_ENTITY{background:var(--diff)}.d-SAME_ENTITY{background:var(--same)}.d-REVIEW{background:var(--rev)}
.hbtn{margin-left:auto;background:transparent;border:1px solid #4A5670;color:#fff;border-radius:6px;padding:6px 12px;cursor:pointer}
.hbtn:hover{border-color:#B9C3D3}
.body{display:grid;grid-template-columns:330px 1fr;min-height:0}
aside{background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;min-height:0}
.find{padding:14px;border-bottom:1px solid var(--line);display:grid;gap:10px}
.find input,.find select{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--soft);font:inherit}
.seg{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line);border-radius:6px;overflow:hidden}
.seg button{border:0;background:var(--panel);padding:6px 0;font-size:12.5px;cursor:pointer;border-right:1px solid var(--line)}
.seg button:last-child{border-right:0}
.seg button[aria-pressed=true]{background:var(--ink);color:#fff}
.count{font-size:12px;color:var(--mute)}
#list{overflow:auto;flex:1}
.item{display:block;width:100%;text-align:left;border:0;border-bottom:1px solid var(--line);background:var(--panel);padding:10px 14px;cursor:pointer}
.item:hover{background:var(--soft)}
.item[aria-current=true]{background:#E3EAF5;box-shadow:inset 3px 0 0 var(--agree)}
.item .who{font:15px/1.3 var(--serif)}
.item .vs{color:var(--mute);font-size:12.5px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item .meta{font-size:12px;color:var(--mute);margin-top:4px}
.lab{font-size:11px;border:1px solid var(--line);border-radius:4px;padding:0 5px;margin-left:6px;color:var(--mute)}
main{overflow:auto;min-height:0}
.page{max-width:1080px;margin:0 auto;padding:28px 32px 64px}
.verdict{display:grid;grid-template-columns:auto 1fr;gap:4px 18px;align-items:baseline;margin-bottom:22px}
.verdict .word{font:600 30px/1.1 var(--serif);grid-row:span 2}
.verdict .why{font-size:15px}
.verdict .ids{font-size:12.5px;color:var(--mute)}
.c-DIFFERENT_ENTITY{color:var(--diff)}.c-SAME_ENTITY{color:var(--same)}.c-REVIEW{color:var(--rev)}
/* the mirror */
.mirror{display:grid;grid-template-columns:minmax(0,1fr) 168px minmax(0,1fr);grid-auto-flow:row dense;background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden}
.mhead{padding:16px 20px 12px;border-bottom:1px solid var(--line)}
.mhead .role{font-size:12.5px;color:var(--mute)}
.mhead .nm{font:600 22px/1.2 var(--serif);margin-top:2px}
.mhead .sub{font-size:12.5px;color:var(--mute);margin-top:4px}
.mhead.right{text-align:right}
.mirror .left{grid-column:1}.mirror .spine,.mirror .spinehead{grid-column:2}.mirror .right{grid-column:3}
.spinehead{border-bottom:1px solid var(--line);background:var(--soft);display:flex;align-items:flex-end;justify-content:center;padding-bottom:12px;font-size:12px;color:var(--mute)}
.cell{padding:11px 20px;border-bottom:1px solid var(--line);min-height:52px}
.cell.right{text-align:right}
.cell .v{font-size:14.5px}
.cell .src{margin-top:3px}
.spine{border-bottom:1px solid var(--line);background:var(--soft);display:grid;grid-template-columns:1fr;justify-items:center;align-content:center;padding:6px 8px;text-align:center}
.spine .f{font-size:12px;color:var(--mute)}
.spine .m{width:30px;height:30px;border-radius:50%;display:grid;place-items:center;font:700 19px/1 var(--sans);margin:4px 0 3px;border:1.5px solid currentColor;background:var(--panel)}
.spine .n{font-size:11.5px;color:var(--mute);line-height:1.3}
.m-eq{color:var(--agree)}.m-ne{color:var(--contra);background:#FBEDEE!important}.m-q{color:#8D97A6;border-style:dashed!important}
.row-decisive .cell{background:#FBF7EA}
.row-decisive .spine{background:#F3E9C8}
.row-decisive .spine .f{color:var(--ink);font-weight:600}
.mrow{display:contents}
.mrow:last-child>*{border-bottom:0}
/* chips */
.chip{display:inline-block;border:1px solid var(--line);background:var(--soft);border-radius:4px;padding:0 6px;margin:2px 3px 0 0;font-size:12px;cursor:pointer;color:var(--ink)}
.chip:hover{border-color:var(--agree)}
.chip.used{border-color:var(--agree);background:#E3EAF5}
.reason{margin:22px 0 8px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 20px}
.reason h2,.trail h2{font:600 17px/1.3 var(--serif);margin:0 0 8px}
.reason p{margin:0;max-width:78ch;font-size:15px}
.reason .ev{margin-top:10px;font-size:12.5px;color:var(--mute)}
/* the trail is a real sequence */
.trail{margin-top:26px}
.steps{list-style:none;margin:0;padding:0;counter-reset:s}
.steps>li{position:relative;padding:0 0 20px 40px;counter-increment:s}
.steps>li:before{content:counter(s);position:absolute;left:0;top:0;width:24px;height:24px;border-radius:50%;background:var(--ink);color:#fff;font-size:12px;display:grid;place-items:center}
.steps>li:after{content:"";position:absolute;left:11.5px;top:26px;bottom:2px;border-left:1px solid var(--line)}
.steps>li:last-child:after{display:none}
.steps h3{margin:2px 0 6px;font-size:14.5px;font-weight:600}
.steps p{margin:0 0 6px;color:var(--mute);max-width:78ch}
table.t{border-collapse:collapse;width:100%;background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden;font-size:13px}
table.t th{text-align:left;font-weight:600;font-size:12px;color:var(--mute);background:var(--soft);padding:6px 10px;border-bottom:1px solid var(--line)}
table.t td{padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
table.t tr:last-child td{border-bottom:0}
tr.off td{color:#97A0AE;text-decoration:line-through;text-decoration-color:#C3CAD4}
tr.used td{background:#EEF3FB}
.note{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:6px 0;font-size:13px;max-width:86ch}
.note.used{border-color:var(--agree);box-shadow:inset 3px 0 0 var(--agree)}
.pre{white-space:pre-wrap;background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:12.5px;max-width:86ch}
/* drawer */
.drawer{visibility:hidden;position:fixed;top:0;right:0;bottom:0;width:min(520px,94vw);background:var(--panel);border-left:1px solid var(--line);box-shadow:-12px 0 32px rgba(23,32,51,.12);transform:translateX(100%);transition:transform .18s ease-out;display:flex;flex-direction:column;z-index:5}
.drawer.open{transform:none;visibility:visible}
.drawer header{background:var(--soft);color:var(--ink);padding:12px 16px;border-bottom:1px solid var(--line);gap:10px}
.drawer header b{font:600 16px var(--serif)}
.drawer header button{margin-left:auto;border:1px solid var(--line);background:var(--panel);border-radius:6px;padding:4px 10px;cursor:pointer}
.drawer dl{margin:0;padding:14px 16px;overflow:auto;display:grid;grid-template-columns:150px 1fr;gap:6px 12px;font-size:13px}
.drawer dt{color:var(--mute)}
.drawer dd{margin:0;white-space:pre-wrap;word-break:break-word}
/* summary */
.sumgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:18px 0 26px}
.tile{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px 18px}
.tile .k{font-size:13px;color:var(--mute)}
.tile .big{font:600 34px/1.1 var(--serif);margin:6px 0 2px}
.tile p{margin:6px 0 0;font-size:13px;color:var(--mute)}
h1.title{font:600 30px/1.15 var(--serif);margin:0 0 6px}
.lede{font-size:15.5px;max-width:70ch;color:var(--mute);margin:0}
.bars td.bar{width:42%}
.bars .b{height:8px;border-radius:4px}
.kbd{font-size:12px;color:var(--mute);margin-top:22px}
kbd{border:1px solid var(--line);border-bottom-width:2px;border-radius:4px;padding:0 5px;background:var(--panel);font:12px var(--sans)}
.cell,.mhead{overflow-wrap:anywhere}
@media (max-width:1100px){.mirror{grid-template-columns:minmax(0,1fr) 120px minmax(0,1fr)}.spine .n{font-size:11px}}
@media (max-width:900px){
  .body{grid-template-columns:minmax(0,1fr)}
  aside{max-height:42vh;border-right:0;border-bottom:1px solid var(--line)}
  main{overflow:visible}.app{height:auto;min-height:100vh}
  .sumgrid{grid-template-columns:1fr}
  header{flex-wrap:wrap;gap:14px}
  .mix{order:3;flex-basis:100%;max-width:none}.mixkey{flex-wrap:wrap;gap:6px 14px}
  .page{padding:20px 16px 48px}
  .verdict{grid-template-columns:1fr}.verdict .word{grid-row:auto;font-size:26px}
}
@media (max-width:700px){
  .mirror{grid-template-columns:minmax(0,1fr) minmax(0,1fr)}
  .mirror .spinehead{display:none}
  .mirror .spine{grid-column:1/-1;grid-template-columns:auto auto 1fr;justify-items:start;align-items:center;gap:0 10px;text-align:left;padding:8px 14px}
  .mirror .left{grid-column:1}.mirror .right{grid-column:2}
  .spine .m{width:24px;height:24px;font-size:15px;margin:0}
  .spine .n{font-size:12px}
  .cell,.mhead{padding:10px 14px}.mhead .nm{font-size:18px}
  .steps>li{padding-left:34px}
  table.t{display:block;overflow-x:auto}
}
@media (prefers-reduced-motion:reduce){.drawer{transition:none}}
</style></head><body>
<div class="app">
<header>
  <div class="brand">Doppelganger<small>Is the customer the person on the watchlist?</small></div>
  <div class="mix"><div class="mixbar" id="mixbar" role="group" aria-label="Filter by decision"></div><div class="mixkey" id="mixkey"></div></div>
  <button class="hbtn" id="home">Run summary</button>
</header>
<div class="body">
<aside>
  <div class="find">
    <input id="q" type="search" placeholder="Search a name, hit or alert ID" aria-label="Search hits">
    <div class="seg" id="seg" role="group" aria-label="Decision"></div>
    <select id="code" aria-label="Reason"><option value="">Any reason</option></select>
    <div class="count" id="count"></div>
  </div>
  <div id="list" role="list"></div>
</aside>
<main id="main"><div class="page" id="page"></div></main>
</div></div>
<div class="drawer" id="drawer" role="dialog" aria-modal="false" aria-labelledby="dtitle">
  <header><b id="dtitle"></b><button id="dclose">Close</button></header><dl id="dbody"></dl>
</div>
<script>
const DATA=__DATA__, RAW=__RAW__, SUM=__SUM__;
const E=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const WORD={DIFFERENT_ENTITY:"Not the same person",SAME_ENTITY:"Same person",REVIEW:"A person decides"};
const SHORT={DIFFERENT_ENTITY:"Different",SAME_ENTITY:"Same",REVIEW:"Review"};
const CODE={TEMPORAL_IMPOSSIBILITY:"the customer was too young at the earliest report",DOB_MISMATCH:"the dates of birth contradict",
 LOCATION_MISMATCH:"they share no country",NAME_MISMATCH_VERIFIED:"the verified name matches none of the hit's names",
 ENTITY_TYPE_MISMATCH:"one is a person and the other a company",DECEASED:"the listed person died before screening",
 NAME_DOB_LOCATION_MATCH:"name, date of birth and country all agree",NAME_DOB_MATCH:"name and date of birth agree",
 NAME_LOCATION_MATCH:"company name and country agree",INSUFFICIENT_DATA:"nothing settles it either way",
 AMBIGUOUS_CUSTOMER_RECORD:"more than one customer fits, and they lead to different answers"};
const FACT={name:"Name",dob:"Date of birth",countries:"Countries",type:"Type",status:"Alive",report:"Earliest report"};
const ORDER=["DIFFERENT_ENTITY","SAME_ENTITY","REVIEW"];
const LABELLED=new Set((SUM.practice&&SUM.practice.labelled)||[]);
let st={q:"",dec:"",code:"",sel:null};
const $=id=>document.getElementById(id);

/* header mix */
(function(){
  const tot=SUM.total;
  $("mixbar").innerHTML=ORDER.map(k=>`<button class="d-${k}" style="width:${100*(SUM.decisions[k]||0)/tot}%" title="Show ${E(SHORT[k])} only" aria-label="Show ${E(WORD[k])} only" data-k="${k}"></button>`).join("");
  $("mixkey").innerHTML=ORDER.map(k=>`<span><span class="dot d-${k}"></span>${E(WORD[k])} <b>${SUM.decisions[k]||0}</b></span>`).join("")+`<span>of <b>${tot}</b> hits</span>`;
  $("mixbar").onclick=e=>{const k=e.target.dataset.k;if(k){st.dec=k;syncSeg();draw()}};
  const seg=$("seg");
  [["","All"],...ORDER.map(k=>[k,SHORT[k]])].forEach(([k,l])=>{const b=document.createElement("button");b.textContent=l;b.dataset.k=k;b.onclick=()=>{st.dec=k;syncSeg();draw()};seg.appendChild(b)});
  const codes=[...new Set(DATA.map(d=>d.decision.reason_code))].sort();
  $("code").innerHTML+=codes.map(c=>`<option value="${c}">${E(CODE[c]||c)}</option>`).join("");
  $("code").onchange=e=>{st.code=e.target.value;draw()};
  $("q").oninput=e=>{st.q=e.target.value.toLowerCase();draw()};
  $("home").onclick=()=>{st.sel=null;draw();summary()};
  syncSeg();
})();
function syncSeg(){[...$("seg").children].forEach(b=>b.setAttribute("aria-pressed",b.dataset.k===st.dec))}

function visible(){
  return DATA.filter(d=>(!st.dec||d.decision.decision===st.dec)&&(!st.code||d.decision.reason_code===st.code)&&
    (!st.q||(d.hit+" "+d.alert+" "+d.screened.name+" "+d.watch.name+" "+(d.watch.aliases||[]).join(" ")).toLowerCase().includes(st.q)));
}
function draw(){
  const v=visible();
  $("count").textContent=`${v.length} of ${DATA.length} hits`;
  $("list").innerHTML=v.slice(0,600).map(d=>`<button class="item" role="listitem" data-h="${d.hit}" aria-current="${d.hit===st.sel}">
    <div class="who">${E(d.screened.name||d.cust.name)}</div>
    <div class="vs">against ${E(d.watch.name)}</div>
    <div class="meta"><span class="dot d-${d.decision.decision}"></span>${E(SHORT[d.decision.decision])}, ${E(CODE[d.decision.reason_code]||d.decision.reason_code)}${LABELLED.has(d.hit)?'<span class="lab" title="One of the 50 practice labels">labelled</span>':""}</div></button>`).join("")
    +(v.length>600?`<div class="count" style="padding:12px 14px">Showing the first 600. Search to narrow down.</div>`:"")
    +(v.length?"":`<div class="count" style="padding:14px">No hits match. Clear the search or pick another decision.</div>`);
}
$("list").onclick=e=>{const b=e.target.closest(".item");if(b)open(b.dataset.h)};

function chip(id,used){return id?`<button class="chip${used&&used.has(id)?" used":""}" data-raw="${E(id)}">${E(id)}</button>`:""}
function chips(ids,used){return (ids||[]).map(i=>chip(i,used)).join("")}

function open(h){
  st.sel=h; draw();
  const d=DATA.find(x=>x.hit===h); if(!d) return;
  const used=new Set(d.decision.evidence||[]);
  const dec=d.decision.decision, decisive=new Set((d.decisive||"").split(" ").filter(Boolean));
  const C=d.cust, W=d.watch;
  const sides={
    name:[`<div class="v">${E(C.name)}</div><div class="src">${C.verified?"verified ":"from the record "}${chips(C.name_src,used)}</div>`,
          `<div class="v">${E(W.name)}</div>${W.aliases.length?`<div class="src" style="color:var(--mute);font-size:12.5px">also ${E(W.aliases.join("; "))}</div>`:""}`],
    dob:[`<div class="v">${E(C.dob||"not known")}</div><div class="src">${chips(C.dob_src,used)}</div>`,
         `<div class="v">${E(W.dob||"not given")}</div><div class="src" style="font-size:12.5px;color:var(--mute)">${W.dob?E("from "+W.dob_from):""}${W.alt_dob?E("; search results say "+W.alt_dob):""} ${chips(W.dob_src,used)}</div>`],
    countries:[`<div class="v">${C.countries.map(x=>E(x.c)).join(", ")||"none known"}</div><div class="src">${chips([...new Set(C.countries.flatMap(x=>x.src))].slice(0,5),used)}</div>`,
               `<div class="v">${E(W.countries.join(", ")||"none given")}</div>`],
    type:[`<div class="v">${E(C.type)}</div>`,`<div class="v">${E(W.type)}</div>`],
    status:[`<div class="v">screened ${E(d.screened.at)}</div>`,`<div class="v">${W.death?"died "+E(W.death):"no death recorded"}</div>`],
    report:[`<div class="v">born ${E(C.dob||"unknown")}</div>`,`<div class="v">${W.report?"first reported "+E(W.report):"no dated report"}</div>`],
  };
  const MK={"=":"m-eq","≠":"m-ne","?":"m-q"}, SAY={"=":"agrees","≠":"contradicts","?":"unknown"};
  const rows=d.rows.map(([f,m,n])=>`<div class="mrow${decisive.has(f)?" row-decisive":""}">
     <div class="spine"><div class="f">${FACT[f]}${decisive.has(f)?" decided it":""}</div><div class="m ${MK[m]}" role="img" aria-label="${SAY[m]}">${m}</div><div class="n">${E(n)}</div></div>
     <div class="cell left">${sides[f][0]}</div>
     <div class="cell right">${sides[f][1]}</div></div>`).join("");
  const p0=d.people[0]||{records:[],checks:[],notes:[]};
  const recs=d.people.map((p,i)=>`${d.people.length>1?`<p>Person ${i+1}</p>`:""}<table class="t"><tr><th>Record</th><th>Name</th><th>Born</th><th>Nationality</th><th>Lives in</th><th>Status</th></tr>
     ${p.records.map(r=>`<tr class="${used.has(r.id)||r.id===d.decision.customer_record_id?"used":""}"><td>${chip(r.id,used)}${r.reached?" reached":""}</td><td>${E(r.name)}</td><td>${E(r.dob)}</td><td>${E(r.nat)}</td><td>${E(r.res)}${r.city?", "+E(r.city):""}</td><td>${E(r.status)}</td></tr>`).join("")}</table>`).join("");
  const checks=d.people.flatMap(p=>p.checks.map(k=>({...k,ver:k.id===p.verified_id})));
  const chk=checks.length?`<table class="t"><tr><th>Check</th><th>Result</th><th>Date</th><th>Verified name</th><th>Born</th><th>Document</th></tr>
     ${checks.map(k=>{const ok=/approv|pass|clear|verif/i.test(k.result);return `<tr class="${ok?"":"off"} ${k.ver||used.has(k.id)?"used":""}"><td>${chip(k.id,used)}</td><td>${E(k.result)}${k.ver?", the latest approved":""}</td><td>${E(k.date)}</td><td>${E(k.name)}</td><td>${E(k.dob)}</td><td>${E(k.doc)} ${E(k.country)}</td></tr>`}).join("")}</table>`:`<p>No provider checks. Any approved check in a note below counts instead.</p>`;
  const notes=d.people.flatMap(p=>p.notes);
  const nts=notes.length?notes.map(n=>`<div class="note ${used.has(n.id)?"used":""}">${chip(n.id,used)} <span style="color:var(--mute)">${E(n.date)}, ${E(n.channel)}</span><br>${E(n.text)}</div>`).join(""):`<p>No notes for this customer.</p>`;
  const multi=d.people.length>1, merged=d.people.some(p=>p.records.length>1);
  $("page").innerHTML=`
   <div class="verdict"><div class="word c-${dec}">${E(WORD[dec])}</div>
     <div class="why">because ${E(CODE[d.decision.reason_code]||d.decision.reason_code)}.</div>
     <div class="ids">Hit ${chip(d.hit,used)} on alert ${chip(d.alert,used)}, customer record ${d.decision.customer_record_id?(d.decision.customer_record_id==="AMBIGUOUS"?"ambiguous":chip(d.decision.customer_record_id,used)):"not found"}${LABELLED.has(d.hit)?", one of the practice labels":""}</div></div>
   <div class="mirror" role="table" aria-label="Customer compared with the listed person">
     <div class="spinehead">compared</div>
     <div class="mhead left"><div class="role">The customer</div><div class="nm">${E(C.name||d.screened.name)}</div><div class="sub">screened as ${E(d.screened.name)}${d.screened.dob?", born "+E(d.screened.dob):""}${d.screened.country?", "+E(d.screened.country):""}</div></div>
     <div class="mhead right"><div class="role">The listed person</div><div class="nm">${E(W.name)}</div><div class="sub">${E(W.category)}${W.position?", "+E(W.position):""}</div></div>
     ${rows}
   </div>
   <section class="reason"><h2>Why</h2><p>${E(d.decision.reason)}</p><div class="ev">Records relied on: ${chips(d.decision.evidence,used)||"none"}</div></section>
   <section class="trail"><h2>How the engine got there</h2><ol class="steps">
     <li><h3>The alert</h3><p>Screened on ${E(d.screened.at)} as ${E(d.screened.name)}${d.screened.dob?", born "+E(d.screened.dob):""}${d.screened.country?", "+E(d.screened.country):""}${d.screened.city?", "+E(d.screened.city):""}.</p></li>
     <li><h3>Finding the customer</h3><p>${E(d.link?("Followed "+d.link+"."):"No link on the alert.")} ${multi?"More than one person fits, so each was decided.":""} ${merged?"Records sharing a phone and full date of birth, or a document number, are one person, so their facts are combined.":""}</p>${recs}</li>
     <li><h3>Who they are, verified</h3><p>The latest approved check sets the legal name and date of birth. Rejected and expired checks are struck through and ignored.</p>${chk}</li>
     <li><h3>What the notes add</h3>${nts}</li>
     <li><h3>The listed person</h3><p>${E(W.category)}${W.score!=null?", provider match score "+E(W.score):""}. Birth date taken ${W.dob?E("from "+W.dob_from):"from nowhere: none is given"}.</p>${d.notes?`<div class="pre">${E(d.notes)}</div>`:""}</li>
     <li><h3>The decision</h3><p>${E(WORD[dec])}: ${E(CODE[d.decision.reason_code]||"")}. A wrong clearance costs 60 times more than sending a hit to review, so the engine clears only on a contradiction it can show.</p></li>
   </ol></section>`;
  $("main").scrollTop=0;
}

function summary(){
  const P=SUM.practice, T=SUM.total, D=SUM.decisions;
  const maxc=Math.max(...SUM.codes.map(c=>c[2]));
  $("page").innerHTML=`
   <h1 class="title">${T} watchlist hits, each resolved with its reasons</h1>
   <p class="lede">Every hit gets the customer it belongs to, the facts compared side by side, and the records that decided it. Nothing is cleared without a contradiction the engine can point to.</p>
   <div class="sumgrid">
     <div class="tile"><div class="k">Practice labels</div><div class="big">${P?P.right+" of "+P.n:"n/a"}</div><p>${P?`Cost ${P.cost} against ${P.base} for sending every hit to review. Unsafe clearances: ${P.unsafe}.`:"No labels file found."}</p></div>
     <div class="tile"><div class="k">Cleared as a different person</div><div class="big c-DIFFERENT_ENTITY">${D.DIFFERENT_ENTITY||0}</div><p>${Math.round(100*(D.DIFFERENT_ENTITY||0)/T)}% of hits, each on a stated contradiction.</p></div>
     <div class="tile"><div class="k">Confirmed or sent to a person</div><div class="big"><span class="c-SAME_ENTITY">${D.SAME_ENTITY||0}</span> <span style="color:var(--line)">/</span> <span class="c-REVIEW">${D.REVIEW||0}</span></div><p>Same person, and hits where nothing settles it.</p></div>
   </div>
   <table class="t bars"><tr><th>Decision</th><th>Main reason</th><th>Hits</th><th></th></tr>
   ${SUM.codes.map(([d,c,n])=>`<tr><td><span class="dot d-${d}"></span>${E(SHORT[d])}</td><td><button class="chip" data-code="${c}" title="Show these hits">${E(CODE[c]||c)}</button></td><td>${n}</td><td class="bar"><div class="b d-${d}" style="width:${100*n/maxc}%"></div></td></tr>`).join("")}
   </table>
   <p class="kbd">Keys: <kbd>/</kbd> search, <kbd>j</kbd> and <kbd>k</kbd> move through the list, <kbd>Esc</kbd> closes a record.</p>`;
}

/* raw record drawer */
document.addEventListener("click",e=>{
  const r=e.target.closest("[data-raw]"); if(r){showRaw(r.dataset.raw);return}
  const c=e.target.closest("[data-code]"); if(c){st.code=c.dataset.code;$("code").value=st.code;st.dec="";syncSeg();draw();const v=visible();if(v[0])open(v[0].hit)}
});
function showRaw(id){
  const o=RAW[id];
  $("dtitle").textContent=id;
  $("dbody").innerHTML=o?Object.entries(o).map(([k,v])=>`<dt>${E(k)}</dt><dd>${E(typeof v==="object"&&v!==null?JSON.stringify(v,null,1):v)}</dd>`).join(""):`<dt>Not embedded</dt><dd>This record is not in the page. Look it up in the data folder.</dd>`;
  $("drawer").classList.add("open"); $("dclose").focus();
}
$("dclose").onclick=()=>$("drawer").classList.remove("open");
document.addEventListener("keydown",e=>{
  if(e.key==="Escape"){$("drawer").classList.remove("open");return}
  if(e.target.matches("input,select,textarea")) return;
  if(e.key==="/"){e.preventDefault();$("q").focus();return}
  if(e.key==="j"||e.key==="k"){
    const v=visible(); if(!v.length) return;
    let i=v.findIndex(x=>x.hit===st.sel); i=e.key==="j"?Math.min(v.length-1,i+1):Math.max(0,i-1);
    open(v[i].hit); const el=document.querySelector(`.item[data-h="${v[i].hit}"]`); if(el) el.scrollIntoView({block:"nearest"});
  }
});
draw(); summary();
</script></body></html>
"""


def main():
    if len(sys.argv) != 4:
        print("usage: build_viewer.py <data folder> <output.jsonl> <viewer.html>", file=sys.stderr)
        sys.exit(2)
    folder, out_path, html_path = sys.argv[1:]
    items, raw = build(folder, out_path)
    labels = os.path.join(folder, "practice_labels.jsonl")
    summ = summary(items, labels)
    page = (PAGE.replace("__DATA__", json.dumps(items, ensure_ascii=False).replace("</", "<\\/"))
                .replace("__RAW__", json.dumps(raw, ensure_ascii=False, default=str).replace("</", "<\\/"))
                .replace("__SUM__", json.dumps(summ, ensure_ascii=False).replace("</", "<\\/")))
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote %s (%d hits, %.1f MB)" % (html_path, len(items), os.path.getsize(html_path) / 1e6), file=sys.stderr)


if __name__ == "__main__":
    main()
