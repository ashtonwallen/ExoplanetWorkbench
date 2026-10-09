import numpy as np
from astropy.io import fits
from exodiscovery.archive import read_lightcurve


def test_fits_quality_mask_and_barycentric_time_reference(tmp_path):
    cols = [fits.Column(name='TIME',format='D',array=[1,2,3,4,5]),
            fits.Column(name='PDCSAP_FLUX',format='D',array=[100.,100.,100.,np.nan,100.]),
            fits.Column(name='PDCSAP_FLUX_ERR',format='D',array=[1.,1.,1.,1.,-1.]),
            fits.Column(name='QUALITY',format='J',array=[0,1,0,0,0])]
    primary=fits.PrimaryHDU()
    primary.header['TELESCOP']='TESS'
    primary.header['TICID']=123
    primary.header['SECTOR']=2
    table=fits.BinTableHDU.from_columns(cols)
    table.header['BJDREFI']=2457000
    table.header['TIMESYS']='TDB'
    path=tmp_path/'test.fits'
    fits.HDUList([primary,table]).writeto(path)
    t,f,e,s,meta=read_lightcurve(path,'strict')
    np.testing.assert_array_equal(t,[2457001,2457003])
    np.testing.assert_array_equal(s,[2,2])
    assert meta['cadences_total']==5
    assert meta['cadences_kept']==2


def test_quality_reader_rejects_unexpected_time_system(tmp_path):
    import pytest
    cols=[fits.Column(name=k,format='D',array=[1.,2.,3.]) for k in ['TIME','PDCSAP_FLUX','PDCSAP_FLUX_ERR']]
    cols.append(fits.Column(name='QUALITY',format='J',array=[0,0,0]))
    h=fits.PrimaryHDU()
    h.header['TELESCOP']='TESS'
    table=fits.BinTableHDU.from_columns(cols)
    table.header['TIMESYS']='UTC'
    path=tmp_path/'wrong-time.fits'
    fits.HDUList([h,table]).writeto(path)
    with pytest.raises(ValueError,match='time system'):
        read_lightcurve(path)
