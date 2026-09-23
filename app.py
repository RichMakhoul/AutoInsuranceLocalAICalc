"""
Flask API for the auto-rate explorer.

Route map
---------
  GET  /                      single-page frontend
  GET  /api/health            service + model status
  GET  /api/states            states available for rating
  GET  /api/places/search     city autocomplete
  GET  /api/geojson/<state>   county boundaries for the map
  POST /api/parse             free text -> {year, make, model}        [LLM]
  POST /api/quote             rate a risk across every county          [deterministic]
  POST /api/explain           narrate an already-computed quote         [LLM, grounded]

/api/quote never calls the LLM. /api/explain never computes a premium.
"""
from __future__ import annotations

import json
import os
import sys

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import llm
import rating

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND = os.path.join(BASE, "frontend")
DATA = os.path.join(BASE, "data")

app = Flask(__name__, static_folder=None)

with open(os.path.join(DATA, "places.json")) as f:
    PLACES: dict[str, dict] = json.load(f)

STATE_NAMES = {
    "AL":"Alabama","AK":"Alaska","AZ":"Arizona","AR":"Arkansas","CA":"California",
    "CO":"Colorado","CT":"Connecticut","DE":"Delaware","DC":"District of Columbia",
    "FL":"Florida","GA":"Georgia","HI":"Hawaii","ID":"Idaho","IL":"Illinois",
    "IN":"Indiana","IA":"Iowa","KS":"Kansas","KY":"Kentucky","LA":"Louisiana",
    "ME":"Maine","MD":"Maryland","MA":"Massachusetts","MI":"Michigan","MN":"Minnesota",
    "MS":"Mississippi","MO":"Missouri","MT":"Montana","NE":"Nebraska","NV":"Nevada",
    "NH":"New Hampshire","NJ":"New Jersey","NM":"New Mexico","NY":"New York",
    "NC":"North Carolina","ND":"North Dakota","OH":"Ohio","OK":"Oklahoma","OR":"Oregon",
    "PA":"Pennsylvania","RI":"Rhode Island","SC":"South Carolina","SD":"South Dakota",
    "TN":"Tennessee","TX":"Texas","UT":"Utah","VT":"Vermont","VA":"Virginia",
    "WA":"Washington","WV":"West Virginia","WI":"Wisconsin","WY":"Wyoming",
}

# city name -> [state, ...], for resolving a city typed without a state
_CITY_STATES: dict[str, list[str]] = {}
for _k in PLACES:
    _n, _s = _k.rsplit("|", 1)
    _CITY_STATES.setdefault(_n, []).append(_s)


@app.get("/")
def index():
    return send_from_directory(FRONTEND, "index.html")


@app.get("/static/<path:fname>")
def static_files(fname):
    return send_from_directory(FRONTEND, fname)


@app.get("/api/health")
def health():
    ref = rating.load_reference_data()
    return jsonify({
        "ok": True,
        "llm": {"available": llm.available(), "model": llm.MODEL},
        "counties": len(ref["counties"]),
        "places": len(PLACES),
        "states": len(ref["base_rates"]),
    })


@app.get("/api/states")
def states():
    ref = rating.load_reference_data()
    return jsonify([
        {"code": s, "name": STATE_NAMES.get(s, s), "base_rate": ref["base_rates"][s],
         "counties": len(ref["by_state"].get(s, []))}
        for s in sorted(ref["base_rates"])
    ])


@app.get("/api/places/search")
def place_search():
    q = (request.args.get("q") or "").strip().lower()
    st = (request.args.get("state") or "").strip().upper()
    if len(q) < 2:
        return jsonify([])
    hits = []
    for key, p in PLACES.items():
        if st and p["state"] != st:
            continue
        if p["name"].lower().startswith(q):
            hits.append({"name": p["name"], "state": p["state"],
                         "county": p["county"], "fips": p["fips"],
                         "lat": p["lat"], "lon": p["lon"],
                         "label": f"{p['name']}, {p['state']}"})
            if len(hits) > 400:
                break
    hits.sort(key=lambda h: (len(h["name"]), h["name"], h["state"]))
    return jsonify(hits[:25])


