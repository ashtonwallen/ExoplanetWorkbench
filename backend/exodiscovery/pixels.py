import numpy as np
from scipy.ndimage import binary_erosion

from . import archive, science


def inspect_pixels(investigation, signal, job_id=None):
    """Exploratory difference image and SAP aperture comparison, not PRF localization."""
    import lightkurve as lk
    from .pipeline import checkpoint
    p = investigation["provenance"][0]
    if investigation["config"]["mission"] != "TESS":
        raise ValueError("Pixel diagnostics currently support TESS SPOC only")
    checkpoint(job_id, "Searching for target pixel files")
    found = lk.search_targetpixelfile(investigation["target"], mission="TESS", sector=p["sector"], author="SPOC", exptime=120)
    if not len(found):
        return {"status": "unavailable", "reason": "No 120-second SPOC target pixel file in the first selected sector"}
    row = archive.rows(found.table)[0]
    product = {"uri": row["dataURI"], "filename": row["productFilename"], "sector": p["sector"], "size":row.get('size'), "kind": "target_pixel_file"}
    path, provenance = archive.download(product, 300*1024**2, lambda m: checkpoint(job_id, m))
    tpf = lk.read(path, quality_bitmask="default")
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
    return science.clean_json({"status": "measured", "sector": p["sector"], "mean_image": mean,
            "difference_image": difference, "aperture": aperture.astype(int), "aperture_tests": variants,
            "in_cadences": int(inside.sum()), "out_cadences": int(outside.sum()), "provenance": provenance,
            "caveat": "Exploratory local-baseline mean difference image; no pointing decorrelation, PRF fit, centroid uncertainty or WCS source attribution. Aperture dependence can reflect dilution and systematics. This test cannot establish the host star."})
