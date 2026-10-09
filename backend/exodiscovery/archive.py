"""Read-only public archive clients. Failures remain distinct from empty catalogs."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import requests

from . import store

TAP = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"


def scalar(v):
    if np.ma.is_masked(v):
        return None
    if isinstance(v, np.generic):
        v = v.item()
    if isinstance(v, bytes):
        return v.decode()
    if isinstance(v, float) and not np.isfinite(v):
        return None
    return v if isinstance(v, (int, float, str, bool, type(None))) else str(v)


def rows(table):
    return [{k: scalar(row[k]) for k in table.colnames} for row in table]


def cached(key, fetch, hours=24):
    old = store.cache_get(key)
    def complete(payload):
        return all(v.get('status') == 'complete' for v in payload.get('records',{}).values())
    if old and complete(old['payload']) and (datetime.now(timezone.utc) - datetime.fromisoformat(old["created"])).total_seconds() < hours * 3600:
        return old["payload"]
    result = fetch()
    if complete(result):
        store.cache_put(key, result)
    return result


def search(target, mission="TESS"):
    import lightkurve as lk
    from astroquery.mast import Observations
    Observations.TIMEOUT = 60

    def fetch():
        author = "SPOC" if mission == "TESS" else "Kepler"
        result = lk.search_lightcurve(target, mission=mission, author=author)
        products = []
        for row in rows(result.table):
            products.append({
                "uri": row.get("dataURI"), "filename": row.get("productFilename"),
                "sector": row.get("sequence_number"), "mission": row.get("mission"),
                "author": row.get("author"), "exptime": row.get("exptime"),
                "year": row.get("year"), "distance_arcsec": row.get("distance"),
                "target_name": row.get("target_name"), "ra": row.get("s_ra"), "dec": row.get("s_dec"),
                "size": row.get("size"), "obs_id": row.get("obs_id"),
            })
        return {"target": target, "mission": mission, "queried_at": store.now(), "products": products,
                "source": "https://mast.stsci.edu", "pipeline": author}
    return cached(f"search:{mission}:{target.lower().strip()}", fetch)


def select_products(search_result, sectors, limit):
    products = [p for p in search_result["products"] if p["uri"] and (not sectors or p["sector"] in sectors)]
    if products and any(p.get('target_name') for p in products):
        nearest = min(products,key=lambda p:float(p.get('distance_arcsec') or 0)).get('target_name')
        products = [p for p in products if p.get('target_name')==nearest]
    # One cadence per sector prevents double counting simultaneous observations.
    products.sort(key=lambda p: (p["sector"] or 0, abs(float(p["exptime"] or 120) - 120)))
    selected = {}
    for p in products:
        selected.setdefault(p["sector"], p)
    return list(selected.values())[:limit]


def download(product, max_bytes, notify=lambda s: None):
    from .allowance import current
    allowance = current.get()
    uri = product["uri"]
    if not uri.startswith("mast:"):
        raise ValueError("Expected a MAST data URI")
    name = hashlib.sha256(uri.encode()).hexdigest()[:20] + ".fits"
    path = store.DATA / "observations" / name
    if allowance:
        max_bytes = allowance.reserve(uri,path.stat().st_size if path.exists() else product.get('size'),max_bytes)
    url = "https://mast.stsci.edu/api/v0.1/Download/file?" + urlencode({"uri": uri})
    if not path.exists():
        notify(f"Downloading {product['filename']}")
        tmp = path.with_suffix(".part")
        try:
            with requests.get(url, stream=True, timeout=(20, 90)) as r:
                r.raise_for_status()
                if int(r.headers.get("content-length", 0)) > max_bytes:
                    raise ValueError("Product exceeds remaining download budget")
                size = 0
                with tmp.open("wb") as f:
                    for chunk in r.iter_content(1024 * 1024):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ValueError("Download budget exceeded")
                        f.write(chunk)
            with tmp.open('rb') as verify:
                if verify.read(6) != b'SIMPLE':
                    raise ValueError('Archive response is not a FITS product; response was not cached')
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if allowance:
        allowance.settle(uri,path.stat().st_size)
    meta_path = path.with_suffix(".json")
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())
        if meta["sha256"] != digest:
            raise ValueError("Cached FITS checksum mismatch; remove damaged file and retry")
    else:
        meta = {**product, "url": url, "downloaded_at": store.now(), "sha256": digest,
                "local_file": name, "bytes": path.stat().st_size}
        meta_path.write_text(json.dumps(meta, indent=2))
    return path, meta


def read_lightcurve(path, quality="default"):
    from astropy.io import fits
    from lightkurve.utils import TessQualityFlags, KeplerQualityFlags
    with fits.open(path, memmap=False) as hdul:
        h, dh, d = hdul[0].header, hdul[1].header, hdul[1].data
        mission = h.get("TELESCOP", "TESS")
        flag = TessQualityFlags if "TESS" in mission else KeplerQualityFlags
        mask_value = flag.DEFAULT_BITMASK if quality == "default" else 0x7FFFFFFF
        qname = "QUALITY" if "QUALITY" in d.names else "SAP_QUALITY"
        t = np.asarray(d["TIME"], dtype=float) + float(dh.get("BJDREFI", 0)) + float(dh.get("BJDREFF", 0))
        f = np.asarray(d["PDCSAP_FLUX"], dtype=float)
        e = np.asarray(d["PDCSAP_FLUX_ERR"], dtype=float)
        q = np.asarray(d[qname], dtype=np.int64)
        good = np.isfinite(t) & np.isfinite(f) & np.isfinite(e) & (e > 0) & (f > 0) & ((q & mask_value) == 0)
        sector = int(h.get("SECTOR", h.get("QUARTER", h.get("CAMPAIGN", 0))))
        metadata = {"tic_id": scalar(h.get("TICID")), "ra": scalar(h.get("RA_OBJ")), "dec": scalar(h.get("DEC_OBJ")),
                    "stellar_radius_solar": scalar(h.get("RADIUS")), "teff_k": scalar(h.get("TEFF")),
                    "tmag": scalar(h.get("TESSMAG")), "sector": sector,
                    "pipeline_version": h.get("PROCVER"), "data_release": h.get("DATA_REL"),
                    "timesys": dh.get("TIMESYS"), "bjd_reference": dh.get("BJDREFI"),
                    "quality_bitmask": mask_value, "cadences_total": len(t), "cadences_kept": int(good.sum()),
                    "crowdsap": scalar(dh.get("CROWDSAP")), "flfrcsap": scalar(dh.get("FLFRCSAP"))}
        if metadata["timesys"] != "TDB":
            raise ValueError("Unsupported time system: expected BJD_TDB")
        return t[good], f[good], e[good], np.full(good.sum(), sector), metadata


def tap(query):
    r = requests.get(TAP, params={"query": query, "format": "json"}, timeout=60)
    r.raise_for_status()
    return r.json(), r.url


def catalogs(ra, dec, tic_id=None):
    key = f"catalogs:{ra:.6f}:{dec:.6f}:{tic_id}"
    def fetch():
        result = {"queried_at": store.now(), "ra": ra, "dec": dec, "cone_arcsec": 30, "records": {}}
        # Rectangular TAP prefilter, followed by an exact spherical cone locally.
        # This also works with TAP gateways that reject spatial ADQL functions.
        width = min(180, .0083333333 / max(abs(np.cos(np.deg2rad(dec))), 1e-6))
        lo, hi = (ra-width) % 360, (ra+width) % 360
        ra_filter = f"ra between {lo:.8f} and {hi:.8f}" if lo < hi else f"(ra >= {lo:.8f} or ra <= {hi:.8f})"
        cone = f"{ra_filter} and dec between {max(-90,dec-.0083333333):.8f} and {min(90,dec+.0083333333):.8f}"
        queries = {
            "confirmed": f"select pl_name,hostname,pl_orbper,pl_orbpererr1,pl_rade,st_rad,ra,dec,tic_id from pscomppars where {cone}",
            "toi": f"select toi,tid,tfopwg_disp,pl_orbper,pl_tranmid,pl_trandurh,pl_trandep,ra,dec from toi where {cone}",
        }
        for name, query in queries.items():
            try:
                data, url = tap(query)
                from astropy.coordinates import SkyCoord
                import astropy.units as u
                center = SkyCoord(ra, dec, unit="deg")
                data = [row for row in data if row.get("ra") is not None and row.get("dec") is not None and
                        center.separation(SkyCoord(row["ra"], row["dec"], unit="deg")) < 30*u.arcsec]
                result["records"][name] = {"status": "complete", "rows": data, "url": url, "query": query}
            except Exception as exc:
                result["records"][name] = {"status": "unavailable", "rows": [], "error": type(exc).__name__, "query": query}
        if tic_id:
            result["exofop_url"] = f"https://exofop.ipac.caltech.edu/tess/target.php?id={int(tic_id)}"
        result["follow_up_status"] = "not_reviewed"
        return result
    return cached(key+":v2", fetch)


def tic(target):
    from astroquery.mast import Catalogs
    Catalogs.TIMEOUT = 60
    match = re.fullmatch(r"\s*(?:TIC\s*)?(\d+)\s*", target, re.I)
    table = Catalogs.query_criteria(catalog="TIC", ID=match[1]) if match else Catalogs.query_object(target, catalog="TIC", radius=0.005)
    keep = [k for k in ["ID", "ra", "dec", "Tmag", "Teff", "rad", "e_rad", "mass", "logg", "GAIA", "contratio"] if k in table.colnames]
    return {"queried_at": store.now(), "source": "MAST TIC", "rows": rows(table[keep])[:30]}


def gaia(ra, dec):
    query = ("SELECT TOP 100 source_id,ra,dec,phot_g_mean_mag,parallax,pmra,pmdec,ruwe "
             "FROM gaiadr3.gaia_source WHERE 1=CONTAINS(POINT('ICRS',ra,dec),"
             f"CIRCLE('ICRS',{float(ra)},{float(dec)},0.0333333333))")
    url = "https://gea.esac.esa.int/tap-server/tap/sync"
    r = requests.get(url, params={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": query}, timeout=90)
    r.raise_for_status()
    payload = r.json()
    names = [m["name"] for m in payload["metadata"]]
    data = [dict(zip(names, row)) for row in payload["data"]]
    for row in data:
        row["source_id"] = str(row["source_id"])
        row["separation_arcsec"] = float(3600 * np.hypot((row["ra"] - ra) * np.cos(np.deg2rad(dec)), row["dec"] - dec))
    return {"queried_at": store.now(), "source": url, "query": query, "rows": sorted(data, key=lambda r: r["separation_arcsec"]),
            "caveat": "Gaia DR3 positions are epoch 2016; proper motions are listed, not propagated. G is not TESS magnitude. Unresolved companions and incomplete faint sources remain possible."}
