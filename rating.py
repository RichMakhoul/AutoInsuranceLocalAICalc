"""
Deterministic personal-auto rating engine.

Design contract
---------------
Every dollar figure this application displays is produced HERE, by arithmetic,
from published tables. The LLM never produces, adjusts, or rounds a premium.

That split is deliberate. In a regulated line like personal auto, a premium has
to be reproducible and explainable -- a regulator can ask why a given insured
was charged a given amount, and "the language model said so" is not an answer.
So the model does language; this module does money.

Rating structure (standard multiplicative plan):

    premium = base_rate(state)
            x territory_relativity(county)
            x vehicle_factor(year, make, model)
            x driver_factor(age)
            x coverage_factor(level)

Territory relativities are normalized to be PREMIUM-NEUTRAL at the state level:
the population-weighted mean relativity within a state is exactly 1.0. That means
redistributing premium across counties does not change the statewide average --
which is how filed territory plans actually work, and it keeps the state base
rate meaningful as a calibration anchor.
"""
from __future__ import annotations

import difflib
import json
import math
import os
import re
import unicodedata
from dataclasses import dataclass, asdict, field
from functools import lru_cache
from typing import Any

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

# Territory transform constants.
#   DENSITY_K      - slope on log10(density). Controls how much spread the
#                    territory relativity has. 0.35 yields roughly 0.72-1.40
#                    across a high-contrast state like NJ, in line with the
#                    spread seen in real filed territory plans.
#   TERRITORY_MIN/MAX - guardrails. Filed plans cap relativities too; without a
#                    cap, a near-empty county (Loving County TX, ~0.1/sq mi)
#                    would produce an absurd credit.
DENSITY_K = 0.35
TERRITORY_MIN = 0.65
TERRITORY_MAX = 1.60

COVERAGE_FACTORS = {
    "liability_only": 0.58,
    "standard": 1.00,
    "full_coverage": 1.00,
    "full_low_deductible": 1.16,
}

# Driver age relativity. Young drivers carry markedly higher loss frequency;
# the curve flattens through middle age and rises slightly at the top end.
AGE_CURVE = [(16, 2.60), (18, 2.15), (21, 1.62), (25, 1.22), (30, 1.06),
             (35, 1.00), (45, 0.96), (55, 0.94), (65, 0.98), (75, 1.12), (85, 1.30)]


