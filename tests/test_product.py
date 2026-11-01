import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
from affine import Affine
from rasterio.features import rasterize
from shapely.geometry import box
from sim import product
from sim.disasters.base import Frame
from sim.disasters.quake import Quake
from sim.disasters.fire import Fire
from sim.disasters.flood import Flood,Chain

class TinyContext:
    def __init__(self):
        self.shape=(10,10);self.cell=10.;self.valid=np.ones(self.shape,bool);self.water=np.zeros(self.shape,bool)
        self.polygons=[box(10,10,20,20),box(25,10,35,20),box(80,80,90,90)]
        self.buildings=[{}, {}, {}];self.centers=np.array([[p.centroid.x,p.centroid.y] for p in self.polygons]);self.heights=np.array([20.,10.,np.nan])
        self.entities=np.arange(3);self.entity_keys=np.array(['a','b','c']);self.population=None;self.population_meta={'available':False,'reason':'missing real WorldPop'}
    def raster_buildings(self,indices,values,buffer_m=0):
        shapes=[(self.polygons[int(i)].buffer(buffer_m),float(v)) for i,v in zip(indices,values)]
        return rasterize(shapes,out_shape=self.shape,transform=Affine(10,0,0,0,10,0),dtype='float32',all_touched=True) if shapes else np.zeros(self.shape,'float32')
    unique_count=product.Context.unique_count
    building_counts=product.Context.building_counts
    def building_max(self,grid):return grid[[1,1,8],[1,3,8]]

