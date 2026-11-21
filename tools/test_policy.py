#!/usr/bin/env python3
"""One test per POLICY.md rule, on tiny synthetic data rooms.

Run:  python3 tools/test_policy.py
Each test builds a fresh data folder (a few records), runs the real engine
end to end, and checks the decision and reason code. No dev data is used.
"""
import json, os, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "system"))
import engine  # noqa: E402

VARIANTS = {
    "description": "test",
    "groups": [["muhammad", "mohammed", "mohamed"], ["william", "bill", "liam"], ["babatunde", "tunde"],
               ["abdul", "abdel", "abd"]],
    "company_legal_forms": ["ltd", "limited", "gmbh", "inc", "llc", "sa", "plc"],
}


class Room:
    """A tiny data room. Defaults give a clean, linked individual customer."""

    def __init__(self):
        self.files = {k: [] for k in ("alerts", "hits", "customers", "accounts", "onboarding", "kyc_checks", "notes", "search_results")}
        self.n = 0

    def _id(self, p):
        self.n += 1
        return "%s-%04d" % (p, self.n)

    def customer(self, name, dob=None, nationality=None, residence=None, phone=None, entity="Individual", email=None, city=None):
        rid = self._id("CUS")
        email = email or (rid.lower() + "@example.test")
        self.files["customers"].append({"record_id": rid, "entity_type": entity, "full_name": name, "preferred_name": None,
                                        "login_name": None, "dob": dob, "nationality": nationality, "residence_country": residence,
                                        "city": city, "address": None, "email": email, "phone": phone or ("+44 7" + rid[-4:] + "00000")})
        acc = self._id("ACC")
        self.files["accounts"].append({"account_id": acc, "customer_id": rid, "login_handle": rid.lower(), "phone": None})
        return rid, acc, email

    def kyc(self, rid, acc, name, dob, country, result="approved", when="2024-01-01", doc=None):
        app = self._id("APP")
        self.files["onboarding"].append({"account_id": acc, "customer_id": rid, "kyc_applicant_id": app})
        kid = self._id("KYC")
        self.files["kyc_checks"].append({"check_id": kid, "applicant_id": app, "provider": "Onfido", "verified_name": name,
                                         "verified_dob": dob, "document_type": "passport", "document_number": doc or ("P" + kid[-4:] + "999"),
                                         "document_country": country, "result": result, "checked_at": when})
        return kid

    def note(self, email, text, channel="kyc", when="2024-02-01"):
        nid = self._id("NOTE")
        self.files["notes"].append({"note_id": nid, "requester_email": email, "created_at": when, "channel": channel, "text": text})
        return nid

    def hit(self, name, entity="Individual", dobs=(), countries=(), aliases=(), notes=None, category="Sanctions",
            deceased=None, life=None):
        hid = self._id("H")
        self.files["hits"].append({"hit_id": hid, "entity_type": entity, "name": name, "aliases": list(aliases), "dobs": list(dobs),
                                   "countries": list(countries), "nationality": [], "citizenship": [], "addresses": [],
                                   "place_of_birth": None, "categories": [category], "primary_category": category,
                                   "profile_notes": notes, "deceased_date": deceased, "life_status": life})
        return hid

    def search(self, query, snippet):
        sid = self._id("SR")
        self.files["search_results"].append({"result_id": sid, "query": query, "title": "x", "snippet": snippet, "source": "t"})
        return sid

    def alert(self, hit_ids, name, account=None, entity="Individual", dob=None, country=None, screened="2026-11-01"):
        aid = self._id("A")
        self.files["alerts"].append({"alert_id": aid, "screened_at": screened,
                                     "customer": {"name": name, "entity_type": entity, "dob": dob, "country": country, "city": None},
                                     "account_ref": account, "login_handle": None, "email": None, "hit_ids": list(hit_ids)})
        return aid

    def run(self):
        d = tempfile.mkdtemp()
        for k, rows in self.files.items():
            with open(os.path.join(d, k + ".jsonl"), "w") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
        with open(os.path.join(d, "name_variants.json"), "w") as f:
            json.dump(VARIANTS, f)
        out = os.path.join(d, "out.jsonl")
        rows = engine.run(d, out)
        return {r["hit_id"]: r for r in rows}


def person(room, name="Daniel Okoro", dob="1980-05-10", country="Nigeria", **kw):
    rid, acc, email = room.customer(name, dob=dob, nationality=country, residence=country, **kw)
    return rid, acc, email


