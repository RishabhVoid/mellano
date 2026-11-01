"""Exercise durable admission and worker interruption using isolated temporary jobs."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sim import jobs


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.logs=Path(self.temp.name)
        self.patch=patch.object(jobs,'LOGS',self.logs)
        self.patch.start();self.addCleanup(self.patch.stop)

    def test_concurrent_launches_start_one_worker(self):
        with patch.object(jobs.subprocess,'Popen',return_value=SimpleNamespace(pid=os.getpid())) as spawn:
            with ThreadPoolExecutor(max_workers=8) as pool:
                results=list(pool.map(lambda _:jobs.launch('test_job',[sys.executable,'-c','pass']),range(8)))
        self.assertEqual(spawn.call_count,1)
        self.assertEqual(sum(item['launched'] for item in results),1)
        self.assertEqual(jobs.state('test_job')['status'],'starting')

    def test_interrupted_status_reconciles_but_completion_is_preserved(self):
        jobs.update('test_job','Running',status='running')
        self.assertEqual(jobs.reconciled_state('test_job')['status'],'failed')
        self.assertIn('interrupted',jobs.state('test_job')['detail'])
        jobs.update('test_job','Finished',status='complete')
        self.assertEqual(jobs.reconciled_state('test_job')['status'],'complete')

    def test_running_child_keeps_lock_after_worker_crash(self):
        child_code="import time; print('READY',flush=True); time.sleep(30)"
        code="from pathlib import Path; import sys; from sim import jobs; jobs.LOGS=Path(sys.argv[1]); raise SystemExit(jobs.worker('test_job',[sys.executable,'-c',sys.argv[2]]))"
        worker=subprocess.Popen([sys.executable,'-u','-c',code,str(self.logs),child_code],cwd=jobs.ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        child_pid=None
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(worker.stdout,selectors.EVENT_READ)
                self.assertTrue(selector.select(timeout=5),'Isolated child did not start')
                self.assertEqual(worker.stdout.readline().strip(),'READY')
            deadline=time.monotonic()+2
            while not jobs.state('test_job').get('child_pid') and time.monotonic()<deadline:time.sleep(.01)
            child_pid=jobs.state('test_job')['child_pid']
            worker.kill();worker.wait(timeout=5)
            self.assertTrue(jobs.active('test_job'))
            self.assertEqual(jobs.reconciled_state('test_job')['status'],'running')
            with patch.object(jobs.subprocess,'Popen') as spawn:
                self.assertFalse(jobs.launch('test_job',[sys.executable,'-c','pass'])['launched'])
                spawn.assert_not_called()
        finally:
            if worker.poll() is None:worker.kill();worker.wait(timeout=5)
            if child_pid:
                try:os.kill(child_pid,signal.SIGKILL)
                except ProcessLookupError:pass
            worker.stdout.close();worker.stderr.close()

    def test_invalid_job_name_does_not_write_files(self):
        with self.assertRaises(ValueError):jobs.launch('../escape',['unused'])
        self.assertEqual(list(self.logs.iterdir()),[])


if __name__=='__main__':unittest.main()
