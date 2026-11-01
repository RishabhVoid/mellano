"""Tiny isolated raster fixtures for satellite overlap, never demo data."""
from pathlib import Path
import tempfile
import unittest

import numpy as np
import rasterio
from rasterio.transform import from_origin
from sim.validation import water_overlap


class WaterOverlapTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.mask=Path(self.temp.name).resolve()/'classified.tif'
        self.profile={'driver':'GTiff','width':2,'height':2,'count':1,'dtype':'uint8','crs':'EPSG:32643','transform':from_origin(700000,3170000,10,10),'nodata':255}

    def save(self,values,**updates):
        with rasterio.open(self.mask,'w',**{**self.profile,**updates}) as output:
            output.write(np.array(values,'uint8'),1);output.update_tags(source_product='isolated unit-test fixture')

    def test_nodata_excluded_instead_of_becoming_water(self):
        self.save([[1,0],[255,1]])
        result=water_overlap(np.array([[1,1],[1,0.]],'float32'),np.ones((2,2),bool),self.profile,self.mask)
        self.assertAlmostEqual(result['iou_vs_sentinel_peak'],1/3)
        self.assertEqual(result['sentinel_comparison']['evaluated_cells'],3)
        self.assertIn('not independent',result['sentinel_comparison']['caveat'])

    def test_missing_nonoverlapping_empty_and_nonwater_are_unavailable(self):
        depth=np.zeros((2,2),'float32');valid=np.ones((2,2),bool)
        self.assertIsNone(water_overlap(depth,valid,self.profile,self.mask)['iou_vs_sentinel_peak'])
        self.save([[0,0],[0,0]])
        self.assertFalse(water_overlap(depth,valid,self.profile,self.mask)['sentinel_comparison']['available'])
        self.save([[1,1],[1,1]],transform=from_origin(800000,3270000,10,10))
        result=water_overlap(depth,valid,self.profile,self.mask)
        self.assertIsNone(result['iou_vs_sentinel_peak']);self.assertEqual(result['sentinel_comparison']['evaluated_cells'],0)
        self.assertIsNone(water_overlap(None,valid,self.profile,self.mask)['iou_vs_sentinel_peak'])

    def test_partial_overlap_and_valid_aoi(self):
        self.save([[1,1],[0,0]],transform=from_origin(700010,3170000,10,10))
        result=water_overlap(np.ones((2,2),'float32'),np.array([[True,True],[True,False]]),self.profile,self.mask)
        self.assertEqual(result['iou_vs_sentinel_peak'],1);self.assertEqual(result['sentinel_comparison']['evaluated_cells'],1)
