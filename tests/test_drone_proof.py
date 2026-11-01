"""Analytic fixtures exist only in temporary test directories, never demo data."""
import tempfile
import csv
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from scipy.spatial.transform import Rotation

from sim import drone_proof as proof


def checkpoint_rows():
    shift=np.array([1.25,-2.5,4.0]);rows=[]
    for split,points in [('fit',[(0,0),(10,0),(0,10)]),('held_out',[(30,30),(40,30),(30,40),(40,40)])]:
        for x,y in points:
            target=np.array([x,y,20.]);ours=target-shift
            rows.append(dict(id=f'{split}-{x}-{y}',split=split,stable_ground='true',**dict(zip(('ours_x','ours_y','ours_z','lidar_x','lidar_y','lidar_z'),[*ours,*target]))))
    return rows


class DroneProofTests(unittest.TestCase):
    def test_fit_uses_only_controls(self):
        rows=checkpoint_rows();first=proof.checkpoint_validation(rows)
        np.testing.assert_allclose(first['fitted_xy_shift_m'],[1.25,-2.5]);self.assertEqual(first['fitted_vertical_offset_m'],4)
        self.assertAlmostEqual(first['held_out_z_rmse_m'],0)
        for row in rows:
            if row['split']=='held_out':row['ours_z']+=12
        second=proof.checkpoint_validation(rows)
        self.assertEqual(second['fitted_vertical_offset_m'],4)
        self.assertAlmostEqual(second['held_out_z_rmse_m'],12)

    def test_scale_rotation_tilt_remain_diagnostics(self):
        rows=checkpoint_rows();angle=np.deg2rad(3);rotation=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
        for row in rows:
            if row['split']=='held_out':
                xy=np.array([row['lidar_x'],row['lidar_y']]);distorted=1.02*rotation@(xy-[35,35])+[35,35]-[1.25,-2.5]
                row['ours_x'],row['ours_y']=distorted
                row['ours_z']+=.002*xy[0]-.003*xy[1]
        result=proof.checkpoint_validation(rows)
        self.assertAlmostEqual(result['scale_error_percent'],2,places=8)
        self.assertAlmostEqual(result['rotation_error_deg'],3,places=8)
        np.testing.assert_allclose(result['tilt_m_per_km'],[2,-3],atol=1e-10)
        self.assertGreater(result['held_out_xy_rmse_m'],0)
        self.assertGreater(result['held_out_z_rmse_m'],0)

    def test_reused_checkpoint_rejected(self):
        rows=checkpoint_rows();rows[-1]['id']=rows[0]['id']
        with self.assertRaisesRegex(ValueError,'unique'):proof.checkpoint_validation(rows)
        rows=checkpoint_rows();rows[-1]['lidar_x']=rows[0]['lidar_x'];rows[-1]['lidar_y']=rows[0]['lidar_y']
        with self.assertRaisesRegex(ValueError,'spatially separate'):proof.checkpoint_validation(rows)

    def test_region_leak_and_outside_checkpoint_rejected(self):
        def polygon(split,left,right):return {'properties':{'split':split},'geometry':{'type':'Polygon','coordinates':[[[left,-5],[right,-5],[right,50],[left,50],[left,-5]]]}}
        regions=[polygon('fit',-5,15),polygon('held_out',25,50)]
        self.assertEqual(len(proof.validation_regions(regions,'EPSG:32615','EPSG:32615',checkpoint_rows())['held_out']),1)
        with self.assertRaisesRegex(ValueError,'separation'):proof.validation_regions([regions[0],polygon('held_out',14,50)],'EPSG:32615','EPSG:32615',checkpoint_rows())
        rows=checkpoint_rows();rows[-1]['lidar_x']=500
        with self.assertRaisesRegex(ValueError,'inside'):proof.validation_regions(regions,'EPSG:32615','EPSG:32615',rows)

    def test_camera_pose_transform_preserves_projection(self):
        camera=Rotation.from_euler('xyz',[.1,.2,.3]).as_matrix();world=Rotation.from_euler('xyz',[-.2,.4,.2]).as_matrix()
        translation=np.array([1,2,3.]);offset=np.array([100,200,50.]);point=np.array([10,20,30.]);scale=3.7
        updated,t=proof.transformed_pose(camera,translation,scale,world,offset)
        original=camera@point+translation;transformed=updated@(scale*world@point+offset)+t
        np.testing.assert_allclose(transformed,scale*original,rtol=1e-12,atol=1e-12)

    def test_similarity_recovery_and_degenerate_rejection(self):
        source=np.array([[0,0,0],[10,0,1],[0,10,2],[4,6,3.]])
        expected=Rotation.from_euler('xyz',[.2,.3,.1]).as_matrix();target=source@(2.4*expected).T+[20,40,60]
        scale,rotation,offset=proof.similarity(source,target)
        self.assertAlmostEqual(scale,2.4);np.testing.assert_allclose(rotation,expected,atol=1e-12);np.testing.assert_allclose(offset,[20,40,60],atol=1e-12)
        with self.assertRaisesRegex(ValueError,'Collinear'):proof.similarity([[0,0,0],[1,0,0],[2,0,0]],[[0,0,0],[1,0,0],[2,0,0]])

    def test_nmad_is_robust_and_missing_stays_missing(self):
        self.assertEqual(proof.nmad([0,0,0,1000,np.nan]),0)
        self.assertIsNone(proof.nmad([np.nan]))

    def test_missing_status_does_not_claim_assets_or_metrics(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory,patch.object(proof,'BASE',Path(directory)),patch.object(proof.shutil,'which',return_value=None):
            status=proof.get_status(Path(directory)/'missing-inputs.json')
        self.assertFalse(status['available']);self.assertIsNone(status['metrics']);self.assertEqual(status['assets'],{})
        self.assertTrue(all(not item['available'] for item in status['steps'].values()))

    def test_changed_manifest_cannot_expose_old_success(self):
        latest={'available':True,'metrics':'old','manifest_sha256':proof.config_digest({'a':1}),'assets':{'video':'old'}}
        with patch.object(proof,'check_manifest',return_value=({'a':2},[])),patch.object(proof,'read_json',return_value=latest):status=proof.get_status()
        self.assertFalse(status['available']);self.assertIsNone(status['metrics']);self.assertEqual(status['assets'],{})

    def test_invalid_paths_rejected_before_filesystem_access(self):
        with patch.object(Path,'is_symlink',side_effect=AssertionError('filesystem metadata accessed')):
            for path in ['/never-access/.env','/never-access/token.env.backup','/never-access/../secret']:
                with self.assertRaises(ValueError):proof.safe_path(path)
        with patch.object(proof,'get_status',side_effect=AssertionError('status accessed')):
            for path in ['../../secret','data/derived/drone_proof/current/secret.json','data/derived/drone_proof/run_20261101T120000123456Z/.env']:
                with self.assertRaises(ValueError):proof.asset_path(path)

    def test_unlisted_asset_rejected(self):
        with patch.object(proof,'get_status',return_value={'assets':{}}):
            with self.assertRaisesRegex(ValueError,'completed stage'):proof.asset_path('data/derived/drone_proof/run_20261101T120000123456Z/sparse.ply')

    def test_existing_dense_stage_is_not_reused(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory,patch.object(proof,'need_tools'),patch.object(proof,'require_stage'),patch.object(proof,'command') as command:
            run=Path(directory);(run/'dense').mkdir()
            with self.assertRaisesRegex(ValueError,'cannot be reused'):proof.dense(run,{})
            command.assert_not_called()

    def test_grid_export_preserves_missing_cells(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory,patch.object(proof,'ROOT',Path(directory)):
            path=Path(directory)/'observed.tif'
            with rasterio.open(path,'w',driver='GTiff',width=2,height=2,count=1,dtype='float32',nodata=-9999,crs='EPSG:32615',transform=from_origin(100,200,.5,.5)) as raster:raster.write(np.array([[1,-9999],[2,3]],dtype='float32'),1)
            metadata=proof.read_json(proof.export_grid(path));values=np.fromfile(path.with_suffix('.bin'),dtype='<f4')
            self.assertEqual(metadata['observed_cells'],3);self.assertTrue(np.isnan(values[1]));np.testing.assert_array_equal(values[[0,2,3]],[1,2,3])

    def test_checkpoint_heights_are_read_from_actual_rasters(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory:
            run=Path(directory)
            for prefix,height in [('fresh',5.),('lidar',8.)]:
                with rasterio.open(run/f'{prefix}_ground_0p5m.tif','w',driver='GTiff',width=2,height=2,count=1,dtype='float32',nodata=-9999,crs='EPSG:32615',transform=from_origin(100,200,.5,.5)) as raster:raster.write(np.full((2,2),height,dtype='float32'),1)
            rows=[{'split':'fit','ours_x':100.25,'ours_y':199.75,'lidar_x':100.25,'lidar_y':199.75,'ours_z':-10000,'lidar_z':-10000}]
            sampled=proof.checkpoint_surface_heights(run,rows)
            self.assertEqual(sampled[0]['ours_z'],5);self.assertEqual(sampled[0]['lidar_z'],8)


    def test_complete_validation_uses_paired_cells_and_writes_metrics_last(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory:
            root=Path(directory);base=root/'derived';run=base/'run_20261101T120000123456Z';run.mkdir(parents=True)
            metric_path=root/'runs'/'drone_proof'/'metrics.json'
            for prefix,z in [('fresh',10.),('lidar',12.)]:
                for kind,height in [('ground',z),('dsm',z+10),('ndsm',10.)]:
                    with rasterio.open(run/f'{prefix}_{kind}_0p5m.tif','w',driver='GTiff',width=40,height=40,count=1,dtype='float32',nodata=-9999,crs='EPSG:32615',transform=from_origin(100,120,.5,.5)) as raster:raster.write(np.full((40,40),height,dtype='float32'),1)
            def polygon(left,bottom,right,top):return {'type':'Polygon','coordinates':[[[left,bottom],[right,bottom],[right,top],[left,top],[left,bottom]]]}
            areas=root/'areas.json';buildings=root/'buildings.json';checkpoints=root/'checkpoints.csv'
            proof.write_json(areas,{'features':[{'geometry':polygon(100.5,100.5,105,105),'properties':{'split':'fit'}},{'geometry':polygon(108,108,118,118),'properties':{'split':'held_out'}}]})
            proof.write_json(buildings,{'features':[{'geometry':polygon(109,109,111,111),'properties':{'id':'analytic-fixture'}}]})
            keys=['id','split','stable_ground','ours_x','ours_y','lidar_x','lidar_y']
            with checkpoints.open('w') as stream:
                writer=csv.DictWriter(stream,fieldnames=keys);writer.writeheader()
                for split,points in [('fit',[(101,102),(103,102),(101,104)]),('held_out',[(110,110),(112,110),(110,112),(112,112)])]:
                    for x,y in points:writer.writerow(dict(id=f'{split}-{x}-{y}',split=split,stable_ground='true',ours_x=x,ours_y=y,lidar_x=x,lidar_y=y))
            config={'output_crs':'EPSG:32615','video':{'acquired_at':'2019-01-01T00:00:00Z'},'telemetry':{'vertical_datum':'analytic datum A'},'lidar':{'acquired_at':'2019-01-02T00:00:00Z','vertical_datum':'analytic datum B'},'validation':{'checkpoints_csv':str(checkpoints),'areas_geojson':str(areas),'buildings_geojson':str(buildings),'geometry_crs':'EPSG:32615','buildings_crs':'EPSG:32615'}}
            proof.write_json(run/'proof.json',{'run_date':'2026-11-01T12:00:00Z','validation_areas_sha256':proof.sha256(areas),'steps':{'ndsm':{'available':True},'lidar':{'available':True}},'assets':{}})
            with patch.object(proof,'ROOT',root),patch.object(proof,'BASE',base),patch.object(proof,'METRICS',metric_path):
                self.assertFalse(metric_path.exists());result=proof.validate(run,config)
                self.assertEqual(result['fitted_vertical_offset_m'],2)
                self.assertEqual(result['surface_nmad_m'],0);self.assertEqual(result['surface_rmse_m'],0)
                self.assertEqual(result['per_building'][0]['ours_median_m'],10)
                self.assertEqual(result['per_building'][0]['lidar_median_m'],10)
                self.assertEqual(result['temporal_gap_days'],1);self.assertTrue(metric_path.is_file())
                with self.assertRaisesRegex(ValueError,'already recorded'):proof.validate(run,config)



class ResourceLimitTests(unittest.TestCase):
    def test_native_command_limits_threads_and_priority(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory:
            run=Path(directory)
            with patch.object(proof,'executable',return_value='/usr/bin/true'),patch.object(proof.os,'getpriority',return_value=0),patch.object(proof.subprocess,'run') as execute:
                proof.command(run,['colmap','feature_extractor'])
            args=execute.call_args.args[0];settings=execute.call_args.kwargs['env']
            self.assertEqual(args[:3],['/usr/bin/nice','-n','15'])
            for name in ('OMP_NUM_THREADS','OMP_THREAD_LIMIT','OPENBLAS_NUM_THREADS','VECLIB_MAXIMUM_THREADS','MKL_NUM_THREADS','NUMEXPR_NUM_THREADS'):self.assertEqual(settings[name],'2')

    def test_dense_resolves_exported_relative_images_in_dense_folder(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory:
            root=Path(directory);base=root/'derived';run=base/'run_20261101T120000123456Z';run.mkdir(parents=True)
            tools=root/'data'/'raw'/'drone_proof'/'tools'/'openmvs-2.4.0';tools.mkdir(parents=True)
            (tools/'DensifyPointCloud_help.txt').write_text('CPU-only test command interface')
            proof.write_json(run/'proof.json',{'steps':{'gps_alignment':{'available':True}},'assets':{}})
            def execute(current,args):
                if args[0]=='colmap':
                    image=run/'dense'/'images'/'frame_000001.jpg';image.parent.mkdir();image.write_bytes(b'isolated image-path fixture')
                elif args[0] in ('InterfaceCOLMAP','DensifyPointCloud'):
                    working=Path(args[args.index('-w')+1]) if '-w' in args else current
                    if not (working/'images'/'frame_000001.jpg').is_file():raise RuntimeError('Relative scene image resolves from the wrong folder')
                    if args[0]=='DensifyPointCloud':(run/'dense'/'scene_dense.ply').write_text('isolated command-contract fixture')
            with patch.object(proof,'ROOT',root),patch.object(proof,'BASE',base),patch.object(proof,'need_tools'),patch.object(proof,'colmap_help',return_value='--num_threads'),patch.object(proof,'command',side_effect=execute):
                proof.dense(run,{})
                self.assertTrue(proof.read_json(run/'proof.json')['steps']['dense']['available'])

    def test_partial_database_preserved_but_not_reused(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory:
            root=Path(directory);base=root/'derived';run=base/'run_20261101T120000123456Z';run.mkdir(parents=True)
            (run/'database.db').write_bytes(b'isolated test partial artifact');(run/'sparse').mkdir();(run/'sparse_text').mkdir()
            proof.write_json(run/'proof.json',{'steps':{name:{'available':name=='frames'} for name in proof.STAGES}})
            with patch.object(proof,'ROOT',root),patch.object(proof,'BASE',base):record=proof.archive_interrupted_sparse(run)
            self.assertFalse((run/'database.db').exists())
            self.assertEqual((root/record['archive']/'database.db').read_bytes(),b'isolated test partial artifact')
            self.assertFalse(record['reused_partial_database'])
            self.assertEqual(proof.read_json(run/'proof.json')['resource_policy']['max_threads'],2)


class SurfaceRegistrationTests(unittest.TestCase):
    def test_translation_fit_ignores_held_out_changes(self):
        rows,columns=np.indices((240,240),dtype=float);x=columns*.5;y=-rows*.5
        def height(x,y):return 200+.6*np.sin(x/5)+.7*np.cos(y/7)+.2*np.sin((x+y)/3)
        reference=height(x,y);ours=height(x+2,y-1.5)-4.75
        fit=np.zeros_like(reference,dtype=bool);fit[:, :140]=True
        baseline=proof.fit_surface_translation(ours,reference,fit)
        changed=reference.copy();changed[:,160:]+=1000
        result=proof.fit_surface_translation(ours,changed,fit)
        np.testing.assert_allclose(result['fitted_xy_shift_m'],[2,-1.5],atol=.06)
        self.assertAlmostEqual(result['fitted_vertical_offset_m'],4.75,places=2)
        self.assertEqual(baseline,result)
        transform_grid=from_origin(0,0,.5,.5)
        aligned=proof.shifted_grid(ours,transform_grid,*result['fitted_xy_shift_m'])
        residual=aligned+result['fitted_vertical_offset_m']-reference
        self.assertLess(np.nanmedian(np.abs(residual)),.02)

    def test_public_image_surface_validation_writes_only_computed_metrics(self):
        with tempfile.TemporaryDirectory(prefix='drone-test-',dir='/private/tmp') as directory:
            root=Path(directory);base=root/'derived';run=base/'run_20261101T120000123456Z';run.mkdir(parents=True)
            metric_path=root/'runs'/'drone_proof'/'metrics.json'
            rr,cc=np.indices((256,256),dtype=float);x=(cc+.5)*.5;y=128-(rr+.5)*.5
            def height(x,y):return 200+.6*np.sin(x/5)+.7*np.cos(y/7)+.2*np.sin((x+y)/3)
            geometry={'type':'Polygon','coordinates':[[[80,80],[90,80],[90,90],[80,90],[80,80]]]}
            for prefix in ('fresh','lidar'):
                xx=x+2 if prefix=='fresh' else x;yy=y-1.5 if prefix=='fresh' else y
                ground=height(xx,yy)-(4.75 if prefix=='fresh' else 0)
                roof=np.where((xx>80)&(xx<90)&(yy>80)&(yy<90),5.,0.)
                for kind,data in [('ground',ground),('dsm',ground+roof),('ndsm',roof)]:
                    with rasterio.open(run/f'{prefix}_{kind}_0p5m.tif','w',driver='GTiff',width=256,height=256,count=1,dtype='float32',nodata=-9999,crs='EPSG:32617',transform=from_origin(0,128,.5,.5)) as raster:raster.write(data.astype('float32'),1)
            def rectangle(left,right):return {'type':'Polygon','coordinates':[[[left,0],[right,0],[right,128],[left,128],[left,0]]]}
            areas=root/'areas.json';buildings=root/'buildings.json'
            proof.write_json(areas,{'features':[{'geometry':rectangle(0,50),'properties':{'split':'fit'}},{'geometry':rectangle(60,128),'properties':{'split':'held_out'}}]})
            proof.write_json(buildings,{'features':[{'geometry':geometry,'properties':{'id':'analytic-test-fixture'}}]})
            config={'output_crs':'EPSG:32617','image_collection':{'acquired_at':'2020-01-01 local date','vertical_datum':'analytic datum A'},'lidar':{'acquired_at':'2020-02-01 local date','vertical_datum':'analytic datum B'},'validation':{'areas_geojson':str(areas),'buildings_geojson':str(buildings),'geometry_crs':'EPSG:32617','buildings_crs':'EPSG:32617'}}
            proof.write_json(run/'proof.json',{'run_date':'2026-11-01T12:00:00Z','validation_areas_sha256':proof.sha256(areas),'steps':{'ndsm':{'available':True},'lidar':{'available':True}},'assets':{}})
            with patch.object(proof,'ROOT',root),patch.object(proof,'BASE',base),patch.object(proof,'METRICS',metric_path):
                self.assertFalse(metric_path.exists());result=proof.validate_surfaces(run,config)
                np.testing.assert_allclose(result['fitted_xy_shift_m'],[2,-1.5],atol=.08)
                self.assertLess(result['surface_nmad_m'],.02)
                self.assertEqual(result['per_building'][0]['ours_median_m'],5.)
                self.assertEqual(result['per_building'][0]['lidar_median_m'],5.)
                self.assertTrue(metric_path.is_file());self.assertIsNone(result['temporal_gap_days'])
                self.assertEqual(result['run_id'],run.name)
                with self.assertRaisesRegex(ValueError,'already recorded'):proof.validate_surfaces(run,config)

    def test_flat_held_out_ground_does_not_invent_correspondences(self):
        flat=np.full((200,200),20.);held=np.ones_like(flat,dtype=bool)
        matches=proof.held_out_patch_matches(flat,flat,held,from_origin(0,100,.5,.5),(0,0))
        self.assertEqual(matches,[])

if __name__=='__main__':unittest.main()