class ProductTests(unittest.TestCase):
    def test_max_pool_preserves_extreme_and_edge(self):
        a=np.zeros((5,7),'uint8');a[1,1]=4;a[-1,-1]=3
        np.testing.assert_array_equal(product.reduced(a),[[4,0],[0,3]])
    def test_stable_entity_count_deduplicates_parts(self):
        ctx=TinyContext();ctx.entities=np.array([0,0,1]);ctx.entity_keys=np.array(['same','other'])
        self.assertEqual(ctx.building_counts(np.array([1,4,255],'uint8')),([0,0,0,0,1],1))
    def test_quake_missing_height_unknown_and_deterministic(self):
        ctx=TinyContext();request={'point':[15,15],'magnitude':6}
        a=Quake().apply(ctx,request).step(30);b=Quake().apply(ctx,request).step(30)
        self.assertEqual(a.building_state[-1],255);self.assertEqual(a.building_state[0],2)
        self.assertGreater(a.debris.max(),0);np.testing.assert_array_equal(a.building_state,b.building_state)
    def test_quake_zero_before_event(self):
        frame=Quake().apply(TinyContext(),{'point':[15,15],'magnitude':6}).step(0)
        self.assertTrue((frame.severity==0).all());self.assertEqual(frame.building_state[-1],255)
    def test_fire_real_gap_and_wind_direction(self):
        ctx=TinyContext();base={'building_index':0,'wind_speed_m_s':5}
        east=Fire().apply(ctx,{**base,'wind_direction_deg':90});west=Fire().apply(ctx,{**base,'wind_direction_deg':270})
        self.assertLess(east.arrival[1],west.arrival[1]);self.assertTrue(np.isinf(east.arrival[2]))
        self.assertEqual(east.step(0).building_state[0],4);self.assertEqual(east.step(121).building_state[0],5)
    def test_flood_threshold_boundaries(self):
        ctx=TinyContext();depth=np.zeros(ctx.shape,'float32');depth[0,:6]=[0,.3,.6,1.2,2,2.1]
        flood=Flood();flood.context=ctx;flood.request={'river_plus_m':3};flood.offset=0;flood.quake=None
        frame=flood.from_depth(0,depth)
        np.testing.assert_array_equal(frame.severity[0,:6],[0,0,1,2,3,4])
    def test_missing_population_is_null_not_zero(self):
        ctx=TinyContext();frame=Frame(0,np.ones(ctx.shape,'uint8'),np.ones(3,'uint8'),np.ones(3,'uint8'))
        result=product.impact(ctx,frame,{'metrics':{'roads_closed':None},'limitations':[]},'test')
        self.assertIsNone(result['people_affected']);self.assertFalse(result['population']['available'])
        ctx.population=np.ones(ctx.shape);ctx.population[0,0]=np.nan;ctx.population_meta={'available':True}
        result=product.impact(ctx,frame,{'metrics':{'roads_closed':0},'limitations':[]},'test')
        self.assertIsNone(result['people_affected']);self.assertEqual(result['people_in_known_cells'],99.)
    def test_population_counts_affected_land_only(self):
        ctx=TinyContext();ctx.population=np.ones(ctx.shape)*2;ctx.population_meta={'available':True};ctx.water[0]=True
        frame=Frame(0,np.ones(ctx.shape,'uint8'),np.ones(3,'uint8'),np.ones(3,'uint8'))
        result=product.impact(ctx,frame,{'metrics':{'roads_closed':0},'limitations':[]},'test')
        self.assertEqual(result['people_affected'],180.);self.assertEqual(result['affected_area_km2'],.009)
    def test_chain_includes_quake_affected_land_after_flood(self):
        ctx=TinyContext();frame=Frame(70,np.ones(ctx.shape,'uint8'),np.ones(3,'uint8'),np.ones(3,'uint8'),depth=np.zeros(ctx.shape),extra={'module':'chain'})
        result=product.impact(ctx,frame,{'metrics':{'roads_closed':0},'limitations':[]},'test')
        self.assertEqual(result['affected_area_km2'],.01)
    def test_invalid_request_and_path_rejected(self):
        for value in ['../a','product_123','product_'+'z'*16]:
            with self.assertRaises(ValueError):product.safe_id(value)
        for request in [{'module':'unknown'},{'module':'flood','river_plus_m':float('nan')},{'module':'flood','seed':1.2},{'module':'flood','seed':True}]:
            with self.assertRaises(ValueError):product.normalize(request)
    def test_replay_remains_gated(self):
        with patch.object(product,'catalog',return_value={'presets':[{'id':'replay2023','enabled':False,'reason':'Missing real masks/calibration'}]}):
            with self.assertRaisesRegex(ValueError,'Missing real masks'):product.normalize({'module':'flood','replay':True})
    def test_static_replay_never_inherits_hypothetical_rise(self):
        with patch.object(product,'catalog',return_value={'presets':[{'id':'replay2023','enabled':True,'reason':None}]}):
            self.assertEqual(product.normalize({'module':'flood','replay':True})['river_plus_m'],0.)
    def test_population_path_cannot_escape_derived(self):
        with self.assertRaises(ValueError):product.population_path('../outside.tif')
    def test_product_api_rejects_invalid_run_and_unknown_fields(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from sim.product_api import router
        app=FastAPI();app.include_router(router);client=TestClient(app)
        self.assertEqual(client.get('/product/runs/not-a-run/status').status_code,404)
        self.assertEqual(client.post('/product/run',json={'module':'flood','unrecognized':1}).status_code,422)
        self.assertEqual(client.post('/product/run',json={'module':'unknown'}).status_code,422)
    def test_hazard_signature_ignores_response_population_only(self):
        a=[('data/derived/ground_10m.tif',10,1),('web/public/data/response_context.json',20,2),('data/derived/population_10m.tif',30,3)]
        b=[a[0],('web/public/data/response_context.json',20,4),('data/derived/population_10m.tif',30,5)]
        self.assertEqual(product.hazard_signature(a),product.hazard_signature(b))
        b[0]=('data/derived/ground_10m.tif',10,6)
        self.assertNotEqual(product.hazard_signature(a),product.hazard_signature(b))
    def test_provenance_only_population_completion_does_not_change_identity(self):
        a={'generated_at':'before','stations':[{'id':'real','position':[1,2]}],'population':{'available':True,'valid_population_total':10,'acquisition':{'full_sha256':None}}}
        b={'generated_at':'after','stations':a['stations'],'population':{**a['population'],'acquisition':{'full_sha256':'verified'}}}
        self.assertEqual(product.response_scientific_signature(a),product.response_scientific_signature(b))
        b['population']['valid_population_total']=11
        self.assertNotEqual(product.response_scientific_signature(a),product.response_scientific_signature(b))
    def test_shoreline_cut_uses_real_boundary_and_preserves_buildings(self):
        ctx=TinyContext();ctx.ground=np.full(ctx.shape,2.);ctx.ground[:,:4]=0;ctx.surface=ctx.ground.copy();ctx.blocked=np.zeros(ctx.shape,bool);ctx.blocked[5,4]=True;ctx.banks=np.zeros(ctx.shape,bool);ctx.water[:,:4]=True;ctx.cached_frames=lambda request:None
        flood=Flood().apply(ctx,{'module':'breach','bank_source':'osm_river_shoreline','point':[45,45],'river_plus_m':3})
        self.assertGreater(flood.breach_info['lowered_cells'],0);self.assertFalse(flood.breach_info['engineered_levee_confirmed'])
        self.assertFalse(flood.solver.valid[5,4]);self.assertEqual(ctx.surface[5,4],2.);np.testing.assert_array_equal(flood.solver.bed[:,:4],ctx.ground[:,:4])
        self.assertIn('engineered levee unconfirmed',flood.labels['label'])
    def test_native_depth_refresh_matches_scenario_and_terrain(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);runs=root/'runs';public=root/'public';index=root/'index.json';run_id='product_'+'a'*16;directory=runs/run_id;directory.mkdir(parents=True)
            request={'module':'breach','point':[10,20],'river_plus_m':1};signature=[['data/derived/ground_10m.tif',10,1],['web/public/data/response_context.json',20,2]]
            (directory/'manifest.json').write_text(json.dumps({'scenario':request,'input_signature':signature,'frames':[{'time_s':0,'depth_url':'/data/native'}]}));(directory/'depth_0000.tif').write_bytes(b'native fixture')
            index.write_text(json.dumps({'runs':[{'run_id':run_id}]}));ctx=SimpleNamespace()
            with patch.object(product,'RUN_ROOT',runs),patch.object(product,'PUBLIC',public),patch.object(product,'INDEX',index),patch.object(product,'input_signature',return_value=[signature[0],['web/public/data/response_context.json',30,3]]):
                self.assertEqual(product.Context.cached_product_frames(ctx,request),[(0.,directory/'depth_0000.tif')])
                self.assertIsNone(product.Context.cached_product_frames(ctx,{**request,'point':[20,20]}))
            with patch.object(product,'RUN_ROOT',runs),patch.object(product,'PUBLIC',public),patch.object(product,'INDEX',index),patch.object(product,'input_signature',return_value=[['data/derived/ground_10m.tif',10,9]]):
                self.assertIsNone(product.Context.cached_product_frames(ctx,request))
    def test_published_run_with_truncated_asset_is_not_complete(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);public=root/'product'/'runs';run_id='product_'+'b'*16;directory=public/run_id;directory.mkdir(parents=True)
            prefix='/data/product/runs/'+run_id+'/'
            manifest={'grid':{'width':2,'height':2},'building_index':{'count':3},'frames':[{'severity_url':prefix+'severity.bin','building_states_url':prefix+'states.bin'}]}
            (directory/'manifest.json').write_text(json.dumps(manifest));(directory/'severity.bin').write_bytes(bytes(4));(directory/'states.bin').write_bytes(bytes(6))
            with patch.object(product,'WEB_DATA',root),patch.object(product,'PUBLIC',public):
                self.assertIsNotNone(product.complete_manifest(run_id))
                (directory/'states.bin').write_bytes(bytes(5));self.assertIsNone(product.complete_manifest(run_id))
    def test_cached_flood_rejects_stale_source(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);derived=root/'derived';runs=root/'runs';derived.mkdir();directory=runs/'relative_river_plus_3m';directory.mkdir(parents=True)
            (derived/'ground_10m.tif').write_bytes(b'current')
            (directory/'manifest.json').write_text(json.dumps({'frames':[{'elapsed_s':0,'depth_file':'depth_0_0.tif'}],'input_sha256':{'ground_10m.tif':'stale'}}))
            with patch.object(product,'DERIVED',derived),patch.object(product,'RUNS',runs):
                with self.assertRaisesRegex(ValueError,'stale'):product.Context.cached_frames(SimpleNamespace(),{'module':'flood','river_plus_m':3})

if __name__=='__main__':unittest.main()
