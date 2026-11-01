import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch
import numpy as np
from pyproj import Transformer
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import Polygon,MultiPolygon,LineString,mapping
from sim import relative,dynamic
from sim.relative import river_reference,RelativePipes,cache_complete
from sim.dynamic import DynamicConfig,_exposure
from sim.geometry import transform_xy

class RelativeTests(unittest.TestCase):
    def test_reference_from_mapped_pixels(self):
        ground=np.repeat(np.arange(8,dtype=float)[:,None],9,axis=1)+200
        water=np.zeros_like(ground,dtype=bool);water[:,3:6]=True
        river,ref,meta=river_reference(ground,water,np.ones_like(water),cell_size=250)
        self.assertTrue(np.array_equal(river,water))
        self.assertTrue(np.allclose(ref[:,4],ground[:,4]))
        altered=ground.copy();altered[:,0]=900
        self.assertTrue(np.array_equal(ref,river_reference(altered,water,np.ones_like(water),250)[1]))
    def test_relative_spreading_and_balance(self):
        ground=np.zeros((12,12));water=np.zeros_like(ground,dtype=bool);water[:,0]=True
        cfg=DynamicConfig(dt_s=.1,duration_s=10,output_interval_s=1,river_ramp_s=1)
        def run():
            solver=RelativePipes(ground,np.zeros_like(ground),boundary=water,config=cfg,reference=np.zeros_like(ground))
            for _ in range(100):solver.step(1)
            return solver
        a,b=run(),run()
        self.assertTrue(np.array_equal(a.depth,b.depth))
        self.assertTrue(np.any(a.depth[:,1:]>0))
        self.assertGreaterEqual(a.depth.min(),0)
        self.assertLess(a.water_balance()['relative_residual'],1e-10)

    def test_cache_requires_every_saved_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory=Path(temporary)
            manifest={'configuration_hash':'digest','frames':[{'depth_file':'depth_0000000_123.tif'},{'depth_file':'depth_0000001_123.tif'}]}
            (directory/'manifest.json').write_text(json.dumps(manifest));(directory/'metrics.json').write_text('{}')
            (directory/'depth_0000000_123.tif').write_bytes(b'isolated fixture')
            self.assertFalse(cache_complete(directory,'digest'))
            (directory/'depth_0000001_123.tif').write_bytes(b'isolated fixture')
            self.assertTrue(cache_complete(directory,'digest'))
            self.assertFalse(cache_complete(directory,'changed'))
            manifest['frames'][1]['depth_file']='../outside.tif'
            (directory/'manifest.json').write_text(json.dumps(manifest))
            self.assertFalse(cache_complete(directory,'digest'))

    def test_relative_sources_track_exposure_inputs_without_satellite(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(relative,'DERIVED',Path(temporary)):
            root=Path(temporary)
            (root/'osm_buildings.geojson').write_text('{}');(root/'osm_roads.geojson').write_text('{}')
            names=[path.name for path in relative.input_paths()]
            self.assertIn('osm_buildings.geojson',names);self.assertIn('osm_roads.geojson',names)
            self.assertNotIn('stage_2023.csv',names);self.assertNotIn('s1_water_before.tif',names)
            (root/'buildings_merged.geojson').write_text('{}')
            names=[path.name for path in relative.input_paths()]
            self.assertIn('buildings_merged.geojson',names);self.assertNotIn('osm_buildings.geojson',names)

    def test_impermeable_building_counts_nearby_water_once(self):
        # Mathematical geometry fixture only; never a production flood mask.
        grid=from_origin(500000,3100000,10,10)
        to_lonlat=Transformer.from_crs(32643,4326,always_xy=True).transform
        def polygon(c,r):
            x,y=grid*(c,r)
            return Polygon([(x+1,y-1),(x+9,y-1),(x+9,y-9),(x+1,y-9)])
        buildings=[MultiPolygon([polygon(2,2),polygon(3,3)]),polygon(0,0)]
        road=LineString([grid*(3.5,2.1),grid*(3.5,2.9)])
        with tempfile.TemporaryDirectory() as temporary,patch.object(dynamic,'DERIVED',Path(temporary)),MemoryFile() as memory:
            root=Path(temporary)
            for name,geometries in [('buildings_merged.geojson',buildings),('osm_roads.geojson',[road])]:
                (root/name).write_text(json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':mapping(transform_xy(to_lonlat,geo))} for geo in geometries]}))
            depth=np.zeros((6,6));depth[2,3]=.8
            with memory.open(driver='GTiff',width=6,height=6,count=1,dtype='float32',crs='EPSG:32643',transform=grid) as ref:
                self.assertEqual(_exposure(depth,ref),{'buildings_wet':1,'roads_closed':1})
                self.assertEqual(_exposure(np.zeros_like(depth),ref),{'buildings_wet':0,'roads_closed':0})
if __name__=='__main__':unittest.main()
