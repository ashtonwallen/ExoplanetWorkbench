"""Bounded, catalog-grounded target selection; these are observing proxies, not planet odds."""
from collections import defaultdict
import json

from . import archive, store
from .schemas import TargetDiscovery


def query_region(ra, dec, cfg):
    from astroquery.mast import Catalogs, Observations
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    Catalogs.TIMEOUT = Observations.TIMEOUT = 60
    position = SkyCoord(ra, dec, unit='deg')
    stars = Catalogs.query_criteria(catalog='Tic', coordinates=position, radius=cfg.radius_degrees*u.deg,
                                  Tmag=[6, cfg.max_tmag], rad=[.15, cfg.max_radius_solar],
                                  Teff=[2500, 6500], objType='STAR')
    fields = ['ID','ra','dec','Tmag','rad','e_rad','Teff','logg','contratio']
    stars = archive.rows(stars[[f for f in fields if f in stars.colnames]])
    # Match catalog IDs directly. A sky-footprint observation query also intersects
    # large camera footprints and can become an effectively all-sky archive query.
    observations = []
    for start in range(0, len(stars), 100):
        ids = [str(s['ID']) for s in stars[start:start+100]]
        observations.extend(archive.rows(Observations.query_criteria(
            obs_collection='TESS', provenance_name='SPOC', target_name=ids)))
    return stars, observations


def rank_targets(stars, observations, cfg):
    coverage = defaultdict(set)
    for obs in observations:
        name = str(obs.get('target_name','')).upper().replace('TIC','').strip()
        sector = obs.get('sequence_number')
        if name.isdigit() and sector is not None:
            coverage[name].add(int(sector))
    result = []
    for star in stars:
        tid = str(star.get('ID',''))
        sectors = sorted(coverage.get(tid, set()))
        if len(sectors) < cfg.min_sectors:
            continue
        # Reapply filters locally as a guard against missing/unexpected catalog values.
        if any(star.get(k) is None for k in ['Tmag','rad','Teff','ra','dec']):
            continue
        if not (6 <= star['Tmag'] <= cfg.max_tmag and .15 <= star['rad'] <= cfg.max_radius_solar and 2500 <= star['Teff'] <= 6500):
            continue
        result.append({'target':f'TIC {tid}', 'tic_id':tid, 'ra':star['ra'], 'dec':star['dec'],
                       'tmag':star['Tmag'], 'radius_solar':star['rad'], 'teff_k':star['Teff'],
                       'contamination_ratio':star.get('contratio'), 'available_sectors':sectors,
                       'sector_count':len(sectors), 'catalog_status':'Not yet cross-referenced for known planets or TOIs',
                       'selection_evidence':f"{len(sectors)} SPOC observing sectors; Tmag {star['Tmag']:.2f}; catalog radius {star['rad']:.3f} solar."})
    if cfg.rank_by == 'coverage':
        result.sort(key=lambda r:(-r['sector_count'],r['tmag'],r['radius_solar'],r['tic_id']))
    elif cfg.rank_by == 'small_stars':
        result.sort(key=lambda r:(r['radius_solar'],-r['sector_count'],r['tmag'],r['tic_id']))
    else:
        result.sort(key=lambda r:(r['tmag'],-r['sector_count'],r['radius_solar'],r['tic_id']))
    return result


def discover(cfg: TargetDiscovery, notify=lambda _:None):
    key = 'target-discovery:v2:'+json.dumps(cfg.model_dump(),sort_keys=True)
    def fetch():
        regions = {'north_pole':('North ecliptic pole',270.,66.5607),
                   'south_pole':('South ecliptic pole',90.,-66.5607)}
        chosen = list(regions.values()) if cfg.region=='compare_poles' else [regions[cfg.region]] if cfg.region!='custom' else [('Custom ICRS cone',cfg.ra,cfg.dec)]
        results, targets = [], []
        for label,ra,dec in chosen:
            notify(f'Comparing TESS coverage and TIC stars: {label}')
            item = {'name':label,'ra':ra,'dec':dec,'radius_degrees':cfg.radius_degrees}
            try:
                stars, observations = query_region(ra,dec,cfg)
                ranked = rank_targets(stars,observations,cfg)
                item.update(status='complete', filtered_catalog_stars=len(stars), observation_rows=len(observations),
                            eligible_observed_stars=len(ranked), returned_stars=min(cfg.limit,len(ranked)))
                for target in ranked[:cfg.limit]:
                    targets.append({**target,'region':label})
            except Exception as exc:
                item.update(status='unavailable',error=type(exc).__name__,eligible_observed_stars=None)
            results.append(item)
        # Each region supplies a bounded shortlist; never present this as an all-sky optimum.
        return {'queried_at':store.now(),'query':cfg.model_dump(),'regions':results,'targets':targets,
                'catalog_filters':{'catalog':'TIC','objType':'STAR','Tmag':[6,cfg.max_tmag],
                                   'radius_solar':[.15,cfg.max_radius_solar],'Teff_K':[2500,6500]},
                'observation_filters':{'obs_collection':'TESS','provenance_name':'SPOC',
                                       'target_name':'Exact returned TIC IDs','sectors':'Unique sequence_number values'},
                'ranking':cfg.rank_by, 'sources':['https://mast.stsci.edu','https://tess.mit.edu/mission-overview/'],
                'limitations':['Compared only the listed bounded cones, not the whole sky.',
                    'Sector count, brightness and radius are observing/sensitivity proxies, not probabilities of planets.',
                    'SPOC target selection is biased; metadata coverage does not guarantee usable photometry.',
                    'Known planets and TOIs are checked by subsequent analysis; targets are not yet established as uncataloged.',
                    'Catalog radii, contamination and stellar variability need verification.']}
    old = store.cache_get(key)
    if old:
        from datetime import datetime, timezone
        if all(r['status']=='complete' for r in old['payload']['regions']) and (datetime.now(timezone.utc)-datetime.fromisoformat(old['created'])).total_seconds()<86400:
            return old['payload']
    result = fetch()
    store.cache_put(key,result)
    return result
