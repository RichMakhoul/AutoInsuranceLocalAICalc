"""
Build the county territory table from real U.S. Census data.

Inputs (both public, no API key):
  - 2023_Gaz_counties_national.txt : Census Gazetteer -> land area (sq mi), centroid
  - co-est2024.csv                 : Census Population Estimates -> county population

Output:
  - data/counties.json : per-county FIPS, name, state, population, density, centroid

Territory relativities are NOT stored here -- they are computed in backend/rating.py
so the transform stays visible and auditable rather than baked into a data file.
"""
import csv, json, os

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")

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

def load_gazetteer():
    """FIPS -> land area in sq mi + internal point (centroid)."""
    out = {}
    path = os.path.join(DATA, "2023_Gaz_counties_national.txt")
    with open(path, encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            row = {k.strip(): (v.strip() if isinstance(v, str) else v)
                   for k, v in row.items() if k}
            fips = row["GEOID"]
            try:
                out[fips] = {
                    "land_sqmi": float(row["ALAND_SQMI"]),
                    "lat": float(row["INTPTLAT"]),
                    "lon": float(row["INTPTLONG"]),
                }
            except (ValueError, KeyError):
                continue
    return out

def load_population():
    """FIPS -> (name, state_name, 2024 population). SUMLEV 050 == county."""
    out = {}
    path = os.path.join(DATA, "co-est2024.csv")
    with open(path, encoding="latin-1") as f:
        for row in csv.DictReader(f):
            if row["SUMLEV"] != "050":
                continue
            fips = row["STATE"].zfill(2) + row["COUNTY"].zfill(3)
            out[fips] = {
                "name": row["CTYNAME"],
                "state_name": row["STNAME"],
                "population": int(row["POPESTIMATE2024"]),
            }
    return out

def main():
    gaz, pop = load_gazetteer(), load_population()
    counties = {}
    skipped = 0
    for fips, p in pop.items():
        g = gaz.get(fips)
        if not g or g["land_sqmi"] <= 0:
            skipped += 1
            continue
        abbr = STATE_ABBR.get(p["state_name"])
        if not abbr:
            skipped += 1
            continue
        counties[fips] = {
            "fips": fips,
            "name": p["name"],
            "state": abbr,
            "state_name": p["state_name"],
            "population": p["population"],
            "land_sqmi": round(g["land_sqmi"], 2),
            "density": round(p["population"] / g["land_sqmi"], 2),
            "lat": g["lat"],
            "lon": g["lon"],
        }

    out = os.path.join(DATA, "counties.json")
    with open(out, "w") as f:
        json.dump(counties, f, separators=(",", ":"))

    states = sorted({c["state"] for c in counties.values()})
    print(f"counties written : {len(counties)}  (skipped {skipped})")
    print(f"states covered   : {len(states)}")
    nj = {k: v for k, v in counties.items() if v["state"] == "NJ"}
    print(f"\nNJ counties: {len(nj)}")
    for c in sorted(nj.values(), key=lambda x: -x["density"])[:5]:
        print(f"  {c['name']:<22} pop {c['population']:>9,}  {c['density']:>9,.0f}/sq mi")
    for c in sorted(nj.values(), key=lambda x: x["density"])[:2]:
        print(f"  {c['name']:<22} pop {c['population']:>9,}  {c['density']:>9,.0f}/sq mi")

if __name__ == "__main__":
    main()
