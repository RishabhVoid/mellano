"""Isolated HTTP admission checks: no scenario computation or production writes."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sim import api,export_web,jobs
from sim.data import write_json


class RunAPITests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(os.environ,{"OTEC_ENABLE_LOCAL_COMPUTE":"1"}))
        root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.runs=root/'runs';self.runs.mkdir();self.web=root/'web';self.web.mkdir()
        for module,name,value in ((api,'RUNS',self.runs),(api,'WEB_DATA',self.web),(export_web,'RUNS',self.runs),(jobs,'LOGS',root/'logs'),(api,'jobs',{})):
            self.stack.enter_context(patch.object(module,name,value))
        self.stack.enter_context(patch.object(api,'requirements',return_value=[]))
        self.stack.enter_context(patch.object(api,'input_signature',return_value=[('isolated-input',1,1)]))
        self.stack.enter_context(patch.object(api,'export_all',return_value={}))
        self.launch=self.stack.enter_context(patch.object(api,'launch',side_effect=self.fake_launch))
        self.client=TestClient(api.app)  # No lifespan: never starts the production exporter.
        self.addCleanup(self.client.close)

    def fake_launch(self,name,command,detail,force=False):
        time.sleep(.02)  # Expose simultaneous requests before the durable status exists.
        jobs.update(name,detail,status='starting',pid=os.getpid(),launched_epoch=time.time())
        return {'job':name,'launched':True}

    def test_default_local_compute_is_disabled_without_launching(self):
        with patch.dict(os.environ,{"OTEC_ENABLE_LOCAL_COMPUTE":"0"}):
            response=self.client.post('/run',json={})
        self.assertEqual(response.status_code,503)
        self.launch.assert_not_called()

    def test_concurrent_different_requests_admit_only_one(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses=list(pool.map(lambda rise:self.client.post('/run',json={'river_plus_m':rise}),[1,2]))
        self.assertEqual(sorted(response.status_code for response in responses),[202,429])
        self.assertEqual(self.launch.call_count,1)
        self.assertEqual(len(export_web.read_json(self.runs/'index.json')['run_ids']),1)

    def test_concurrent_identical_requests_reuse_run_and_index(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:self.client.post('/run',json={'river_plus_m':1}),range(4)))
        self.assertTrue(all(response.status_code==202 for response in responses))
        self.assertEqual(len({response.json()['run_id'] for response in responses}),1)
        self.assertEqual(self.launch.call_count,1)
        self.assertEqual(len(export_web.read_json(self.runs/'index.json')['run_ids']),1)

    def test_restart_retains_pending_status_and_has_no_frames(self):
        run_id=self.client.post('/run',json={}).json()['run_id']
        api.jobs.clear()
        self.assertEqual(self.client.get(f'/runs/{run_id}/status').json()['status'],'starting')
        self.assertEqual(self.client.get(f'/runs/{run_id}/frames').status_code,404)
        self.assertEqual(self.client.get(f'/runs/{run_id}/metrics').status_code,404)

    def test_stale_job_does_not_block_a_new_request(self):
        jobs.update('scenario_aaaaaaaaaaaaaaaa','Running',status='running')
        response=self.client.post('/run',json={})
        self.assertEqual(response.status_code,202)
        self.assertEqual(jobs.state('scenario_aaaaaaaaaaaaaaaa')['status'],'failed')

    def test_failed_status_survives_api_restart(self):
        run_id='relative_edit_aaaaaaaaaaaaaaaa'
        write_json(self.runs/'index.json',{'run_ids':[run_id]})
        jobs.update('scenario_aaaaaaaaaaaaaaaa','Running',status='running')
        response=self.client.get(f'/runs/{run_id}/status')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()['status'],'failed')
        self.assertIn('interrupted',response.json()['error'])

    def test_unknown_run_is_not_a_file_reader(self):
        response=self.client.get('/runs/not_registered/metrics')
        self.assertEqual(response.status_code,404)
        with self.assertRaises(api.HTTPException):api.known_run('../outside')

    def test_complete_job_with_missing_output_restarts_without_duplicate_index(self):
        run_id=self.client.post('/run',json={}).json()['run_id']
        job_name='scenario_'+run_id.removeprefix('relative_edit_')
        jobs.update(job_name,'Finished',status='complete')
        directory=self.runs/run_id
        write_json(directory/'manifest.json',{'configuration_hash':'isolated','frames':[{'depth_file':'depth_0000000_123.tif'}]})
        write_json(directory/'metrics.json',{})
        self.assertEqual(self.client.post('/run',json={}).status_code,202)
        self.assertEqual(self.launch.call_count,2)
        self.assertTrue(self.launch.call_args.kwargs['force'])
        self.assertEqual(export_web.read_json(self.runs/'index.json')['run_ids'],[run_id])
        (directory/'depth_0000000_123.tif').write_bytes(b'isolated fixture')
        jobs.update(job_name,'Finished',status='complete')
        self.assertEqual(self.client.post('/run',json={}).status_code,202)
        self.assertEqual(self.launch.call_count,2)


if __name__=='__main__':unittest.main()
