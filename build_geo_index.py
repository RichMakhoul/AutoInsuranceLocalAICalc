"""
Build the offline geography index.

Produces:
  data/geojson/<ST>.json  - county boundaries per state (served to Leaflet)
  data/places.json        - "city|ST" -> county FIPS + coordinates

Sources, all public and key-free:
  - plotly/datasets county GeoJSON      : boundaries for 49 states + DC
  - Census TIGERweb (live query)        : Connecticut, which replaced counties
                                          with 9 planning regions in 2022 and is
                                          therefore absent from older boundary files
  - Census Gazetteer 'places'           : cities/boroughs/CDPs -> resolved to county
                                          by point-in-polygon
  - Census Gazetteer 'county subdivisions' : townships (New England + mid-Atlantic
                                          municipalities that are NOT Census places --
                                          e.g. Montclair NJ). Their 10-digit GEOID
                                          begins with the 5-digit county FIPS, so the
                                          county is read directly, no geometry needed.

The running application makes no outbound requests; everything is resolved here.
"""
import csv, json, os, re, collections, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
GEODIR = os.path.join(DATA, "geojson")

CT_URL = ("https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/"
          "State_County/MapServer/1/query?where=STATE%3D%2709%27"
          "&outFields=GEOID,NAME,STATE,COUNTY&outSR=4326&f=geojson")

# Census appends a legal-status suffix to every name; strip it for lookup.
SUFFIXES = (" CDP", " city", " borough", " town", " village", " township",
            " municipality", " (balance)", " comunidad", " zona urbana",
            " plantation", " gore", " grant", " location", " purchase",
            " reservation", " UT", " charter township", " metro township")

# County subdivisions that are administrative filler rather than real places.
SKIP_COUSUB = re.compile(
    r"^(unorganized|unincorporated|not defined|county subdivisions not defined|"
    r"township \d+|district \d+|precinct \d+|ward \d+|\d+)", re.I)


def bbox(geom):
    xs, ys = [], []
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    for poly in polys:
        for pt in poly[0]:
            xs.append(pt[0]); ys.append(pt[1])
    return min(xs), min(ys), max(xs), max(ys)


def point_in_ring(x, y, ring):
    inside, n = False, len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-300) + xi):
            inside = not inside
        j = i
    return inside


def point_in_geom(x, y, geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    for poly in polys:
        if point_in_ring(x, y, poly[0]) and not any(
                point_in_ring(x, y, h) for h in poly[1:]):
            return True
    return False


def strip_suffix(name):
    for suf in sorted(SUFFIXES, key=len, reverse=True):
        if name.endswith(suf):
            return name[: -len(suf)].strip()
    return name.strip()


def main():
    counties = json.load(open(os.path.join(DATA, "counties.json")))
    features = json.load(open(os.path.join(DATA, "counties_raw.geojson")))["features"]

    # Connecticut: pull current planning-region boundaries.
    try:
        with urllib.request.urlopen(CT_URL, timeout=60) as r:
            ct = json.load(r)
        for f in ct.get("features", []):
            g = f["properties"].get("GEOID")
            if g:
                f["properties"] = {"STATE": g[:2], "COUNTY": g[2:]}
                features.append(f)
        print(f"CT planning regions merged: {len(ct.get('features', []))}")
    except Exception as e:
        print(f"WARNING: CT boundary fetch failed ({e}); CT will be unavailable")

    by_state = collections.defaultdict(lambda: {"type": "FeatureCollection", "features": []})
    index = collections.defaultdict(list)
    for feat in features:
        fips = feat["properties"]["STATE"] + feat["properties"]["COUNTY"]
        c = counties.get(fips)
        if not c:
            continue
        st = c["state"]
        feat["properties"] = {"fips": fips, "name": c["name"], "state": st,
                              "population": c["population"], "density": c["density"]}
        by_state[st]["features"].append(feat)
        index[st].append((fips, bbox(feat["geometry"]), feat["geometry"]))

    os.makedirs(GEODIR, exist_ok=True)
    for st, fc in by_state.items():
        with open(os.path.join(GEODIR, f"{st}.json"), "w") as f:
            json.dump(fc, f, separators=(",", ":"))

    places, stats = {}, collections.Counter()

    def add(key, rec, source):
        if key in places:
            stats[f"dupe_{source}"] += 1
            return
        places[key] = rec
        stats[source] += 1

    # ---- pass 1: Census places (point-in-polygon) ----
    with open(os.path.join(DATA, "2023_Gaz_place_national.txt"), encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            st = row["USPS"]
            if st not in index:
                continue
            try:
                lat, lon = float(row["INTPTLAT"]), float(row["INTPTLONG"])
            except ValueError:
                continue
            name = strip_suffix(row["NAME"])
            fips = None
            for cf, (x0, y0, x1, y1), geom in index[st]:
                if x0 <= lon <= x1 and y0 <= lat <= y1 and point_in_geom(lon, lat, geom):
                    fips = cf
                    break
            if not fips:
                stats["unresolved_place"] += 1
                continue
            add(f"{name.lower()}|{st}", {"name": name, "state": st, "fips": fips,
                                         "lat": lat, "lon": lon,
                                         "county": counties[fips]["name"]}, "place")

    # ---- pass 2: county subdivisions (county read from GEOID prefix) ----
    with open(os.path.join(DATA, "2023_Gaz_cousubs_national.txt"), encoding="latin-1") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
            st, geoid = row["USPS"], row["GEOID"]
            if st not in index or len(geoid) < 5:
                continue
            fips = geoid[:5]
            if fips not in counties:
                continue
            raw = row["NAME"]
            if SKIP_COUSUB.match(raw):
                continue
            name = strip_suffix(raw)
            if not name or SKIP_COUSUB.match(name):
                continue
            try:
                lat, lon = float(row["INTPTLAT"]), float(row["INTPTLONG"])
            except ValueError:
                continue
            add(f"{name.lower()}|{st}", {"name": name, "state": st, "fips": fips,
                                         "lat": lat, "lon": lon,
                                         "county": counties[fips]["name"]}, "cousub")

    with open(os.path.join(DATA, "places.json"), "w") as f:
        json.dump(places, f, separators=(",", ":"))

    print(f"states with boundaries : {len(by_state)}")
    print(f"places indexed         : {len(places):,}  "
          f"(places {stats['place']:,}, townships {stats['cousub']:,})")
    print(f"  skipped: dupes {stats['dupe_place'] + stats['dupe_cousub']:,}, "
          f"unresolved {stats['unresolved_place']:,}")
    print("\nprobes:")
    for p in ["lodi|NJ", "montclair|NJ", "newark|NJ", "jersey city|NJ",
              "hartford|CT", "austin|TX", "brooklyn|NY", "cambridge|MA"]:
        r = places.get(p)
        print(f"  {p:<16} -> {r['county'] if r else 'NOT FOUND'}")


if __name__ == "__main__":
    main()