def _interp(curve: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear interpolation, flat outside the curve's range."""
    if x <= curve[0][0]:
        return curve[0][1]
    if x >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            t = (x - x0) / (x1 - x0)
            return y0 + t * (y1 - y0)
    return curve[-1][1]


def _load(name: str) -> Any:
    with open(os.path.join(DATA_DIR, name)) as f:
        return json.load(f)


@lru_cache(maxsize=1)
def load_reference_data() -> dict[str, Any]:
    """Load and index all rating tables once."""
    counties = _load("counties.json")
    base = _load("state_base_rates.json")
    veh = _load("vehicle_factors.json")

    # make -> tier factor, case-insensitive
    make_to_factor: dict[str, float] = {}
    make_to_tier: dict[str, str] = {}
    for tier, spec in veh["make_tier"].items():
        if tier.startswith("_"):
            continue
        for mk in spec["makes"]:
            make_to_factor[mk.lower()] = spec["factor"]
            make_to_tier[mk.lower()] = tier

    model_to_class = {k.lower().strip(): v
                      for k, v in veh["model_class"].items() if not k.startswith("_")}
    model_names = {_fold(k): k.strip()
                   for k in veh["model_class"] if not k.startswith("_")}

    by_state: dict[str, list[dict]] = {}
    for c in counties.values():
        by_state.setdefault(c["state"], []).append(c)

    return {
        "counties": counties,
        "by_state": by_state,
        "base_rates": base["rates"],
        "base_meta": base["_meta"],
        "vehicle": veh,
        "make_to_factor": make_to_factor,
        "make_to_tier": make_to_tier,
        "model_to_class": model_to_class,
        "model_names": model_names,
        "body_class": {k: v for k, v in veh["body_class"].items() if not k.startswith("_")},
        "age_curve_veh": [tuple(p) for p in veh["age_curve"]["breakpoints"]],
    }


@lru_cache(maxsize=64)
def territory_relativities(state: str) -> dict[str, float]:
    """
    Territory relativity per county FIPS within a state.

    Transform:  r_i = exp(K * (log10(density_i + 1) - mu))
    where mu is the population-weighted mean of log10(density + 1) in-state.
    Result is then rescaled so the population-weighted mean relativity is
    exactly 1.0, making the plan premium-neutral statewide.
    """
    ref = load_reference_data()
    counties = ref["by_state"].get(state.upper(), [])
    if not counties:
        return {}

    logs = {c["fips"]: math.log10(c["density"] + 1.0) for c in counties}
    pops = {c["fips"]: max(c["population"], 1) for c in counties}
    total_pop = sum(pops.values())

    mu = sum(logs[f] * pops[f] for f in logs) / total_pop
    raw = {f: math.exp(DENSITY_K * (logs[f] - mu)) for f in logs}

    # Rescale so the population-weighted mean is exactly 1.0 (premium neutrality),
    # then clamp. Clamping perturbs neutrality slightly; for realistic state
    # density distributions the effect is well under 1%.
    wmean = sum(raw[f] * pops[f] for f in raw) / total_pop
    return {f: round(min(max(raw[f] / wmean, TERRITORY_MIN), TERRITORY_MAX), 4)
            for f in raw}


def _fold(s: str) -> str:
    """Lowercase and strip accents/punctuation: 'Huracán' -> 'huracan', 'F-150' -> 'f150'."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def canonical_model(model: str | None) -> str | None:
    """
    Snap user-typed model text onto a known model name, tolerating accents,
    punctuation, and small typos ('hurrican' -> 'Huracan', 'f150' -> 'F-150').
    Returns the input unchanged (stripped) if nothing is close enough -- the
    engine then labels the body class ASSUMED rather than guessing silently.
    """
    if not model or not model.strip():
        return None
    ref = load_reference_data()
    names = ref["model_names"]                      # folded -> canonical
    key = _fold(model)
    if key in names:
        return names[key]
    if len(key) >= 4:
        hit = difflib.get_close_matches(key, list(names), n=1, cutoff=0.8)
        if hit:
            return names[hit[0]]
    return model.strip()


def vehicle_factor(year: int | None, make: str | None, model: str | None,
                   current_year: int = 2026) -> dict[str, Any]:
    """Vehicle relativity plus the component breakdown that produced it."""
    ref = load_reference_data()
    mk = (make or "").strip().lower()
    md = (model or "").strip().lower()

    tier_factor = ref["make_to_factor"].get(mk)
    tier_name = ref["make_to_tier"].get(mk)
    make_matched = tier_factor is not None
    if not make_matched:
        tier_factor, tier_name = 1.00, "mainstream (assumed)"

    resolved = canonical_model(model)
    body = ref["model_to_class"].get(resolved.lower()) if resolved else None
    model_matched = body is not None
    if not model_matched:
        # An unknown exotic is far more likely a sports car than a sedan.
        body = "sports" if tier_name == "exotic" else "sedan"
    body_factor = ref["body_class"].get(body, 1.00)

    if year and 1950 <= year <= current_year + 1:
        age = max(0, current_year - year)
        age_matched = True
    else:
        age = 7  # median US vehicle age, used when year is unknown
        age_matched = False
    age_factor = _interp(ref["age_curve_veh"], age)

    total = tier_factor * body_factor * age_factor
    return {
        "factor": round(total, 4),
        "components": {
            "make_tier": {"name": tier_name, "factor": round(tier_factor, 4),
                          "matched": make_matched},
            "body_class": {"name": body, "factor": round(body_factor, 4),
                           "matched": model_matched,
                           "model": resolved if model_matched else (model or None)},
            "vehicle_age": {"years": age, "factor": round(age_factor, 4),
                            "matched": age_matched},
        },
    }


def driver_factor(age: int | None) -> dict[str, Any]:
    if age is None:
        return {"factor": 1.00, "age": None, "matched": False}
    a = max(16, min(int(age), 90))
    return {"factor": round(_interp(AGE_CURVE, a), 4), "age": a, "matched": True}


@dataclass
class CountyQuote:
    fips: str
    county: str
    state: str
    population: int
    density: float
    lat: float
    lon: float
    territory_relativity: float
    annual_premium: int


@dataclass
class QuoteResult:
    state: str
    state_base_rate: int
    vehicle: dict[str, Any]
    driver: dict[str, Any]
    coverage: dict[str, Any]
    subject_county: dict[str, Any] | None
    counties: list[CountyQuote] = field(default_factory=list)
    statewide: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["counties"] = [asdict(c) if not isinstance(c, dict) else c
                         for c in self.counties]
        return d


def quote_state(state: str, year: int | None, make: str | None, model: str | None,
                driver_age: int | None = None, coverage: str = "full_coverage",
                subject_fips: str | None = None) -> QuoteResult:
    """
    Rate the same risk across every county in a state.

    Holding vehicle/driver/coverage fixed and varying only territory isolates
    the geographic component -- which is exactly what the map is meant to show.
    """
    ref = load_reference_data()
    st = state.upper()
    if st not in ref["base_rates"]:
        raise ValueError(f"unknown state: {state}")

    base = ref["base_rates"][st]
    veh = vehicle_factor(year, make, model)
    drv = driver_factor(driver_age)
    cov_factor = COVERAGE_FACTORS.get(coverage, 1.00)

    rel = territory_relativities(st)
    non_territory = veh["factor"] * drv["factor"] * cov_factor

    quotes: list[CountyQuote] = []
    for c in ref["by_state"][st]:
        r = rel.get(c["fips"], 1.0)
        quotes.append(CountyQuote(
            fips=c["fips"], county=c["name"], state=st,
            population=c["population"], density=c["density"],
            lat=c["lat"], lon=c["lon"],
            territory_relativity=r,
            annual_premium=int(round(base * r * non_territory)),
        ))
    quotes.sort(key=lambda q: q.annual_premium, reverse=True)

    prem = [q.annual_premium for q in quotes]
    pops = [q.population for q in quotes]
    tot_pop = sum(pops) or 1
    statewide = {
        "min": min(prem), "max": max(prem),
        "mean": int(round(sum(prem) / len(prem))),
        "population_weighted_mean": int(round(
            sum(p * w for p, w in zip(prem, pops)) / tot_pop)),
        "spread_pct": round((max(prem) / min(prem) - 1) * 100, 1),
        "county_count": len(quotes),
        "cheapest": {"county": quotes[-1].county, "premium": quotes[-1].annual_premium},
        "priciest": {"county": quotes[0].county, "premium": quotes[0].annual_premium},
    }

    subject = None
    if subject_fips:
        for q in quotes:
            if q.fips == subject_fips:
                rank = sorted(prem, reverse=True).index(q.annual_premium) + 1
                subject = {**asdict(q), "rank_in_state": rank,
                           "vs_state_mean_pct": round(
                               (q.annual_premium / statewide["population_weighted_mean"] - 1) * 100, 1)}
                break

    return QuoteResult(
        state=st, state_base_rate=base, vehicle=veh, driver=drv,
        coverage={"level": coverage, "factor": cov_factor},
        subject_county=subject, counties=quotes, statewide=statewide,
    )
