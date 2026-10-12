"""Regression cases for the failure modes observed in the overnight survey."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import copy

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from mcp.shared.memory import create_connected_server_and_client_session

from exodiscovery import archive, discovery, execute, pipeline, science, store
from exodiscovery.api import app
from exodiscovery.evidence import compact_evidence, signal_groups
from exodiscovery.mcp_server import build_server
from exodiscovery.schemas import Analysis, TargetDiscovery
from exodiscovery.worker import remote_stage_timeout


def complete_catalog():
    return {'records': {k: {'status': 'complete', 'rows': []} for k in ('confirmed', 'toi')}}


@pytest.fixture
def saved_signal(isolated_store):
    ident = 'a'*16
    t = 2457000 + np.arange(0, 15, .01)
    signal = {'index': 0, 'period': .65, 'epoch_bjd': 2457000.2, 'duration_hours': 2.4,
              'depth': .001, 'depth_ppm': 1000., 'depth_error_ppm': 20., 'depth_snr': 50.,
              'grid_capped': False, 'catalog_matches': [], 'classification': 'Interesting unclassified transit signal',
              'fit': {'status': 'fitted'}, 'uncertainties': 'Synthetic independent errors', 'radius_assumption': 'Test fixture'}
    f = np.ones(len(t))
    f[np.abs(science.phase(t, .65, signal['epoch_bjd'])) < .05] -= .001
    error = np.full(len(t), .0001)
    arrays = dict(time=t, flux=f, flux_error=error, processed=f, error=error, sector=np.ones(len(t)),
                  normalized=f, trend=np.ones(len(t)))
    signal['validation'] = science.diagnostics(t, f, error, arrays['sector'], signal)
    record = {'id': ident, 'target': 'TIC 123', 'star': {'tic_id': 123, 'ra': 20., 'dec': 10.},
              'created': store.now(), 'config': Analysis(target='TIC 123', max_duration=4, max_period=5).model_dump(),
              'signals': [signal], 'catalogs': complete_catalog(), 'history': [], 'limitations': [],
              'cadences': len(t), 'baseline_days': float(np.ptp(t)), 'provenance': [{'sector': 1}]}
    store.save_investigation(record, ident)
    folder = isolated_store/'artifacts'/ident
    folder.mkdir()
    np.savez_compressed(folder/'lightcurve.npz', **arrays)
    pipeline.atomic_json(folder/'result.json', record)
    return record, arrays


def test_search_masks_are_not_acquisition_gaps():
    t = np.arange(0, 10, .01)
    sector = np.ones(len(t))
    keep = ~((t > 4) & (t < 5))
    correct = science.acquisition_edges(t, sector, 1.5)[keep]
    incorrect = science.acquisition_edges(t[keep], sector[keep], 1.5)
    middle = (t[keep] > 3.5) & (t[keep] < 5.5)
    assert not correct[middle].any()
    assert incorrect[middle].any()
    actual_gap = science.acquisition_edges(t[keep], sector[keep], 1.5)
    assert actual_gap[middle].any()


def test_coverage_is_not_a_detection_and_depth_inconsistency_is_exposed():
    t = np.arange(0, 20, .01)
    sectors = np.where(t < 10, 1, 2)
    signal = {'period': 2., 'epoch_bjd': .5, 'duration_hours': 2.4}
    y = np.ones(len(t))
    e = np.full(len(t), .0002)
    diag = science.diagnostics(t, y, e, sectors, signal)
    assert diag['covered_transit_windows'] >= 3
    assert 'not individually detected dips' in diag['event_count_semantics']
    assert all(abs(m['depth_ppm']) < 1e-6 for m in diag['events'] if m['status'] == 'measured')
    y[(sectors == 2) & (np.abs(science.phase(t, 2., .5)) < .05)] -= .005
    diag = science.diagnostics(t, y, e, sectors, signal)
    assert next(v for v in diag['tests'] if v['name'] == 'Sector depth consistency')['status'] == 'flag'
    sample = [{'status': 'measured', 'depth_ppm': d, 'error_ppm': 10.} for d in (1000., 0., 0.)]
    assert science.consistency(sample)['minimum_leave_one_out_snr'] == 0


def test_injection_preserves_duration_and_accounts_for_observability(saved_signal, monkeypatch):
    record, _ = saved_signal
    searches = []
    def search(t, y, e, config):
        searches.append(len(t))
        return {'period': .65, 'epoch_bjd': 2457000.4, 'depth_snr': 10.}, None
    monkeypatch.setattr(science, 'search_bls', search)
    class FixedRng:
        def uniform(self, *_):
            return .4
    monkeypatch.setattr(np.random, 'default_rng', lambda _: FixedRng())
    params = {'investigation_id': record['id'], 'period': .65, 'depth_ppm': 1000., 'trials': 2}
    job = store.create_job('injection', params)
    result = pipeline.followup(params, 'injection', job['id'])['result']
    assert result['duration_hours'] == pytest.approx(2.4)
    assert result['observable_trials'] == 2 and len(searches) == 2
    assert all(t['injected_cadences'] > 0 and t['covered_transit_windows'] >= 3 for t in result['trials'])
    # Re-executing a completed follow-up checkpoint must not repeat the experiment.
    pipeline.followup(params, 'injection', job['id'])
    assert len(searches) == 2
    class MaskedRng:
        def uniform(self, *_):
            return .2
    monkeypatch.setattr(np.random, 'default_rng', lambda _: MaskedRng())
    result = pipeline.followup(params, 'injection', store.create_job('injection', params)['id'])['result']
    assert result['unobservable_trials'] == 2
    assert result['observable_fraction'] is None
    assert all(t['injected_cadences'] == 0 for t in result['trials'])
    assert len(searches) == 2
    with pytest.raises(ValueError, match='duration'):
        pipeline.followup({**params, 'duration_hours': 5}, 'injection', store.uid())


def test_holdout_freezes_ephemeris_and_rejects_overlap(saved_signal, isolated_store, monkeypatch):
    record, arrays = saved_signal
    other = copy.deepcopy(record)
    other['id'] = 'b'*16
    store.save_investigation(other, other['id'])
    with pytest.raises(ValueError, match='disjoint'):
        pipeline.heldout_measurement(record, record['signals'][0], other['id'])
    other['provenance'] = [{'sector': 2}]
    store.save_investigation(other, other['id'])
    path = isolated_store/'artifacts'/other['id']
    path.mkdir()
    t = arrays['time'] + 65 # exactly 100 original periods
    np.savez_compressed(path/'lightcurve.npz', **{**arrays, 'time': t, 'sector': np.full(len(t), 2)})
    monkeypatch.setattr(science, 'search_bls', lambda *_: pytest.fail('Holdout must not search/refit'))
    measured = pipeline.heldout_measurement(record, record['signals'][0], other['id'])
    assert measured['frozen_ephemeris']['epoch_bjd'] == record['signals'][0]['epoch_bjd']
    assert measured['measurement']['depth_ppm'] > 800
    assert measured['status'] == 'measured'
    other['star']['tic_id'] = 999
    store.save_investigation(other, other['id'])
    with pytest.raises(ValueError, match='same target'):
        pipeline.heldout_measurement(record, record['signals'][0], other['id'])


def test_product_manifest_explains_missing_and_limited_sectors():
    found = {'products': [{'uri': 'mast:'+str(s), 'sector': s, 'exptime': 120} for s in (1, 2)]}
    result = archive.product_manifest(found, [1, 2, 3], 1)
    assert result['selected_sectors'] == [1]
    assert result['missing_sectors'] == [{'sector': 2, 'reason': 'product_limit'}, {'sector': 3, 'reason': 'no_matching_lightcurve'}]


def test_catalog_refresh_does_not_turn_failure_into_novelty(saved_signal, monkeypatch):
    record, _ = saved_signal
    bad = {'records': {'confirmed': {'status': 'unavailable', 'rows': []}, 'toi': {'status': 'complete', 'rows': []}}}
    calls = []
    def catalogs(*args, **kwargs):
        calls.append(kwargs)
        return bad
    monkeypatch.setattr(archive, 'catalogs', catalogs)
    params = {'investigation_id': record['id']}
    pipeline.followup(params, 'catalog', store.create_job('catalog', params)['id'])
    updated = store.investigation(record['id'])
    assert updated['signals'][0]['classification'] == 'Insufficient evidence'
    assert len(updated['catalog_history']) == 1
    assert calls == [{'force_refresh': True}]
    assert archive.catalog_host_status(bad, 20., 10., 123)['status'] == 'unavailable'


def test_gaia_orders_before_limit_and_discloses_truncation(monkeypatch):
    captured = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'metadata': [{'name': n} for n in ('source_id', 'ra', 'dec')],
                    'data': [[i, .00001*i, 0.] for i in range(101)]}
    def get(url, **kw):
        captured.append(kw['params']['QUERY'])
        return Response()
    monkeypatch.setattr(archive.requests, 'get', get)
    result = archive.gaia(359.99999, 0.)
    assert 'TOP 101' in captured[0] and 'ORDER BY DISTANCE' in captured[0]
    assert result['truncated'] and len(result['rows']) == 100
    assert result['rows'][0]['separation_arcsec'] < .1 # RA wrap is spherical


def test_queue_idempotency_priority_pause_and_resume_order(isolated_store):
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: store.create_job('search', {'target': 'x'}, idempotency_key='same'), range(4)))
    assert len({j['id'] for j in jobs}) == 1
    with pytest.raises(ValueError, match='different'):
        store.create_job('search', {'target': 'y'}, idempotency_key='same')
    first = jobs[0]['id']
    second = store.create_job('search', {'target': 'second'})['id']
    store.control_job(first, 'pause')
    store.control_job(first, 'resume')
    assert store.claim_job()['id'] == second
    with pytest.raises(ValueError):
        store.control_job(second, 'pause')
    third = store.create_job('search', {'target': 'third'})['id']
    store.control_job(third, 'prioritize')
    assert store.claim_job()['id'] == third
    assert store.claim_job()['id'] == first
    assert remote_stage_timeout('Searching TESS: test', 181)
    assert not remote_stage_timeout('BLS search 1/3', 181)


def test_mcp_retry_keys_compact_evidence_and_event_pagination(saved_signal):
    record, _ = saved_signal
    class LostResponse(httpx.ASGITransport):
        lost = False
        async def handle_async_request(self, request):
            response = await super().handle_async_request(request)
            if request.method == 'POST' and not self.lost:
                self.lost = True
                raise httpx.ReadTimeout('Simulated response loss AFTER durable submission')
            return response
    async def check():
        async with create_connected_server_and_client_session(build_server(max_jobs=1, transport=LostResponse(app=app))) as client:
            params = {'config': {'target': 'TIC 123'}, 'idempotency_key': 'lost-response'}
            lost = await client.call_tool('search_observations', params)
            assert lost.isError and len(store.jobs()) == 1
            first_id = store.jobs()[0]['id']
            again = (await client.call_tool('search_observations', params)).structuredContent
            assert first_id == again['job_id']
            assert len(store.jobs()) == 1
            status = (await client.call_tool('workbench_status', {})).structuredContent
            assert status['submissions_remaining'] == 0
            short = (await client.call_tool('read_investigation', {'investigation_id': record['id']})).structuredContent
            full = (await client.call_tool('read_investigation', {'investigation_id': record['id'], 'detail': 'full'})).structuredContent
            assert short['omitted_or_truncated']['signals[0].validation.events']['count'] > 20
            assert len(short['signals'][0]['validation']['events']) == 20
            assert len(full['signals'][0]['validation']['events']) > 20
            page = (await client.call_tool('read_signal_events', {'investigation_id': record['id'], 'offset': 2, 'limit': 3})).structuredContent
            assert page['events'] == full['signals'][0]['validation']['events'][2:5]
    asyncio.run(check())


def test_compact_view_never_changes_original_and_groups_do_not_merge(saved_signal):
    record, _ = saved_signal
    original = copy.deepcopy(record)
    compact_evidence(record)
    assert record == original
    repeated = copy.deepcopy(record)
    repeated['id'] = 'b'*16
    repeated['signals'][0]['period'] *= 1.001
    grouped = signal_groups([record, repeated])
    assert grouped['group_count'] == 1 and len(grouped['groups'][0]['members']) == 2
    repeated['signals'][0]['period'] *= 2
    assert signal_groups([record, repeated])['group_count'] == 2


def test_discovery_excludes_known_and_unavailable_hosts(isolated_store, monkeypatch):
    stars = [{'ID': str(i), 'ra': 20., 'dec': 10., 'Tmag': 10., 'rad': .3, 'Teff': 3500} for i in (1, 2, 3)]
    observations = [{'target_name': str(i), 'sequence_number': s} for i in (1, 2, 3) for s in (1, 2)]
    monkeypatch.setattr(discovery, 'query_region', lambda *_: (stars, observations))
    def catalogs(ra, dec, tid):
        cat = complete_catalog()
        if tid == '1':
            cat['records']['confirmed']['rows'] = [{'tic_id': 'TIC 1', 'ra': 20., 'dec': 10.}]
        if tid == '2':
            cat['records']['toi']['status'] = 'unavailable'
        return cat
    monkeypatch.setattr(archive, 'catalogs', catalogs)
    result = discovery.discover(TargetDiscovery(region='south_pole', limit=1, exclude_known_hosts=True))
    assert [t['tic_id'] for t in result['targets']] == ['3']
    assert len(result['regions'][0]['excluded_known_hosts']) == 1
    assert len(result['regions'][0]['catalog_unavailable']) == 1


def test_preflight_and_strict_missing_sector_policy(isolated_store, monkeypatch):
    found = {'products': [{'uri': 'mast:a', 'sector': 1, 'exptime': 120}]}
    monkeypatch.setattr(archive, 'search', lambda *_: found)
    cfg = Analysis(target='test', sectors=[1, 2])
    with pytest.raises(ValueError, match='Requested sectors'):
        pipeline.analyze(cfg)
    with TestClient(app) as client:
        job = client.post('/api/preflight', json=cfg.model_dump()).json()
        execute.run(job['id'])
        completed = store.get_job(job['id'])
        assert completed['status'] == 'completed'
        assert completed['result']['missing_sectors'][0]['sector'] == 2


def test_pixels_select_sector_cadence_and_strict_quality(saved_signal, monkeypatch):
    import lightkurve as lk
    from types import SimpleNamespace
    from exodiscovery import pixels
    from astropy.table import Table
    record, _ = saved_signal
    record['config']['quality'] = 'strict'
    record['provenance'].append({'sector': 2})
    captured = {}
    class Found:
        table = Table({'dataURI': ['mast:tpf'], 'productFilename': ['test.fits'], 'size': [100]})
        def __len__(self): return 1
    def search(*args, **kwargs):
        captured.update(kwargs)
        return Found()
    def read(path, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(time=SimpleNamespace(jd=np.array([2457000.])),
                               flux=SimpleNamespace(value=np.zeros((1, 2, 2))))
    monkeypatch.setattr(lk, 'search_targetpixelfile', search)
    monkeypatch.setattr(lk, 'read', read)
    monkeypatch.setattr(archive, 'download', lambda *args: ('unused', {'sector': 2}))
    result = pixels.inspect_pixels(record, record['signals'][0], sector=2, cadence_seconds=20)
    assert captured['sector'] == 2 and captured['exptime'] == 20
    assert captured['quality_bitmask'] == 0x7FFFFFFF
    assert result['status'] == 'insufficient_data'
    with pytest.raises(ValueError, match='belong'):
        pixels.inspect_pixels(record, record['signals'][0], sector=99)


def test_report_includes_full_range_zoom_and_pixel_maps(saved_signal, monkeypatch):
    from exodiscovery import reports
    record, arrays = saved_signal
    record['provenance'][0].update(url='https://example.test/fixture', filename='synthetic.fits', sha256='test')
    record['signals'][0]['followups'] = [{'kind': 'pixels', 'result': {'status': 'measured',
        'mean_image': [[10, 20], [30, 40]], 'difference_image': [[1, -1], [2, -2]],
        'aperture': [[0, 1], [1, 1]]}}]
    store.save_investigation(record, record['id'])
    monkeypatch.setattr(pipeline, 'plots', lambda *_: {'time': arrays['time'].tolist(), 'flux': arrays['flux'].tolist(),
        'period': [1, 2, 3], 'power': [0, 2, 0], 'phase': science.phase(arrays['time'], .65, 2457000.2).tolist(),
        'fold_flux': arrays['flux'].tolist(), 'binned': {'phase': [0.], 'flux': [.999], 'error': [.0001]}})
    html = reports.report(record['id'])
    assert 'full range shown above' in html
    assert 'Pixel maps with orange SPOC aperture outline' in html
    assert html.count('data:image/png;base64,') == 3
