import os
import pytest
from exodiscovery import pipeline, archive, store
from exodiscovery.schemas import Analysis


@pytest.mark.network
@pytest.mark.skipif(os.environ.get('EXO_NETWORK_TESTS')!='1',reason='Set EXO_NETWORK_TESTS=1 to access public MAST/NASA services')
def test_recover_wasp18_from_real_tess():
    store.init()
    result=pipeline.analyze(Analysis(target='WASP-18',sectors=[2],max_products=1,max_signals=1))
    first=result['signals'][0]
    assert abs(first['period']/.94145223-1)<.001
    assert first['classification']=='Known planet'
    assert any(m['record'].get('pl_name')=='WASP-18 b' for m in first['catalog_matches'])
    assert result['cadences']>10000
    assert result['provenance'][0]['sha256']