@app.get("/api/geojson/<state>")
def geojson(state):
    st = state.upper()
    path = os.path.join(DATA, "geojson", f"{st}.json")
    if not os.path.exists(path):
        return jsonify({"error": f"no boundaries for {st}"}), 404
    return send_from_directory(os.path.join(DATA, "geojson"), f"{st}.json")


@app.post("/api/parse")
def parse():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    out = llm.parse_vehicle(text)
    # Snap the model onto a known name (typos, accents) so the UI shows it corrected.
    if out.get("model"):
        out["model"] = rating.canonical_model(out["model"])
    return jsonify(out)


def _resolve_place(city: str, state: str | None):
    """city + optional state -> place record. Ambiguity is reported, not guessed."""
    c = (city or "").strip().lower()
    if not c:
        return None, None
    if state:
        return PLACES.get(f"{c}|{state.strip().upper()}"), None
    opts = _CITY_STATES.get(c, [])
    if len(opts) == 1:
        return PLACES.get(f"{c}|{opts[0]}"), None
    if len(opts) > 1:
        return None, sorted(opts)
    return None, None


@app.post("/api/quote")
def quote():
    b = request.get_json(silent=True) or {}
    city, state = b.get("city"), b.get("state")

    place, ambiguous = _resolve_place(city, state)
    if ambiguous:
        return jsonify({"error": "ambiguous_city", "city": city,
                        "states": ambiguous}), 409
    if city and not place:
        return jsonify({"error": "unknown_city", "city": city,
                        "hint": "Try the city autocomplete."}), 404

    st = (place["state"] if place else (state or "")).upper()
    if not st:
        return jsonify({"error": "state_required"}), 400

    try:
        year = int(b["year"]) if b.get("year") else None
    except (TypeError, ValueError):
        year = None
    try:
        age = int(b["driver_age"]) if b.get("driver_age") else None
    except (TypeError, ValueError):
        age = None

    try:
        res = rating.quote_state(
            state=st, year=year, make=b.get("make"), model=b.get("model"),
            driver_age=age, coverage=b.get("coverage", "full_coverage"),
            subject_fips=place["fips"] if place else None,
        )
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    out = res.to_dict()
    out["state_name"] = STATE_NAMES.get(st, st)
    if place:
        out["place"] = place
    return jsonify(out)


@app.post("/api/explain")
def explain():
    """
    Builds a facts packet from an already-computed quote and asks the model to
    narrate it. Everything numeric here was produced by rating.py.
    """
    b = request.get_json(silent=True) or {}
    q = b.get("quote") or {}
    subj = q.get("subject_county")
    if not subj:
        return jsonify({"error": "quote_with_subject_county_required"}), 400

    v = q.get("vehicle", {}).get("components", {})
    model = rating.canonical_model(b.get("model")) if b.get("model") else None
    desc = " ".join(str(x) for x in [b.get("year"), b.get("make"), model] if x) or "vehicle"

    facts = {
        "vehicle_desc": desc,
        "county": subj["county"].replace(" County", "").replace(" Planning Region", ""),
        "state": q.get("state"),
        "premium": subj["annual_premium"],
        "coverage": q.get("coverage", {}).get("level", "full_coverage"),
        "density": subj["density"],
        "territory_relativity": subj["territory_relativity"],
        "rank_in_state": subj.get("rank_in_state"),
        "vs_state_mean_pct": subj.get("vs_state_mean_pct"),
        "state_base_rate": q.get("state_base_rate"),
        "vehicle": {
            "total_factor": q.get("vehicle", {}).get("factor"),
            "make_tier": v.get("make_tier", {}).get("name"),
            "make_tier_factor": v.get("make_tier", {}).get("factor"),
            "body_class": v.get("body_class", {}).get("name"),
            "body_class_factor": v.get("body_class", {}).get("factor"),
            "vehicle_age_years": v.get("vehicle_age", {}).get("years"),
            "vehicle_age_factor": v.get("vehicle_age", {}).get("factor"),
        },
        "driver": q.get("driver", {}),
        "statewide": q.get("statewide", {}),
    }
    result = llm.explain_quote(facts)
    result["facts_sent"] = facts        # surfaced in the UI: auditable grounding
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"rate explorer -> http://localhost:{port}")
    print(f"  llm: {'up' if llm.available() else 'DOWN (deterministic fallback)'}")
    app.run(host="127.0.0.1", port=port, debug=False)
