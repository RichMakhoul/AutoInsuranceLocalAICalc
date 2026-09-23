"""
Build state base rates from the NAIC Auto Insurance Database Average Premium
Supplement (2023 edition).

Source : https://content.naic.org/sites/default/files/aut-db_1.pdf
Table 5: "Combined Average Premium" = Liability + Collision + Comprehensive
         average premium per insured vehicle, voluntary and residual market
         combined. This is the closest published analog to a full-coverage
         premium, which is what the rating plan treats as its base.

Requires `pdftotext` (poppler-utils). The parsed output is committed, so the
app does not depend on the PDF at runtime.
"""
import json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
PDF = os.path.join(DATA, "naic_2023_supplement.pdf")
SOURCE_URL = "https://content.naic.org/sites/default/files/aut-db_1.pdf"

STATE_ABBR = {
    "Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA",
    "Colorado":"CO","Connecticut":"CT","Delaware":"DE","District of Columbia":"DC",
    "Florida":"FL","Georgia":"GA","Hawaii":"HI","Idaho":"ID","Illinois":"IL",
    "Indiana":"IN","Iowa":"IA","Kansas":"KS","Kentucky":"KY","Louisiana":"LA",
    "Maine":"ME","Maryland":"MD","Massachusetts":"MA","Michigan":"MI","Minnesota":"MN",
    "Mississippi":"MS","Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV",
    "New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM","New York":"NY",
    "North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK","Oregon":"OR",
    "Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD",
    "Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA",
    "Washington":"WA","West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY",
}

NUM = r"[\d,]+\.\d{2}"


def extract_table(lines, header, want_year_index=0):
    """
    Pull one state->value mapping out of the table that follows `header`.
    Rows look like:  New Jersey   1,742.55   1,522.89   1,475.97  ...
    want_year_index picks the column (0 = most recent year).
    """
    start = None
    for i, ln in enumerate(lines):
        if header in ln and "=" not in ln and ":" not in ln:
            start = i
    if start is None:
        raise SystemExit(f"header not found: {header}")

    out = {}
    pattern = re.compile(r"^\s*([A-Za-z][A-Za-z .]+?)\s+((?:" + NUM + r"\s*){3,})$")
    for ln in lines[start: start + 90]:
        m = pattern.match(ln.rstrip())
        if not m:
            continue
        name = m.group(1).strip()
        if name not in STATE_ABBR:
            continue
        vals = re.findall(NUM, m.group(2))
        out[STATE_ABBR[name]] = float(vals[want_year_index].replace(",", ""))
    return out


def main():
    if not os.path.exists(PDF):
        sys.exit(f"missing {PDF}\nDownload it from {SOURCE_URL}")
    txt = subprocess.run(["pdftotext", "-layout", PDF, "-"],
                         capture_output=True, text=True, check=True).stdout
    lines = txt.splitlines()

    combined = extract_table(lines, "Combined Average Premium")
    if len(combined) != 51:
        sys.exit(f"expected 51 jurisdictions, parsed {len(combined)}")

    payload = {
        "_meta": {
            "description": ("Statewide combined average premium per insured vehicle "
                            "(liability + collision + comprehensive), used as the "
                            "rating-plan base rate that territory/vehicle/driver "
                            "relativities multiply against."),
            "source": "NAIC 2023 Auto Insurance Database Average Premium Supplement, Table 5",
            "source_url": SOURCE_URL,
            "data_year": 2023,
            "basis": ("Voluntary and residual market combined. Written premium / written "
                      "exposures. This is an observed market average, not a filed rate."),
            "generated_by": "scripts/build_base_rates.py",
        },
        "rates": {k: round(v, 2) for k, v in sorted(combined.items())},
    }
    out = os.path.join(DATA, "state_base_rates.json")
    with open(out, "w") as f:
        json.dump(payload, f, indent=2)

    r = payload["rates"]
    lo, hi = min(r, key=r.get), max(r, key=r.get)
    print(f"parsed {len(r)} jurisdictions from NAIC 2023 Table 5")
    print(f"  lowest  {lo} ${r[lo]:,.2f}")
    print(f"  highest {hi} ${r[hi]:,.2f}")
    print(f"  NJ      ${r['NJ']:,.2f}    NY ${r['NY']:,.2f}    TX ${r['TX']:,.2f}")
    print(f"  mean    ${sum(r.values())/len(r):,.2f}   (NAIC countrywide 2023: $1,438.46)")


if __name__ == "__main__":
    main()
