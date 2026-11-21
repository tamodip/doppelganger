#!/usr/bin/env python3
"""Score predictions against practice labels using the brief's cost matrix."""
import json, sys
COST = {("DIFFERENT_ENTITY", "DIFFERENT_ENTITY"): 0, ("DIFFERENT_ENTITY", "SAME_ENTITY"): 3, ("DIFFERENT_ENTITY", "REVIEW"): 3,
        ("SAME_ENTITY", "DIFFERENT_ENTITY"): 60, ("SAME_ENTITY", "SAME_ENTITY"): 0, ("SAME_ENTITY", "REVIEW"): 2,
        ("REVIEW", "DIFFERENT_ENTITY"): 60, ("REVIEW", "SAME_ENTITY"): 2, ("REVIEW", "REVIEW"): 0}
pred = {}
for l in open(sys.argv[1]):
    r = json.loads(l); pred[r["hit_id"]] = r
labels = [json.loads(l) for l in open(sys.argv[2]) if l.strip()]
cost = base = 0
wrong = []
for lab in labels:
    truth = lab["decision"]
    p = pred.get(lab["hit_id"])
    got = p["decision"] if p else "REVIEW"
    cost += COST[(truth, got)]
    base += COST[(truth, "REVIEW")]
    codes = lab.get("reason_codes") or lab.get("reason_code") or []
    if isinstance(codes, str):
        codes = [c.strip() for c in codes.replace(";", ",").split(",") if c.strip()]
    rids = lab.get("customer_record_ids") or lab.get("customer_record_id") or []
    if isinstance(rids, str):
        rids = [c.strip() for c in rids.replace(";", ",").split(",") if c.strip()]
    ok_code = p and p["reason_code"] in codes
    ok_rid = p and (p["customer_record_id"] in rids or (not rids and p["customer_record_id"] is None))
    if got != truth or not ok_code or not ok_rid:
        wrong.append((lab["hit_id"], truth, codes, got, p and p["reason_code"], rids, p and p["customer_record_id"]))
print("decision score: %.3f  (cost %d vs all-review %d)" % (max(0, 1 - cost / base) if base else 0, cost, base))
print("mistakes/mismatches: %d" % len(wrong))
for w in wrong:
    print("  ", w)