class Policy(unittest.TestCase):
    def check(self, r, decision, code=None):
        self.assertEqual(r["decision"], decision, r["reason"])
        if code:
            self.assertEqual(r["reason_code"], code, r["reason"])
        self.assertTrue(r["reason"])

    # ---------------------------------------------------------------- confirm
    def test_confirm_name_dob_location(self):
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("OKORO, Daniel", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY", "NAME_DOB_LOCATION_MATCH")

    def test_name_dob_agree_but_country_differs_is_review(self):           # P6 location note
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", dobs=["1980-05"], countries=["Ghana"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "REVIEW")

    def test_company_name_and_country(self):                                 # P7 companies
        R = Room(); rid, acc, _ = R.customer("Acme Widgets Ltd", nationality="Germany", residence="Germany", entity="Company")
        h = R.hit("ACME WIDGETS GMBH", entity="Company", countries=["Germany"]); R.alert([h], "Acme Widgets Ltd", acc, "Company")
        self.check(R.run()[h], "SAME_ENTITY", "NAME_LOCATION_MATCH")

    # ------------------------------------------------------------ contradictions
    def test_dob_more_than_one_year_apart(self):
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", dobs=["1975"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "DOB_MISMATCH")

    def test_one_year_apart_is_not_a_contradiction(self):
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", dobs=["1981"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "REVIEW")

    def test_same_year_different_month(self):
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", dobs=["1980-09-02"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "DOB_MISMATCH")

    def test_one_agreeing_dob_prevents_clearing(self):
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", dobs=["1960", "1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_too_young_at_earliest_dated_report(self):                      # P6 time
        R = Room(); rid, acc, _ = person(R, dob="2008-03-01")
        notes = "Summary.\n\nAccording to Daily News; March 3, 2015: Daniel Okoro was charged."
        h = R.hit("Daniel Okoro", countries=["Nigeria"], notes=notes); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY")

    def test_company_incorporated_after_report(self):                        # rare
        R = Room(); rid, acc, _ = R.customer("Bluewave Trading Ltd", dob="2021-06-01", nationality="United Kingdom",
                                               residence="United Kingdom", entity="Company")
        notes = "According to FT; May 2, 2018: Bluewave Trading was fined."
        h = R.hit("Bluewave Trading Limited", entity="Company", countries=["United Kingdom"], notes=notes)
        R.alert([h], "Bluewave Trading Ltd", acc, "Company")
        self.check(R.run()[h], "DIFFERENT_ENTITY", "TEMPORAL_IMPOSSIBILITY")

    def test_deceased_before_screening(self):                                # rare
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", countries=["Nigeria"], deceased="2019-04-02", life="Deceased"); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "DECEASED")

    def test_person_versus_company(self):                                    # rare
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro Ltd", entity="Company", countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "ENTITY_TYPE_MISMATCH")

    def test_location_mismatch(self):
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", countries=["Canada"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "LOCATION_MISMATCH")

    def test_country_codes_and_nationalities_are_countries(self):
        R = Room(); rid, acc, _ = R.customer("Daniel Okoro", dob="1980-05-10", nationality="Nigerian", residence="NG")
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY", "NAME_DOB_LOCATION_MATCH")

    def test_note_says_customer_lived_in_hit_country(self):
        R = Room(); rid, acc, email = person(R, country="United Kingdom")
        R.note(email, "Customer mentioned they lived in Canada from 2005 to 2010.", channel="email")
        h = R.hit("Daniel Okoro", countries=["Canada"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "REVIEW")

    # ------------------------------------------------------------- names (P8)
    def test_verified_name_mismatch(self):
        R = Room(); rid, acc, _ = person(R)
        R.kyc(rid, acc, "Grace Okoro", "1980-05-10", "Nigeria")
        h = R.hit("Okoro, Daniel", countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED")

    def test_names_written_together_or_apart(self):
        R = Room(); rid, acc, _ = person(R, name="Abdulsalam Bello")
        h = R.hit("Abdul Salam Bello", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Abdulsalam Bello", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_name_variants_and_nicknames(self):
        R = Room(); rid, acc, _ = person(R, name="Bill Adeyemi")
        h = R.hit("William Adeyemi", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Bill Adeyemi", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_single_part_alias_can_agree(self):                              # rare
        R = Room(); rid, acc, _ = person(R, name="Babatunde Fashola")
        R.kyc(rid, acc, "Babatunde Fashola", "1980-05-10", "Nigeria")
        h = R.hit("Femi Adebayo", aliases=["Tunde"], countries=["Nigeria"]); R.alert([h], "Babatunde Fashola", acc)
        # the single-part alias agrees with the verified name, so no NAME_MISMATCH clearance
        self.assertNotEqual(R.run()[h]["reason_code"], "NAME_MISMATCH_VERIFIED")

    def test_placeholder_name_parts_cannot_clear_on_name(self):              # rare
        R = Room(); rid, acc, _ = person(R, name="N/A Okoro")
        R.kyc(rid, acc, "N/A OKORO", "1980-05-10", "Nigeria")
        h = R.hit("Okoro, Daniel", countries=["Nigeria"]); R.alert([h], "N/A Okoro", acc)
        self.assertNotEqual(R.run()[h]["reason_code"], "NAME_MISMATCH_VERIFIED")

    def test_placeholder_name_cannot_confirm(self):
        R = Room(); rid, acc, _ = person(R, name="XXX Okoro")
        h = R.hit("Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "XXX Okoro", acc)
        self.check(R.run()[h], "REVIEW")

    def test_short_parts_must_be_identical(self):
        R = Room(); rid, acc, _ = person(R, name="Li Wei Chen")
        R.kyc(rid, acc, "Li Wei Chen", "1980-05-10", "China")
        h = R.hit("Lu Wen Chan", countries=["China"]); R.alert([h], "Li Wei Chen", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "NAME_MISMATCH_VERIFIED")

    def test_company_legal_forms_ignored(self):
        R = Room(); rid, acc, _ = R.customer("Nordic Fish AS Limited", nationality="Norway", residence="Norway", entity="Company")
        h = R.hit("Nordic Fish Ltd", entity="Company", countries=["Norway"]); R.alert([h], "Nordic Fish Limited", acc, "Company")
        self.check(R.run()[h], "SAME_ENTITY", "NAME_LOCATION_MATCH")

    # ---------------------------------------------------- customer picture (P2-4)
    def test_verified_beats_declared(self):
        R = Room(); rid, acc, _ = person(R, dob="1953-01-03")
        R.kyc(rid, acc, "Daniel Okoro", "1980-05-10", "Nigeria", when="2025-01-01")
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_note_only_verification_counts(self):
        R = Room(); rid, acc, email = person(R, dob="1953-01-03")
        R.note(email, "On 2025-03-01, Persona processed the Nigeria passport for DANIEL OKORO, born on the 10th of May 1980.")
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_rejected_check_is_not_evidence(self):
        R = Room(); rid, acc, _ = person(R)
        R.kyc(rid, acc, "Grace Okoro", "1999-01-01", "Nigeria", result="rejected", when="2025-01-01")
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_rejected_note_is_not_evidence(self):
        R = Room(); rid, acc, email = person(R)
        R.note(email, "Veriff rejected the passport submitted for GRACE OKORO, DOB 1999-01-01; the document was expired.")
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_unreadable_verified_dob_never_falls_back_to_declared(self):
        R = Room(); rid, acc, email = person(R, dob="1950-01-01")
        R.note(email, "Onfido verified the passport for DANIEL OKORO from Nigeria on 2025-01-01.")
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.assertNotEqual(R.run()[h]["decision"], "DIFFERENT_ENTITY")

    def test_same_phone_and_dob_is_one_person(self):                        # P3
        R = Room()
        rid1, acc1, _ = R.customer("Daniel Okoro", dob="1980-05-10", nationality="United Kingdom", residence="United Kingdom", phone="+44 7000 111222")
        rid2, acc2, _ = R.customer("Daniel Okoro", dob="10/05/1980", nationality="Ghana", residence="Ghana", phone="+44 7000 111222")
        h = R.hit("Daniel Okoro", countries=["Ghana"]); R.alert([h], "Daniel Okoro", acc1)
        # the old record's Ghana link counts, so location cannot clear
        self.check(R.run()[h], "REVIEW")

    def test_shared_phone_alone_is_not_one_person(self):
        R = Room()
        rid1, acc1, _ = R.customer("Daniel Okoro", dob="1980-05-10", nationality="United Kingdom", residence="United Kingdom", phone="+44 7000 111222")
        rid2, acc2, _ = R.customer("Grace Okoro", dob="1955-02-01", nationality="Ghana", residence="Ghana", phone="+44 7000 111222")
        h = R.hit("Daniel Okoro", countries=["Ghana"]); R.alert([h], "Daniel Okoro", acc1)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "LOCATION_MISMATCH")

    def test_ambiguous_customer_with_different_decisions(self):             # rare
        R = Room()
        R.customer("Juan Gonzalez", dob="1980-05-10", nationality="Panama", residence="Panama")
        R.customer("Juan Gonzalez", dob="1950-01-01", nationality="Panama", residence="Panama")
        h = R.hit("Juan Gonzalez", dobs=["1980"], countries=["Panama"]); R.alert([h], "Juan Gonzalez", None)
        r = R.run()[h]
        self.check(r, "REVIEW", "AMBIGUOUS_CUSTOMER_RECORD")
        self.assertEqual(r["customer_record_id"], "AMBIGUOUS")

    def test_ambiguous_customer_with_same_decision(self):
        R = Room()
        R.customer("Juan Gonzalez", dob="1980-05-10", nationality="Panama", residence="Panama")
        R.customer("Juan Gonzalez", dob="1981-01-01", nationality="Panama", residence="Panama")
        h = R.hit("Juan Gonzalez", countries=["Brazil"]); R.alert([h], "Juan Gonzalez", None)
        r = R.run()[h]
        self.check(r, "DIFFERENT_ENTITY", "LOCATION_MISMATCH")
        self.assertEqual(r["customer_record_id"], "AMBIGUOUS")

    # ------------------------------------------------------- hit picture (P5)
    def test_age_at_date_gives_two_birth_years(self):
        R = Room(); rid, acc, _ = person(R, dob="1973-08-01")
        h = R.hit("Daniel Okoro", countries=["Nigeria"], notes="Daniel Okoro was aged 45 when sentenced in March 2019.")
        R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_age_counts_are_not_ages(self):
        R = Room(); rid, acc, _ = person(R, dob="1990-01-01")
        h = R.hit("Daniel Okoro", countries=["Nigeria"], notes="In March 2019 Daniel Okoro was 37 million naira in debt.")
        R.alert([h], "Daniel Okoro", acc)
        self.assertNotEqual(R.run()[h]["decision"], "DIFFERENT_ENTITY")

    def test_search_result_must_match_listing_and_country(self):
        R = Room(); rid, acc, _ = person(R, dob="1980-05-10")
        h = R.hit("Okoro, Daniel", countries=["Nigeria"], category="Sanctions")
        R.search("daniel okoro", "In June 2010, Daniel Okoro, 52, a chef in Nigeria, opened a restaurant.")       # wrong listing
        R.search("daniel okoro", "In June 2010, Daniel Okoro, 52, was named as a sanctioned individual in Kenya.")  # wrong country
        R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "REVIEW")

    def test_search_result_gives_dob_when_valid(self):
        R = Room(); rid, acc, _ = person(R, dob="1980-05-10")
        h = R.hit("Okoro, Daniel", countries=["Nigeria"], category="Sanctions")
        sid = R.search("daniel okoro", "In June 2010, Daniel Okoro, 52, was named as a sanctioned individual in Nigeria.")
        R.alert([h], "Daniel Okoro", acc)
        r = R.run()[h]
        self.check(r, "DIFFERENT_ENTITY", "DOB_MISMATCH")
        self.assertIn(sid, r["evidence"])

    def test_month_first_provider_date(self):
        R = Room(); rid, acc, _ = person(R, dob="1955-06-20")
        h = R.hit("Daniel Okoro", countries=["Nigeria"], notes="Name: Okoro, Daniel\nDOB: 6/20/1955\nSex: M")
        R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    # ------------------------------------------------------------- safety
    def test_unknown_stays_unknown(self):
        R = Room(); rid, acc, _ = R.customer("Daniel Okoro")
        h = R.hit("Daniel Okoro"); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "REVIEW", "INSUFFICIENT_DATA")

    def test_output_is_deterministic(self):
        R = Room(); rid, acc, _ = person(R)
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.assertEqual(R.run(), R.run())

    # ------------------------------------------------- hardening (cross-check with a second solution)
    def _name_room(self, verified, hit_name):
        R = Room(); rid, acc, _ = person(R, name=hit_name, dob="1980-05-10")
        R.kyc(rid, acc, verified, "1980-05-10", "Nigeria")
        h = R.hit(hit_name, dobs=["1990"], countries=["Nigeria"]); R.alert([h], hit_name, acc)
        return R.run()[h]

    def test_special_letters_fold_before_comparing_names(self):
        for v, hname in [("Işık Yılmaz", "Isik Yilmaz"), ("Johann Strauß", "Johann Strauss"),
                         ("Þór Guðmundsson", "Thor Gudmundsson"), ("Łukasz Wałęsa", "Lukasz Walesa")]:
            r = self._name_room(v, hname)
            self.assertNotEqual(r["reason_code"], "NAME_MISMATCH_VERIFIED", v)

    def test_lookalike_or_foreign_script_never_clears_on_name(self):
        for v in ["Аnna Smith", "Иван Петров"]:          # Cyrillic A / full Cyrillic
            r = self._name_room(v, "Anna Smith")
            self.assertNotEqual(r["reason_code"], "NAME_MISMATCH_VERIFIED", v)

    def test_circa_and_decade_hit_dobs_never_contradict(self):
        for v in ["circa 1980", "c. 1980", "1980s"]:
            R = Room(); rid, acc, _ = person(R, dob="1984-01-01")
            h = R.hit("Daniel Okoro", dobs=[v], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
            self.assertNotEqual(R.run()[h]["decision"], "DIFFERENT_ENTITY", v)

    def test_year_range_contradicts_when_every_year_is_far(self):
        R = Room(); rid, acc, _ = person(R, dob="1984-01-01")
        h = R.hit("Daniel Okoro", dobs=["1979-1981"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "DIFFERENT_ENTITY", "DOB_MISMATCH")

    def test_bare_congo_never_clears_on_location(self):
        R = Room(); rid, acc, _ = person(R, country="Democratic Republic of the Congo")
        h = R.hit("Daniel Okoro", countries=["Congo"]); R.alert([h], "Daniel Okoro", acc)
        self.assertNotEqual(R.run()[h]["decision"], "DIFFERENT_ENTITY")

    def test_two_digit_year_in_note_is_unreadable(self):
        R = Room(); rid, acc, email = person(R, dob="1960-01-01")
        R.note(email, "Passport of DANIEL OKORO verified by Sumsub on 2024-06-01, DOB 12/04/85.")
        h = R.hit("Daniel Okoro", dobs=["1985"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.assertNotEqual(R.run()[h]["decision"], "DIFFERENT_ENTITY")

    def test_phone_security_check_is_not_an_identity_check(self):
        R = Room(); rid, acc, email = person(R, dob="1980-05-10")
        R.kyc(rid, acc, "DANIEL OKORO", "1980-05-10", "Nigeria", when="2022-01-01")
        R.note(email, "Customer called about a card payment; identity verified on the phone by security questions.",
               channel="phone", when="2025-02-02")
        h = R.hit("Daniel Okoro", dobs=["1980-05-10"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "SAME_ENTITY")

    def test_hit_sources_that_disagree_go_to_review(self):
        R = Room(); rid, acc, _ = person(R, dob="1955-11-19", country="United States")
        h = R.hit("Daniel Okoro", countries=["United States"], category="Enforcement", notes="Name: Okoro, Daniel\nDOB: 6/20/1955")
        R.search("Daniel Okoro",
                 "Throughout March 2011, United States news media featured Daniel Okoro, 55, who was the subject of enforcement action.")
        R.alert([h], "Daniel Okoro", acc)
        self.check(R.run()[h], "REVIEW")

    def test_placeholder_phone_decides_both_ways(self):
        R = Room()
        rid1, acc1, _ = R.customer("Daniel Okoro", dob="1980-05-10", nationality="United Kingdom", residence="United Kingdom", phone="0000000000")
        R.customer("Daniel Okoro", dob="1980-05-10", nationality="Kenya", residence="Kenya", phone="0000000000")
        h = R.hit("Daniel Okoro", countries=["Kenya"]); R.alert([h], "Daniel Okoro", acc1)
        self.check(R.run()[h], "REVIEW")

    def test_linked_record_is_the_one_reported(self):
        R = Room()
        old, _, _ = R.customer("Daniel Okoro", dob="1980-05-10", nationality="Nigeria", residence="Nigeria", phone="+234 803 555 1234")
        new, acc, _ = R.customer("Daniel Okoro", dob="1980-05-10", nationality="Nigeria", residence="Nigeria", phone="+234 803 555 1234")
        h = R.hit("Daniel Okoro", dobs=["1980"], countries=["Nigeria"]); R.alert([h], "Daniel Okoro", acc)
        self.assertEqual(R.run()[h]["customer_record_id"], new)


if __name__ == "__main__":
    unittest.main(verbosity=2)
