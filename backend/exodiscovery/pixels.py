import numpy as np
from scipy.ndimage import binary_erosion

from . import archive, science


def inspect_pixels(investigation, signal, job_id=None, sector=None, cadence_seconds=120):
    """Exploratory difference image and SAP aperture comparison, not PRF localization."""
    import lightkurve as lk
    from .pipeline import checkpoint
    selected_sector = sector if sector is not None else investigation['provenance'][0]['sector']
    if selected_sector not in {p['sector'] for p in investigation['provenance']}:
        raise ValueError('Pixel sector must belong to this investigation')
    if investigation["config"]["mission"] != "TESS":
        raise ValueError("Pixel diagnostics currently support TESS SPOC only")
    checkpoint(job_id, "Searching for target pixel files")
    found = lk.search_targetpixelfile(investigation["target"], mission="TESS", sector=selected_sector, author="SPOC", exptime=cadence_seconds)
    if not len(found):
        return {"status": "unavailable", "reason": f"No {cadence_seconds}-second SPOC target pixel file in sector {selected_sector}"}
    row = archive.rows(found.table)[0]
    product = {"uri": row["dataURI"], "filename": row["productFilename"], "sector": selected_sector, "size":row.get('size'), "kind": "target_pixel_file"}
    path, provenance = archive.download(product, 300*1024**2, lambda m: checkpoint(job_id, m))
    quality = investigation['config'].get('quality', 'default')
    tpf = lk.read(path, quality_bitmask=0x7FFFFFFF if quality == 'strict' else 'default')
    t = np.asarray(tpf.time.jd)
    flux = np.asarray(tpf.flux.value)
    d, period, epoch = signal["duration_hours"]/24, signal["period"], signal["epoch_bjd"]
    ph = science.phase(t, period, epoch)
    inside = np.abs(ph) < d/2
    outside = (np.abs(ph) > d) & (np.abs(ph) < 2*d)
    if inside.sum() < 5 or outside.sum() < 10:
        return {"status": "insufficient_data", "provenance": provenance}
    mean = np.nanmean(flux[outside], axis=0)
    difference = mean - np.nanmean(flux[inside], axis=0)
    aperture = np.asarray(tpf.pipeline_mask)
    eroded = binary_erosion(aperture)
    if eroded.sum() == 0:
        eroded = np.zeros_like(aperture)
        finite_mean = np.where(aperture & np.isfinite(mean), mean, -np.inf)
        eroded[np.unravel_index(np.argmax(finite_mean), mean.shape)] = True
    variants = []
    for name, mask in [("SPOC aperture", aperture), ("Inner aperture", eroded)]:
        lc = tpf.to_lightcurve(aperture_mask=mask).remove_nans()
        tt, ff, ee = np.asarray(lc.time.jd), np.asarray(lc.flux.value), np.asarray(lc.flux_err.value)
        y, err, _, _ = science.preprocess(tt, ff, ee, np.zeros(len(tt)), "median", max(1.5, d*6))
        variants.append({"aperture": name, "pixels": int(mask.sum()), **science.depth_measure(y, err, np.abs(science.phase(tt, period, epoch)) < d/2)})
    return science.clean_json({"status": "measured", "sector": selected_sector, "mean_image": mean,
            'quality': quality, 'cadence_seconds': cadence_seconds,
            "difference_image": difference, "aperture": aperture.astype(int), "aperture_tests": variants,
            "in_cadences": int(inside.sum()), "out_cadences": int(outside.sum()), "provenance": provenance,
            "caveat": "Exploratory local-baseline mean difference image; no pointing decorrelation, PRF fit, centroid uncertainty or WCS source attribution. Aperture dependence can reflect dilution and systematics. This test cannot establish the host star."})
