#!/usr/bin/env python3
"""Audit-trail viewer: one self-contained HTML page for the demo.

    python3 tools/build_viewer.py <data folder> <output.jsonl> <viewer.html>

Click a hit to see the path an analyst would follow:
alert -> link -> customer records -> ID check / notes -> watchlist hit -> decision,
with every record that decided it highlighted.
The page embeds data from the data folder: keep it local (it is git-ignored),
because the dev data is for event participants only.
"""
import html, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "system"))
import engine  # noqa: E402


def s(v):
    return engine.sval(v)


def build(folder, out_path):
    data = engine.Data(folder)
    decisions = {}
    for l in open(out_path, encoding="utf-8"):
        r = json.loads(l)
        decisions[r["hit_id"]] = r
    items = []
    for alert in sorted(data.alerts, key=lambda a: s(a.get("alert_id"))):
        fa = engine.flatten_alert(alert)
        persons, how = data.resolve(fa)
        linked = []
        if fa.get("account_ref"):
            linked = sorted({s(a.get("customer_id")) for a in data.acc_by_id.get(s(fa.get("account_ref")), [])})
        custs = [engine.Customer(data, p, fa) for p in persons]
        cust_view = []
        for c in custs:
            checks = []
            for r in c.rids:
                for o in data.onb_by_cust.get(r, []):
                    for k in data.kyc_by_app.get(s(o.get("kyc_applicant_id")), []):
                        checks.append({"id": k.get("check_id"), "name": s(k.get("verified_name")), "dob": s(k.get("verified_dob")),
                                       "country": s(k.get("document_country")), "result": s(k.get("result")),
                                       "date": s(k.get("checked_at")), "via": "%s → %s → %s" % (r, s(o.get("account_id")), s(o.get("kyc_applicant_id")))})
            cust_view.append({
                "records": [{"id": x["record_id"], "name": s(x.get("full_name")), "dob": s(x.get("dob")),
                             "nat": s(x.get("nationality")), "res": s(x.get("residence_country")), "email": s(x.get("email")),
                             "phone": s(x.get("phone"))} for x in c.records],
                "grouped": len(c.rids) > 1,
                "checks": sorted(checks, key=lambda k: k["date"]),
                "notes": [{"id": n.get("note_id"), "date": s(n.get("created_at")), "channel": s(n.get("channel")),
                           "text": s(n.get("text"))[:400]} for n in c.notes],
                "verified": c.verified and {"id": c.verified["id"], "name": c.verified.get("name"),
                                            "dob": str(c.verified.get("dob") or ""), "date": str(c.verified.get("date") or "")},
                "countries": {k: sorted(v) for k, v in sorted(c.countries.items())},
            })
        for hid in engine.as_list(alert.get("hit_ids")):
            h = data.hits.get(hid)
            if not h or hid not in decisions:
                continue
            hit = engine.Hit(data, h)
            items.append({
                "hit": hid, "alert": alert.get("alert_id"), "screened": {"name": s(fa.get("customer_name")), "dob": s(fa.get("dob")),
                    "country": s(fa.get("country")), "city": s(fa.get("city")), "type": s(fa.get("entity_type")), "at": s(fa.get("screened_at"))},
                "link": how, "linked": linked, "persons": [p for p in persons], "customers": cust_view,
                "watch": {"names": hit.names, "type": s(h.get("entity_type")), "category": hit.category,
                          "dobs": [str(x) for x in hit.dobs], "dob_source": hit.dob_source or "none", "countries": sorted(hit.countries),
                          "report": str(hit.earliest_report or ""), "death": str(hit.death or ""), "search": hit.search_used,
                          "notes": (s(h.get("profile_notes")) or "")[:600]},
                "decision": decisions[hid],
            })
    return items


PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Doppelganger: audit trail</title>
<style>
:root{--ink:#14161a;--mute:#6b7280;--line:#e5e7eb;--bg:#fafaf7;--card:#fff;--diff:#0f766e;--same:#b42318;--rev:#b45309;--hl:#fff4c2}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--ink);background:var(--bg)}
header{display:flex;align-items:baseline;gap:16px;padding:16px 24px;border-bottom:1px solid var(--line);background:var(--card)}
header h1{font-size:18px;margin:0;letter-spacing:-.01em}header .sub{color:var(--mute)}
.stats{margin-left:auto;display:flex;gap:14px;font-size:13px}.stats b{font-variant-numeric:tabular-nums}
main{display:grid;grid-template-columns:340px 1fr;height:calc(100vh - 58px)}
aside{border-right:1px solid var(--line);overflow:auto;background:var(--card)}
.filters{position:sticky;top:0;background:var(--card);padding:12px;border-bottom:1px solid var(--line);display:flex;flex-direction:column;gap:8px}
.filters input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:8px;font:inherit}
.chips{display:flex;gap:6px;flex-wrap:wrap}.chip{border:1px solid var(--line);background:#fff;border-radius:999px;padding:3px 10px;font-size:12px;cursor:pointer}
.chip.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.row{padding:10px 12px;border-bottom:1px solid var(--line);cursor:pointer}.row:hover{background:#f6f6f2}.row.sel{background:#eef2ff}
.row .n{font-weight:600}.row .m{color:var(--mute);font-size:12px}
.tag{display:inline-block;font-size:11px;font-weight:700;letter-spacing:.03em;padding:1px 7px;border-radius:5px;color:#fff}
.DIFFERENT_ENTITY{background:var(--diff)}.SAME_ENTITY{background:var(--same)}.REVIEW{background:var(--rev)}
section{overflow:auto;padding:24px 32px}
.verdict{display:flex;gap:14px;align-items:flex-start;padding:18px;border-radius:12px;background:var(--card);border:1px solid var(--line);margin-bottom:22px}
.verdict .tag{font-size:13px;padding:4px 10px}.verdict p{margin:6px 0 0}
.trail{position:relative;margin-left:10px}.step{position:relative;padding:0 0 18px 26px;border-left:2px solid var(--line)}
.step:last-child{border-left-color:transparent}.step:before{content:"";position:absolute;left:-7px;top:3px;width:12px;height:12px;border-radius:50%;background:var(--card);border:2px solid var(--ink)}
.step h3{margin:0 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--mute)}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px;margin:6px 0}
.box.ev{background:var(--hl);border-color:#f2d36b}.box .id{font:12px ui-monospace,SFMono-Regular,Menlo,monospace;font-weight:700}
.kv{display:grid;grid-template-columns:120px 1fr;gap:2px 10px;font-size:13px}.kv span:nth-child(odd){color:var(--mute)}
.muted{color:var(--mute)}.rej{opacity:.55}.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
.legend{font-size:12px;color:var(--mute);margin-top:4px}.legend i{background:var(--hl);border:1px solid #f2d36b;padding:0 6px;border-radius:4px;font-style:normal}
</style></head><body>
<header><h1>Doppelganger</h1><span class="sub">Watchlist hit → who is the customer, who is the listed person, same or not?</span>
<div class="stats" id="stats"></div></header>
<main><aside><div class="filters"><input id="q" placeholder="Search name, hit or alert ID…">
<div class="chips" id="chips"></div></div><div id="list"></div></aside>
<section id="detail"><p class="muted">Select a hit on the left.</p></section></main>
<script>
const DATA = __DATA__;
const E = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let filt = "ALL", sel = null;
const counts = DATA.reduce((a,x)=>(a[x.decision.decision]=(a[x.decision.decision]||0)+1,a),{});
document.getElementById("stats").innerHTML = ["DIFFERENT_ENTITY","SAME_ENTITY","REVIEW"].map(k=>`<span><span class="tag ${k}">${k.replace("_ENTITY","")}</span> <b>${counts[k]||0}</b></span>`).join("");
const chips = document.getElementById("chips");
["ALL","DIFFERENT_ENTITY","SAME_ENTITY","REVIEW"].forEach(k=>{const b=document.createElement("button");b.className="chip"+(k==="ALL"?" on":"");b.textContent=k==="ALL"?"All":k.replace("_ENTITY","").replace("_"," ");b.onclick=()=>{filt=k;[...chips.children].forEach(c=>c.classList.remove("on"));b.classList.add("on");draw()};chips.appendChild(b)});
document.getElementById("q").oninput = draw;
function draw(){
  const q=document.getElementById("q").value.toLowerCase();
  const rows=DATA.filter(x=>(filt==="ALL"||x.decision.decision===filt)&&(!q||(x.hit+" "+x.alert+" "+x.screened.name+" "+x.watch.names.join(" ")).toLowerCase().includes(q)));
  document.getElementById("list").innerHTML=rows.slice(0,400).map(x=>`<div class="row ${sel===x.hit?"sel":""}" data-h="${x.hit}">
   <div><span class="tag ${x.decision.decision}">${x.decision.decision.replace("_ENTITY","")}</span> <span class="n">${E(x.screened.name)}</span></div>
   <div class="m">vs ${E(x.watch.names[0])} · ${x.decision.reason_code}</div></div>`).join("")+(rows.length>400?`<div class="row muted">${rows.length-400} more: refine the search</div>`:"");
  document.querySelectorAll(".row[data-h]").forEach(r=>r.onclick=()=>{sel=r.dataset.h;show(DATA.find(x=>x.hit===sel));draw()});
}
function box(id, inner, ev, extra=""){return `<div class="box ${ev.has(id)?"ev":""} ${extra}"><div class="id">${E(id)}${ev.has(id)?" · decisive evidence":""}</div>${inner}</div>`}
function kv(o){return `<div class="kv">${Object.entries(o).filter(([k,v])=>v).map(([k,v])=>`<span>${E(k)}</span><span>${E(v)}</span>`).join("")}</div>`}
function show(x){
  const d=x.decision, ev=new Set(d.evidence);
  let h=`<div class="verdict"><span class="tag ${d.decision}">${d.decision}</span><div><b>${d.reason_code}</b> · customer <span class="mono">${E(d.customer_record_id)}</span><p>${E(d.reason)}</p>
   <div class="legend">Highlighted <i>records</i> are the evidence cited for this decision.</div></div></div><div class="trail">`;
  h+=`<div class="step"><h3>1 · Alert ${E(x.alert)} · screened ${E(x.screened.at)}</h3><div class="box">${kv({"Screened name":x.screened.name,"Type":x.screened.type,"Date of birth":x.screened.dob,"Country":x.screened.country,"City":x.screened.city})}</div></div>`;
  h+=`<div class="step"><h3>2 · Find the customer</h3><div class="box">${kv({"Followed":x.link,"Account holders":x.linked.join(", "),"People who fit":x.persons.map(p=>p.join(" + ")).join("  |  ")||"none"})}
   ${x.persons.length>1?'<div class="muted">More than one person fits: the hit is decided for each.</div>':""}</div></div>`;
  x.customers.forEach((c,i)=>{
    h+=`<div class="step"><h3>3 · Customer picture${x.customers.length>1?" (person "+(i+1)+")":""}${c.grouped?" · records merged (same phone + DOB, or same document)":""}</h3>`;
    c.records.forEach(r=>h+=box(r.id,kv({"Name on record":r.name,"Declared DOB":r.dob,"Nationality":r.nat,"Residence":r.res,"Email":r.email,"Phone":r.phone}),ev));
    h+=`</div><div class="step"><h3>4 · Identity checks (verified beats declared)</h3>`;
    if(!c.checks.length) h+=`<div class="muted">No KYC provider checks.</div>`;
    c.checks.forEach(k=>h+=box(k.id,kv({"Verified name":k.name,"Verified DOB":k.dob,"Document country":k.country,"Result":k.result+(k.result!=="approved"?" (not evidence)":""),"Checked":k.date,"Linked via":k.via}),ev,k.result!=="approved"?"rej":""));
    h+=`<div class="box">${kv({"Most recent approved check":c.verified?c.verified.id+" ("+c.verified.date+")":"none","Legal name":c.verified&&c.verified.name,"Verified DOB":c.verified&&c.verified.dob})}</div></div>`;
    h+=`<div class="step"><h3>5 · Notes (by the customer's email)</h3>`;
    if(!c.notes.length) h+=`<div class="muted">No notes.</div>`;
    c.notes.forEach(n=>h+=box(n.id,`<div class="muted">${E(n.date)} · ${E(n.channel)}</div>${E(n.text)}`,ev));
    h+=`<div class="box">${kv(Object.fromEntries(Object.entries(c.countries).map(([k,v])=>["Linked to "+k,v.join(", ")])))}</div></div>`;
  });
  const w=x.watch;
  h+=`<div class="step"><h3>6 · The listed person (${E(w.category||"watchlist")})</h3><div class="box">${kv({"Names":w.names.join(" · "),"Type":w.type,"Birth year(s)":w.dobs.join(" | ")+(w.dobs.length?"  (from "+w.dob_source+")":"none found"),"Countries":w.countries.join(", "),"Earliest dated report":w.report,"Date of death":w.death})}</div>
   ${w.search.map(sid=>box(sid,'<div class="muted">Web result that matches the listing type and a country tied to the hit</div>',ev)).join("")}
   ${w.notes?`<div class="box muted" style="white-space:pre-wrap">${E(w.notes)}</div>`:""}</div>`;
  h+=`<div class="step"><h3>7 · Decision</h3><div class="box"><span class="tag ${d.decision}">${d.decision}</span> ${d.reason_code} · cites ${d.evidence.map(e=>`<span class="mono">${E(e)}</span>`).join(", ")||"nothing"}</div></div></div>`;
  document.getElementById("detail").innerHTML=h;document.getElementById("detail").scrollTop=0;
}
draw();
const first = DATA.find(x=>x.decision.decision==="SAME_ENTITY")||DATA[0]; if(first){sel=first.hit;show(first);draw();}
</script></body></html>"""


def main():
    folder, out_path, html_path = sys.argv[1], sys.argv[2], sys.argv[3]
    items = build(folder, out_path)
    blob = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(PAGE.replace("__DATA__", blob))
    print("wrote %s (%d hits, %.1f MB)" % (html_path, len(items), os.path.getsize(html_path) / 1e6))


if __name__ == "__main__":
    main()
