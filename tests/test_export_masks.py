"""Optional real-mask export contract, verified with isolated raster fixtures."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from sim import export_web
from sim.config import MASK_NAMES


class MaskExportTests(unittest.TestCase):
    def test_recession_uses_its_own_date_and_excludes_missing_pixels(self):
        self.assertIn('s1_water_recession',MASK_NAMES)
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);web=root/'web';web.mkdir()
            path=root/'s1_water_recession.tif'
            profile=dict(driver='GTiff',width=2,height=2,count=1,dtype='float32',crs='EPSG:32643',transform=from_origin(500000,3100000,10,10),nodata=float('nan'))
            with rasterio.open(path,'w',**profile) as dst:dst.write(np.array([[1,np.nan],[0,0]],dtype='float32'),1)
            dates={'before_dates':['2023-07-04'],'peak_dates':['2023-07-16'],'recession_dates':['2023-07-24']}
            with patch.object(export_web,'DERIVED',root),patch.object(export_web,'WEB_DATA',web),rasterio.open(path) as ref:
                result=export_web.export_mask('s1_water_recession',ref,dates)
            payload=json.loads((web/'s1_water_recession.json').read_text())
            self.assertTrue(result['available'])
            self.assertEqual(payload['date'],'2023-07-24')
            self.assertEqual(payload['wet_cells'],1)
            self.assertEqual(len(payload['geometries']),1)

    def test_absent_recession_stays_unavailable_without_creating_data(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(export_web,'DERIVED',Path(temporary)):
            result=export_web.export_mask('s1_water_recession',None,{})
            self.assertFalse(result['available'])
            self.assertEqual(list(Path(temporary).iterdir()),[])


if __name__=='__main__':unittest.main()
