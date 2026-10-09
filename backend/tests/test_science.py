import numpy as np
import pytest
from exodiscovery import science, archive
from exodiscovery.schemas import Analysis


def synthetic(period=3.18, depth=.003, seed=21):
    t = np.arange(0, 26, .00694) + 2457000
    rng = np.random.default_rng(seed)
    inside = np.abs(science.phase(t, period, 2457000.8)) < .065
    y = 1 + .003*np.sin(2*np.pi*(t-t[0])/7) + rng.normal(0,.0003,len(t))
    y[inside] -= depth
    return t,y,np.full(len(t),.0003),inside


def test_injected_transit_survives_preprocessing_and_blind_search():
    t,f,e,inside = synthetic()
    cfg = Analysis(target="synthetic", min_period=1, max_period=6)
    y,er,_,_ = science.preprocess(t,f,e,np.zeros(len(t)),window_days=1.5)
    s,_ = science.search_bls(t,y,er,cfg)
    assert abs(s['period']/3.18-1) < .002
    assert .0024 < s['depth'] < .0035
    assert s['depth_snr'] > 15
    assert np.all(er>0)


@pytest.mark.parametrize('method',['median','savgol','none'])
def test_transit_mask_preserves_flux_and_depth(method):
    t,f,e,inside=synthetic()
    f_before=f.copy()
    y,er,_,_=science.preprocess(t,f,e,np.zeros(len(t)),method,1.5,inside)
    np.testing.assert_array_equal(f,f_before)
    d=science.depth_measure(y,er,inside)
    assert .0023 < d['depth_ppm']/1e6 < .0036


def test_no_transit_manufactured_in_constant_data_or_across_sector_jump():
    t=np.r_[np.arange(0,12,.01),np.arange(200,212,.01)]
    f=np.r_[np.ones(1200)*10,np.ones(1200)*70]
    sectors=np.r_[np.zeros(1200),np.ones(1200)]
    y,_,_,_=science.preprocess(t,f,np.ones(len(t))*.01,sectors)
    np.testing.assert_allclose(y,1,atol=1e-12)


def test_eclipsing_binary_fails_odd_even_and_secondary_guards():
    t=np.arange(0,25,.005)
    e=np.full(len(t),.0002)
    y=np.ones(len(t))
    phase=science.phase(t,2,.5)
    inside=np.abs(phase)<.05
    cycles=np.rint((t-.5)/2).astype(int)
    y[inside & (cycles%2==0)]-=.01
    y[inside & (cycles%2==1)]-=.004
    y[np.abs(science.phase(t,2,1.5))<.05]-=.002
    s={'period':2,'epoch_bjd':.5,'duration_hours':2.4}
    diag=science.diagnostics(t,y,e,np.zeros(len(t)),s)
    assert diag['odd_even_sigma']>10
    assert diag['secondary']['snr']>10
    signal={**s,'catalog_matches':[],'validation':diag,'depth_snr':30,'grid_capped':False}
    assert science.classify(signal,{})=='Likely false positive'


def test_missing_tests_and_catalog_failure_never_become_passes():
    s={'period':5,'epoch_bjd':.5,'duration_hours':1}
    t=np.array([.49,.5,.51])
    d=science.diagnostics(t,np.ones(3)*.99,np.ones(3)*.001,np.zeros(3),s)
    assert d['odd_even_sigma'] is None
    assert d['observed_transits']==0
    s.update(validation=d,catalog_matches=[],depth_snr=40,grid_capped=False)
    assert science.classify(s,{})=='Insufficient evidence'


def test_catalog_match_requires_host_and_period():
    rows=[{'pl_name':'test b','pl_orbper':3.18,'ra':20.,'dec':10.,'tic_id':'TIC 123'}]
    cat={'records':{'confirmed':{'rows':rows}}}
    assert science.crossmatch({'period':3.18},cat,20.,10.,123)
    assert not science.crossmatch({'period':2.},cat,20.,10.,123)
    assert not science.crossmatch({'period':3.18},cat,20.1,10.1,999)


def test_one_cadence_per_sector():
    p=[{'uri':'mast:a','sector':2,'exptime':20},{'uri':'mast:b','sector':2,'exptime':120},{'uri':'mast:c','sector':3,'exptime':120}]
    assert [r['uri'] for r in archive.select_products({'products':p},[],5)]==['mast:b','mast:c']


def test_batman_fit_is_computed_and_conditional():
    t,f,e,inside=synthetic()
    y,er,_,_=science.preprocess(t,f,e,np.zeros(len(t)),mask=inside)
    result=science.fit_transit(t,y,er,{'period':3.18,'epoch_bjd':2457000.8,'duration_hours':3.12,'depth':.003})
    assert result['status']=='fitted'
    assert .035<result['rp_over_rstar']<.09
    assert len(result['flux'])==400
    assert 'conditional' in result['assumptions']


def test_invalid_ranges_rejected():
    with pytest.raises(ValueError):
        Analysis(target='test',min_period=3,max_period=2)
    with pytest.raises(ValueError):
        Analysis(target='test',window_days=.25,max_duration=5)
