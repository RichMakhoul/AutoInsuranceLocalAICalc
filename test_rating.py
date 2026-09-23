"""
Unit tests for the rating engine.

These assert the PROPERTIES that make the plan defensible, not just that the
code runs: premium neutrality, monotonicity in density, correct direction of
each relativity, and graceful handling of unknown input.

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))

import rating


class TestInterpolation(unittest.TestCase):
    curve = [(0, 1.0), (10, 2.0), (20, 4.0)]

    def test_exact_breakpoints(self):
        for x, y in self.curve:
            self.assertAlmostEqual(rating._interp(self.curve, x), y)

    def test_midpoint(self):
        self.assertAlmostEqual(rating._interp(self.curve, 5), 1.5)
        self.assertAlmostEqual(rating._interp(self.curve, 15), 3.0)

    def test_flat_outside_range(self):
        self.assertAlmostEqual(rating._interp(self.curve, -99), 1.0)
        self.assertAlmostEqual(rating._interp(self.curve, 999), 4.0)


class TestTerritoryRelativities(unittest.TestCase):
    """The territory plan must not change the statewide average premium."""

    def test_population_weighted_mean_is_one(self):
        ref = rating.load_reference_data()
        for st in ["NJ", "NY", "CA", "TX", "MT", "WY", "RI", "AK"]:
            rel = rating.territory_relativities(st)
            cs = ref["by_state"][st]
            tot = sum(c["population"] for c in cs)
            wm = sum(rel[c["fips"]] * c["population"] for c in cs) / tot
            # Exactly 1.0 before clamping; clamping in states with extreme rural
            # counties introduces a small, bounded drift.
            self.assertAlmostEqual(wm, 1.0, delta=0.03, msg=f"{st} drifted to {wm:.4f}")

    def test_denser_county_is_never_cheaper(self):
        ref = rating.load_reference_data()
        for st in ["NJ", "PA", "OH"]:
            rel = rating.territory_relativities(st)
            cs = sorted(ref["by_state"][st], key=lambda c: c["density"])
            vals = [rel[c["fips"]] for c in cs]
            for a, b in zip(vals, vals[1:]):
                self.assertLessEqual(a, b + 1e-9,
                    f"{st}: relativity fell as density rose")

    def test_within_guardrails(self):
        for st in ["NJ", "TX", "AK", "NV"]:
            for r in rating.territory_relativities(st).values():
                self.assertGreaterEqual(r, rating.TERRITORY_MIN)
                self.assertLessEqual(r, rating.TERRITORY_MAX)

    def test_unknown_state_is_empty(self):
        self.assertEqual(rating.territory_relativities("ZZ"), {})


class TestVehicleFactor(unittest.TestCase):
    def test_luxury_costs_more_than_economy(self):
        kia = rating.vehicle_factor(2020, "Kia", "Forte")["factor"]
        bmw = rating.vehicle_factor(2020, "BMW", "3 Series")["factor"]
        self.assertGreater(bmw, kia)

    def test_sports_body_costs_more_than_minivan(self):
        van = rating.vehicle_factor(2020, "Honda", "Odyssey")["factor"]
        spt = rating.vehicle_factor(2020, "Chevrolet", "Corvette")["factor"]
        self.assertGreater(spt, van)

    def test_newer_vehicle_costs_more(self):
        new = rating.vehicle_factor(2026, "Toyota", "Camry")["factor"]
        old = rating.vehicle_factor(2006, "Toyota", "Camry")["factor"]
        self.assertGreater(new, old)

    def test_unknown_make_falls_back_and_flags_itself(self):
        r = rating.vehicle_factor(2020, "Studebaker", "Champion")
        self.assertFalse(r["components"]["make_tier"]["matched"])
        self.assertFalse(r["components"]["body_class"]["matched"])
        self.assertAlmostEqual(r["components"]["make_tier"]["factor"], 1.00)

    def test_missing_year_uses_median_age_and_flags_it(self):
        r = rating.vehicle_factor(None, "Honda", "Civic")
        self.assertFalse(r["components"]["vehicle_age"]["matched"])
        self.assertEqual(r["components"]["vehicle_age"]["years"], 7)

    def test_absurd_year_is_rejected(self):
        self.assertFalse(
            rating.vehicle_factor(1776, "Honda", "Civic")["components"]["vehicle_age"]["matched"])

    def test_case_insensitive_lookup(self):
        a = rating.vehicle_factor(2020, "honda", "civic")["factor"]
        b = rating.vehicle_factor(2020, "HONDA", "CIVIC")["factor"]
        self.assertAlmostEqual(a, b)


class TestDriverFactor(unittest.TestCase):
    def test_young_drivers_pay_more(self):
        self.assertGreater(rating.driver_factor(18)["factor"],
                           rating.driver_factor(40)["factor"])

    def test_curve_is_u_shaped(self):
        self.assertGreater(rating.driver_factor(85)["factor"],
                           rating.driver_factor(55)["factor"])

    def test_clamped_to_rateable_range(self):
        self.assertEqual(rating.driver_factor(2)["age"], 16)
        self.assertEqual(rating.driver_factor(140)["age"], 90)

    def test_none_is_neutral(self):
        r = rating.driver_factor(None)
        self.assertAlmostEqual(r["factor"], 1.00)
        self.assertFalse(r["matched"])


class TestQuote(unittest.TestCase):
    def test_premium_equals_the_product_of_its_factors(self):
        """The headline number must be exactly what the displayed chain implies."""
        q = rating.quote_state("NJ", 2006, "Honda", "Civic",
                               driver_age=22, coverage="full_coverage")
        rel = rating.territory_relativities("NJ")
        for c in q.counties:
            expected = round(q.state_base_rate * rel[c.fips] *
                             q.vehicle["factor"] * q.driver["factor"] *
                             q.coverage["factor"])
            self.assertEqual(c.annual_premium, int(expected), f"mismatch in {c.county}")

    def test_liability_only_is_cheaper_than_full(self):
        a = rating.quote_state("NJ", 2020, "Honda", "Civic", 30, "liability_only")
        b = rating.quote_state("NJ", 2020, "Honda", "Civic", 30, "full_coverage")
        self.assertLess(a.statewide["mean"], b.statewide["mean"])

    def test_subject_county_is_resolved_and_ranked(self):
        # 34003 = Bergen County, NJ
        q = rating.quote_state("NJ", 2006, "Honda", "Civic", 22, subject_fips="34003")
        self.assertIsNotNone(q.subject_county)
        self.assertIn("Bergen", q.subject_county["county"])
        self.assertTrue(1 <= q.subject_county["rank_in_state"] <= 21)

    def test_every_state_rates_without_error(self):
        ref = rating.load_reference_data()
        for st in ref["base_rates"]:
            q = rating.quote_state(st, 2018, "Toyota", "Camry", 35)
            self.assertGreater(len(q.counties), 0, f"{st} produced no counties")
            self.assertGreater(q.statewide["min"], 0)
            self.assertLessEqual(q.statewide["min"], q.statewide["max"])

    def test_unknown_state_raises(self):
        with self.assertRaises(ValueError):
            rating.quote_state("ZZ", 2020, "Honda", "Civic")

    def test_counties_sorted_most_expensive_first(self):
        q = rating.quote_state("TX", 2020, "Ford", "F-150", 40)
        prem = [c.annual_premium for c in q.counties]
        self.assertEqual(prem, sorted(prem, reverse=True))



class TestModelResolution(unittest.TestCase):
    def test_typos_and_accents_snap_to_known_model(self):
        for typed, want in [("hurrican", "Huracan"), ("Huracán", "Huracan"),
                            ("f150", "F-150"), ("cr v", "CR-V"), ("Camery", "Camry")]:
            self.assertEqual(rating.canonical_model(typed), want, typed)

    def test_close_but_distinct_models_are_not_merged(self):
        self.assertEqual(rating.canonical_model("Model 3"), "Model 3")
        self.assertEqual(rating.canonical_model("Model S"), "Model S")

    def test_unknown_model_passes_through_and_is_flagged(self):
        bc = rating.vehicle_factor(2020, "Honda", "Zzyzx")["components"]["body_class"]
        self.assertFalse(bc["matched"])
        self.assertEqual(bc["name"], "sedan")

    def test_exotic_rates_as_sports_car(self):
        bc = rating.vehicle_factor(2020, "Lamborghini", "hurrican")["components"]["body_class"]
        self.assertTrue(bc["matched"])
        self.assertEqual((bc["name"], bc["model"]), ("sports", "Huracan"))
        # an unknown exotic still defaults to sports, not sedan
        bc = rating.vehicle_factor(2020, "Ferrari", "Enzo")["components"]["body_class"]
        self.assertEqual(bc["name"], "sports")

if __name__ == "__main__":
    unittest.main(verbosity=2)
