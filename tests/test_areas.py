"""Small isolated fixtures only; no network, source-pack build, or background worker."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from pyproj import Transformer
from sim import areas,area_builder

class AreasTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=areas.Store(self.root/'metadata.sqlite3')
    def tearDown(self):self.temp.cleanup()
    def region(self):return areas.selection({'bbox':[77.2004,28.6004,77.202,28.602]})
    def fixture_export(self):
        output=self.root/'export';output.mkdir(exist_ok=True);region=self.region();project=Transformer.from_crs(4326,region['suggested_crs'],always_xy=True);points=[project.transform(lon,lat) for lon in (region['bbox'][0],region['bbox'][2]) for lat in (region['bbox'][1],region['bbox'][3])];west=min(p[0] for p in points)-20;north=max(p[1] for p in points)+20;grid={'width':24,'height':25,'cell_size_m':10,'origin':[west,north],'crs':region['suggested_crs']}
        areas.write_json(output/'terrain.json',grid);np.full((25,24),200,dtype='<f4').tofile(output/'terrain.bin');areas.write_json(output/'buildings.json',{'buildings':[{'id':'fixture','footprint_source':'unit-test-only','footprint':[[[10,10],[20,10],[20,20],[10,10]]],'height':None}]});areas.write_json(output/'area-model.json',{'bbox':region['bbox'],'synthetic':False,'provenance':[{'source':'isolated contract fixture','source_url':'https://example.test/unit-fixture','license':'test-only'}]})
        return output,region
    def test_region_identity_and_size(self):
        a=self.region();b=areas.selection({'bbox':list(a['bbox'])});self.assertEqual(a['area_id'],b['area_id']);self.assertEqual(a['suggested_crs'],'EPSG:32643')
        center=areas.selection({'center':[77.25,28.65],'size_km':5});self.assertAlmostEqual(center['width_km'],5,places=2);self.assertAlmostEqual(center['height_km'],5,places=2)
    def test_invalid_bounds_sizes_and_ambiguity(self):
        for request in [{'bbox':[180,0,-180,1]},{'bbox':[77,28,float('nan'),29]},{'bbox':[77,28,78,29]},{'center':[77,28],'size_km':0},{'bbox':[True,1,2,3]},{'bbox':[77.2,28.6,77.21,28.61],'center':[77,28],'size_km':1}]:
            with self.assertRaises((ValueError,TypeError)):areas.selection(request)
    def test_duplicate_admission_is_single_durable_job(self):
        region=self.region()
        with ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(lambda _:self.store.admit(region),range(8)))
        self.assertEqual(len({r['job_id'] for r in rows}),1);other=areas.Store(self.root/'metadata.sqlite3');self.assertEqual(other.get(rows[0]['job_id'])['status'],'queued')
    def test_configured_database_never_falls_back_when_driver_is_missing(self):
        with patch.dict('sys.modules',{'psycopg':None}):
            with self.assertRaisesRegex(RuntimeError,'PostgreSQL driver unavailable'):
                areas.Store(self.root/'unused.sqlite3',dsn='postgresql://example.invalid/test')
        self.assertFalse((self.root/'unused.sqlite3').exists())
    def test_http_selection_does_not_launch_process(self):
        with patch.object(areas,'cached_delhi',return_value=None),patch.object(areas.subprocess,'Popen') as process:
            a=areas.select_area({'bbox':self.region()['bbox']},self.store);b=areas.select_area({'bbox':self.region()['bbox']},self.store)
        self.assertEqual(a['job_id'],b['job_id']);self.assertEqual(a['execution'],'remote_server_required');process.assert_not_called()
    def test_claim_is_exclusive_and_expired_is_not_restarted(self):
        job=self.store.admit(self.region());first=self.store.claim('worker-a');self.assertEqual(first['job_id'],job['job_id']);self.assertIsNone(self.store.claim('worker-b'))
        with self.store.connection() as con:self.store.execute(con,'UPDATE area_jobs SET lease_until=0 WHERE job_id=?',(job['job_id'],))
        self.assertIsNone(self.store.claim('worker-b'));self.assertEqual(self.store.get(job['job_id'])['status'],'interrupted');self.assertTrue(self.store.requeue(job['job_id']));self.assertIsNotNone(self.store.claim('worker-b'))
    def test_worker_without_builder_is_honest_server_required(self):
        self.store.admit(self.region())
        with patch.object(areas.subprocess,'Popen') as process:result=areas.worker_once(self.store,'operator',command=None)
        self.assertEqual(result['status'],'server_required');process.assert_not_called()
    def test_changed_model_version_is_not_old_ready_cache(self):
        output,region=self.fixture_export();model=areas.publish_export(output,region,self.root/'published');self.store.admit(region,model)
        with patch.object(areas,'MODEL_VERSION','next-model'),patch.object(areas,'cached_delhi',return_value=None):result=areas.select_area({'bbox':region['bbox']},self.store)
        self.assertEqual(result['status'],'queued')
    def test_validated_publication_and_subset_metrics_scope(self):
        output,region=self.fixture_export();model=areas.publish_export(output,region,self.root/'published');self.assertEqual(areas.publish_export(output,region,self.root/'published')['model_id'],model['model_id'])
        descriptor=areas.clip_descriptor(region,model);self.assertEqual(descriptor['metrics_scope'],'unavailable_for_model');self.assertEqual(descriptor['status'],'ready')
        model['assets']['product_catalog_url']='/data/product/catalog.json';descriptor=areas.clip_descriptor(region,model);self.assertEqual(descriptor['metrics_scope'],'full_cached_aoi')
    def test_rejects_missing_provenance_truncated_or_invalid_raster(self):
        output,region=self.fixture_export();(output/'terrain.bin').write_bytes(bytes(4))
        with self.assertRaisesRegex(ValueError,'length'):areas.validate_export(output,region)
        np.full((25,24),np.nan,dtype='<f4').tofile(output/'terrain.bin')
        with self.assertRaisesRegex(ValueError,'invalid heights'):areas.validate_export(output,region)
        np.zeros((25,24),dtype='<f4').tofile(output/'terrain.bin');data=areas.read_json(output/'area-model.json');data['provenance']=[];areas.write_json(output/'area-model.json',data)
        with self.assertRaisesRegex(ValueError,'provenance'):areas.validate_export(output,region)
    def test_declared_bbox_cannot_hide_wrong_grid_location(self):
        output,region=self.fixture_export();terrain=areas.read_json(output/'terrain.json');terrain['origin']=[0,0];areas.write_json(output/'terrain.json',terrain)
        with self.assertRaisesRegex(ValueError,'actually cover'):areas.validate_export(output,region)
    def test_symlink_artifact_is_refused(self):
        output,region=self.fixture_export();(output/'terrain.bin').rename(output/'real.bin');(output/'terrain.bin').symlink_to(output/'real.bin')
        with self.assertRaisesRegex(ValueError,'symlink'):areas.validate_export(output,region)
    def test_server_published_model_can_serve_contained_view(self):
        output,region=self.fixture_export();model=areas.publish_export(output,region,self.root/'published');self.store.admit(region,model)
        with patch.object(areas,'cached_delhi',return_value=None):result=areas.select_area({'bbox':region['bbox']},self.store)
        self.assertEqual(result['status'],'ready');self.assertEqual(result['model_id'],model['model_id'])
    def test_regional_builder_uses_ground_and_only_explicit_heights(self):
        tile=self.root/'test-only-source.tif';array=200+np.arange(900,dtype='float32').reshape(30,30)/1000
        with rasterio.open(tile,'w',driver='GTiff',height=30,width=30,count=1,dtype='float32',crs='EPSG:4326',transform=from_origin(77.2,28.603,.0001,.0001)) as stream:stream.write(array,1)
        geometry=[{'lon':x,'lat':y} for x,y in [(77.2007,28.6007),(77.201,28.6007),(77.201,28.601),(77.2007,28.6007)]]
        osm={'elements':[{'type':'way','id':1,'tags':{'building':'yes','height':'8 m'},'geometry':geometry},{'type':'way','id':2,'tags':{'building':'yes','building:levels':'9'},'geometry':geometry}],'otec_source':'https://example.test/isolated-osm-fixture','osm3s':{'timestamp_osm_base':'fixture'}}
        output=self.root/'regional'
        with patch.object(area_builder,'pack_for_bbox',return_value={'sha256':areas.digest(tile),'source_url':'https://example.test/unit-source'}):
            area_builder.assemble({'bbox':self.region()['bbox']},tile,osm,output)
        buildings=areas.read_json(output/'buildings.json')['buildings'];self.assertEqual([b['height'] for b in buildings],[8,None]);self.assertTrue((np.fromfile(output/'terrain.bin','<f4')<201).all());self.assertEqual(areas.read_json(output/'embankments.json')['lines'],[])
        model,files=areas.validate_export(output,self.region());self.assertIn('context.json',files);self.assertIn('landcover.bin',files);self.assertFalse(model['capabilities']['disaster_runs'])
    def test_regional_builder_rejects_unverified_tile_and_other_region(self):
        tile=self.root/'wrong.tif';tile.write_bytes(b'not a verified source')
        with self.assertRaisesRegex(ValueError,'verified FABDEM'):area_builder.assemble({'bbox':self.region()['bbox']},tile,{'elements':[]},self.root/'wrong-output')
        with self.assertRaisesRegex(ValueError,'source pack'):area_builder.assemble({'bbox':[72.8,18.9,72.81,18.91]},tile,{'elements':[]},self.root/'outside')
    def test_height_levels_do_not_become_invented_metres(self):
        self.assertEqual(area_builder.mapped_height({'height':'10 ft'})[0],3.048);self.assertIsNone(area_builder.mapped_height({'building:levels':'5'})[0]);self.assertIsNone(area_builder.mapped_height({'height':'unknown'})[0])
    def test_api_validation_and_queued_descriptor_status(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sim.areas_api import router
        app=FastAPI();app.include_router(router)
        with patch.object(areas,'get_store',return_value=self.store),patch.object(areas,'cached_delhi',return_value=None):
            client=TestClient(app);response=client.post('/areas/select',json={'bbox':self.region()['bbox']});self.assertEqual(response.status_code,200);body=response.json();self.assertEqual(client.get(body['descriptor_url']).status_code,409);self.assertEqual(client.get(body['status_url']).json()['status'],'queued');self.assertEqual(client.post('/areas/select',json={'bbox':[77,28,78,29]}).status_code,422)

if __name__=='__main__':unittest.main()
