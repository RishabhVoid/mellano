"""Immutable deployment snapshots must preserve only known precomputed runs."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sim import deployment, product


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();deployment.frozen_snapshot.cache_clear()
        self.addCleanup(deployment.frozen_snapshot.cache_clear)

    def test_frozen_mode_requires_explicit_valid_snapshot(self):
        with patch.dict(os.environ,{'OTEC_FROZEN_DEMO':'0'}):
            self.assertIsNone(deployment.frozen_snapshot())
        deployment.frozen_snapshot.cache_clear()
        payload={'schema_version':1,'product_catalog':{'runs':[]},'drone_status':{'available':False}}
        (self.root/'deployment_snapshot.json').write_text(json.dumps(payload))
        with patch.dict(os.environ,{'OTEC_FROZEN_DEMO':'1'}),patch.object(deployment,'DERIVED',self.root):
            self.assertEqual(deployment.frozen_snapshot(),payload)
            self.assertEqual(product.catalog(),payload['product_catalog'])

    def test_cached_admission_does_not_launch_or_borrow_stale_run(self):
        current='product_'+'a'*16;stale='product_'+'b'*16
        snapshot={'product_catalog':{'runs':[{'run_id':stale,'source_current':False},{'run_id':current,'source_current':True}]}}
        scenario={'module':'fire','seed':0,'building_index':1}
        with patch.object(deployment,'frozen_snapshot',return_value=snapshot),patch.object(product,'normalize',return_value=scenario),patch.object(product,'read',return_value={'scenario':scenario}),patch.object(product,'status',return_value={'run_id':current,'status':'complete'}),patch.object(product.jobs,'launch') as launch:
            self.assertEqual(product.admit(scenario)['run_id'],current);launch.assert_not_called()
        with patch.object(deployment,'frozen_snapshot',return_value=snapshot),patch.object(product,'normalize',return_value=scenario),patch.object(product,'read',return_value={'scenario':{'module':'flood'}}):
            with self.assertRaises(product.LocalComputeDisabled):product.admit(scenario)
