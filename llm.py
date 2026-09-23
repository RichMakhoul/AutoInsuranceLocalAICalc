"""
Ollama client. Language tasks only.

Two jobs, both chosen because they are genuinely language problems:

  1. parse_vehicle  - turn free text ("06 honda civic si") into structured
                      {year, make, model}. This is messy-input normalization,
                      which is what LLMs are good at and what regex is bad at.
  2. explain_quote  - narrate a premium that rating.py already computed.

explain_quote is GROUNDED: the prompt carries the computed factors, and the
model is instructed to use only those numbers. It is not asked what the premium
should be -- it is told what the premium is and asked to explain it in English.
If the model is unavailable, both paths degrade to deterministic fallbacks and
the application keeps working; the LLM is an enhancement, never a dependency.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
TIMEOUT = 120

KNOWN_MAKES = [
    "Acura","Alfa Romeo","Aston Martin","Audi","Bentley","BMW","Buick","Cadillac",
    "Chevrolet","Chrysler","Dodge","Ferrari","Ford","Genesis","GMC","Honda","Hyundai",
    "Infiniti","Isuzu","Jaguar","Jeep","Kia","Lamborghini","Land Rover","Lexus",
    "Lincoln","Lotus","Lucid","Maserati","Mazda","McLaren","Mercedes-Benz","Mercury",
    "Mini","Mitsubishi","Nissan","Oldsmobile","Plymouth","Polestar","Pontiac","Porsche",
    "Ram","Rivian","Rolls-Royce","Saab","Saturn","Scion","Smart","Subaru","Suzuki",
    "Tesla","Toyota","Volkswagen","Volvo",
]

_MAKE_ALIASES = {
    "chevy": "Chevrolet", "vw": "Volkswagen", "mercedes": "Mercedes-Benz",
    "benz": "Mercedes-Benz", "lambo": "Lamborghini", "beemer": "BMW", "bimmer": "BMW", "landrover": "Land Rover",
    "range rover": "Land Rover", "alfa": "Alfa Romeo", "rolls": "Rolls-Royce",
}


def available() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def _generate(prompt: str, system: str = "", fmt: str | None = None,
              num_predict: int = 400, temperature: float = 0.2) -> str:
    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if system:
        payload["system"] = system
    if fmt:
        payload["format"] = fmt
    req = urllib.request.Request(
        f"{OLLAMA_URL}/api/generate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode()).get("response", "").strip()


# --------------------------------------------------------------------------
# 1. Vehicle parsing
# --------------------------------------------------------------------------

PARSE_SYSTEM = (
    "You extract vehicle details from text. Respond with JSON only, no prose.\n"
    'Schema: {"year": <int|null>, "make": <string|null>, "model": <string|null>}\n'
    "Rules:\n"
    "- Expand 2-digit years: 06 -> 2006, 98 -> 1998, 23 -> 2023.\n"
    "- Normalize make to its full official name (chevy -> Chevrolet, vw -> Volkswagen).\n"
    "- Keep the model as written but capitalized properly (civic -> Civic, f150 -> F-150).\n"
    "- Use null for anything genuinely absent. Never invent a year."
)


def _regex_parse(text: str) -> dict:
    """Deterministic fallback used when Ollama is unavailable or returns junk."""
    t = text.strip()
    year = None
    m = re.search(r"\b(19[5-9]\d|20[0-4]\d)\b", t)
    if m:
        year = int(m.group(1))
    else:
        m2 = re.search(r"\b('?)(\d{2})\b", t)
        if m2:
            yy = int(m2.group(2))
            year = 2000 + yy if yy <= 26 else 1900 + yy

    make = None
    matched = None
    low = t.lower()
    # Exact make names first, so "Mercedes-Benz" isn't caught by the "mercedes" alias.
    for mk in sorted(KNOWN_MAKES, key=len, reverse=True):
        if mk.lower() in low:
            make = mk
            break
    if not make:
        for alias, full in _MAKE_ALIASES.items():
            if re.search(rf"\b{re.escape(alias)}\b", low):
                make, matched = full, alias
                break

    model = None
    if make:
        rest = re.sub(re.escape(matched or make), "", t, flags=re.I)
        rest = re.sub(rf"\b{re.escape(make.split()[0])}\b", "", rest, flags=re.I)
        rest = re.sub(r"\b('?)(19|20)?\d{2}\b", "", rest).strip(" ,-")
        if rest:
            model = " ".join(w.capitalize() for w in rest.split())
    return {"year": year, "make": make, "model": model, "source": "regex_fallback"}


def parse_vehicle(text: str) -> dict:
    if not text or not text.strip():
        return {"year": None, "make": None, "model": None, "source": "empty"}
    if not available():
        return _regex_parse(text)
    try:
        raw = _generate(
            f"Extract the vehicle from this text:\n{text}",
            system=PARSE_SYSTEM, fmt="json", num_predict=120, temperature=0.0,
        )
        data = json.loads(raw)
        year = data.get("year")
        if isinstance(year, str) and year.isdigit():
            year = int(year)
        if not isinstance(year, int) or not (1950 <= year <= 2027):
            year = None
        make = data.get("make") or None
        model = data.get("model") or None

        # Snap the make onto the known list so downstream lookups hit.
        if make:
            ml = str(make).lower()
            if ml in _MAKE_ALIASES:
                make = _MAKE_ALIASES[ml]
            else:
                for mk in KNOWN_MAKES:
                    if mk.lower() == ml:
                        make = mk
                        break

        out = {"year": year, "make": make, "model": model, "source": "llm"}
        # If the LLM produced nothing usable, fall back rather than return blanks.
        if not any([out["year"], out["make"], out["model"]]):
            return _regex_parse(text)
        return out
    except Exception:
        return _regex_parse(text)


# --------------------------------------------------------------------------
# 2. Grounded explanation
# --------------------------------------------------------------------------

EXPLAIN_SYSTEM = (
    "You are an insurance analyst explaining a rating result to a customer.\n\n"
    "HARD RULES\n"
    "1. Every number you cite must appear verbatim in the RATING FACTS. Never "
    "compute, estimate, adjust, or invent a figure.\n"
    "2. Explain causes correctly:\n"
    "   - Population density raises premium because urban driving produces more "
    "claims -- more vehicles interacting per mile, more theft, more "
    "uninsured-motorist and litigation exposure. It is NOT about demand for "
    "insurance, wealth, or how many people buy policies.\n"
    "   - A county's RANK and its percentage versus the state average are "
    "RESULTS of the premium, not causes of it. Report them as comparisons, "
    "never as reasons.\n"
    "   - A factor above 1.00 increased this premium; below 1.00 decreased it. "
    "An older vehicle lowers premium because it costs less to repair or replace.\n"
    "   - 'state_base_rate' and the population-weighted state average are DIFFERENT numbers. vs_state_mean_pct compares against the state AVERAGE, never against the base rate. Never describe a percentage as relative to the base rate.\n"
    "3. Do not invent any factor that is not in the facts -- no driving record, "
    "no credit, no mileage, no claims history.\n\n"
    "Write 3-4 short sentences of plain English. No jargon, no bullet points, no "
    "markdown, no headings. Lead with the premium and where it sits, then name "
    "the one or two factors that moved it most."
)


def _fallback_explanation(f: dict) -> str:
    v, s = f["vehicle"], f["statewide"]
    parts = [
        f"A {f['vehicle_desc']} in {f['county']} County rates at "
        f"${f['premium']:,} per year for {f['coverage'].replace('_', ' ')}."
    ]
    if f.get("vs_state_mean_pct") is not None:
        d = f["vs_state_mean_pct"]
        word = "above" if d > 0 else "below"
        parts.append(f"That is {abs(d):.1f}% {word} the {f['state']} average of "
                     f"${s['population_weighted_mean']:,}.")
    parts.append(
        f"The territory factor for this county is {f['territory_relativity']:.2f}, "
        f"driven by a population density of {f['density']:,.0f} people per square mile."
    )
    parts.append(
        f"Across {f['state']}, the same driver and vehicle would pay between "
        f"${s['min']:,} in {s['cheapest']['county']} and ${s['max']:,} in "
        f"{s['priciest']['county']}."
    )
    return " ".join(parts)


def explain_quote(facts: dict) -> dict:
    """
    `facts` is built by app.py from the QuoteResult. It contains only
    already-computed values; this function adds no arithmetic of its own.
    """
    if not available():
        return {"text": _fallback_explanation(facts), "source": "deterministic_fallback"}
    try:
        text = _generate(
            "RATING FACTS (use only these numbers):\n"
            + json.dumps(facts, indent=2)
            + "\n\nExplain this premium to the customer.",
            system=EXPLAIN_SYSTEM, num_predict=320, temperature=0.3,
        )
        if not text or len(text) < 40:
            return {"text": _fallback_explanation(facts), "source": "deterministic_fallback"}
        return {"text": text, "source": "llm"}
    except Exception:
        return {"text": _fallback_explanation(facts), "source": "deterministic_fallback"}
