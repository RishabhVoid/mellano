"""Isolated scientific/data-integrity tests; no fixture enters production data."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from sim import sentinel


class SentinelTests(unittest.TestCase):
    def test_earthengine_credential_copies_preserve_cli_broker(self):
        try:
            import google.oauth2.credentials  # Optional worker dependency.
        except ImportError:
            self.skipTest('Run with --with earthengine-api for credential integration')
        expiry = sentinel.datetime.now(sentinel.timezone.utc).replace(tzinfo=None) + sentinel.timedelta(minutes=50)
        with patch.object(sentinel, 'gcloud_access_token', return_value=('isolated-fixture-token-value', expiry)) as broker:
            credentials = sentinel.ee_credentials(None, 'fixture-project', 'otec')
            copied = credentials.with_quota_project('fixture-quota')
            copied.refresh(None)
            second = copied.with_quota_project('fixture-quota-2')
        broker.assert_called_once()
        self.assertIsNotNone(second.refresh_handler)
        self.assertEqual(second.expiry, expiry)
        self.assertEqual(second.token, 'isolated-fixture-token-value')

    def test_gcloud_refresh_uses_named_configuration_and_no_output_logging(self):
        result = Mock(returncode=0, stdout='isolated-fixture-token-value\n')
        with patch.object(sentinel.subprocess, 'run', return_value=result) as run:
            token, expiry = sentinel.gcloud_access_token('otec')
        self.assertEqual(token, 'isolated-fixture-token-value')
        self.assertIsNone(expiry.tzinfo)
        self.assertGreater((expiry - sentinel.datetime.now(sentinel.timezone.utc).replace(tzinfo=None)).total_seconds(), 2990)
        self.assertEqual(run.call_args.args[0], ['gcloud', '--configuration=otec', 'auth', 'print-access-token', '--quiet'])
        self.assertEqual(run.call_args.kwargs['stdout'], sentinel.subprocess.PIPE)
        self.assertEqual(run.call_args.kwargs['stderr'], sentinel.subprocess.DEVNULL)
        result.returncode = 1
        result.stdout = 'sensitive-fixture-failure'
        with patch.object(sentinel.subprocess, 'run', return_value=result):
            with self.assertRaisesRegex(RuntimeError, '^Verified gcloud configuration token refresh unavailable$'):
                sentinel.gcloud_access_token('otec')
        with self.assertRaises(ValueError):
            sentinel.gcloud_access_token('--wrong')

    def test_gate_preserves_verified_gcloud_configuration_on_worker(self):
        with patch.object(sentinel, 'state', return_value={'status': 'needs_input'}), patch.object(sentinel, 'active', return_value=False), patch.object(sentinel, 'project_access', return_value=(True, 'ready')) as probe, patch.object(sentinel, 'update'), patch.object(sentinel, 'launch', return_value={'launched': True}) as launch:
            sentinel.wait_project('fixture-project', 'otec')
        probe.assert_called_once_with('fixture-project', 'otec')
        self.assertEqual(launch.call_args.args[1][-2:], ['--gcloud-configuration', 'otec'])

    def test_access_gate_waits_and_launches_once_after_readiness(self):
        with patch.object(sentinel, 'state', return_value={'status': 'needs_input'}), patch.object(sentinel, 'active', return_value=False), patch.object(sentinel, 'project_access', side_effect=[(False, 'Earth Engine API enablement pending'), (True, 'ready')]) as probe, patch.object(sentinel.time, 'sleep') as sleep, patch.object(sentinel, 'update') as status, patch.object(sentinel, 'launch', return_value={'launched': True}) as launch:
            self.assertEqual(sentinel.wait_project('fixture-project'), 0)
        self.assertEqual(probe.call_count, 2)
        sleep.assert_called_once_with(60)
        launch.assert_called_once_with('job_a', ['uv', 'run', '--frozen', '--with', 'earthengine-api', 'python', '-u', '-m', 'sim.sentinel', 'run', '--ee-project', 'fixture-project'], detail='Earth Engine project ready; acquiring real before/peak/recession scenes')
        self.assertIn('enablement pending', status.call_args_list[0].args[1])

    def test_access_gate_does_not_duplicate_running_worker(self):
        with patch.object(sentinel, 'state', return_value={'status': 'running'}), patch.object(sentinel, 'active', return_value=True), patch.object(sentinel, 'project_access') as probe, patch.object(sentinel, 'launch') as launch, patch.object(sentinel, 'update'):
            self.assertEqual(sentinel.wait_project('fixture-project'), 0)
        probe.assert_not_called()
        launch.assert_not_called()

    def test_expired_noncommercial_eligibility_is_not_reported_as_oauth_failure(self):
        client = Mock()
        client.Initialize.side_effect = RuntimeError('Project fixture noncommercial Earth Engine access has expired. Sensitive upstream details must not escape.')
        with patch.dict('sys.modules', {'ee': client}):
            ready, reason = sentinel.project_access('fixture-project')
        self.assertFalse(ready)
        self.assertEqual(reason, 'Earth Engine noncommercial eligibility expired; reverify this project in Earth Engine configuration')
        self.assertNotIn('Sensitive', reason)

    def test_access_probe_sets_deadline_only_after_client_initialization(self):
        client = Mock()
        client.Number.return_value.getInfo.return_value = 1
        with patch.dict('sys.modules', {'ee': client}):
            self.assertEqual(sentinel.project_access('fixture-project'), (True, 'ready'))
        methods = [call[0] for call in client.mock_calls]
        self.assertLess(methods.index('Initialize'), methods.index('data.setDeadline'))

    def test_access_probe_reloads_credentials_and_sanitizes_errors(self):
        client = Mock()
        client.Initialize.side_effect = RuntimeError('API has not been used or is disabled; sensitive-upstream-content')
        with patch.dict('sys.modules', {'ee': client}):
            ready, reason = sentinel.project_access('fixture-project')
        self.assertFalse(ready)
        self.assertEqual(reason, 'Earth Engine API enablement pending')
        client.Reset.assert_called_once()
        client.data.get_persistent_credentials.assert_called_once()
        self.assertNotIn('sensitive', reason)

    def test_linear_power_conversion_and_median_speckle(self):
        data = np.full((11, 11), .01, dtype='float32')
        data[5, 5] = 100
        result = sentinel.linear_to_filtered_db(data)
        np.testing.assert_allclose(result, -20, atol=1e-5)

    def test_nodata_is_unknown_and_never_a_dark_water_sample(self):
        data = np.full((15, 15), .01, dtype='float32')
        data[7, 7] = 0
        result = sentinel.linear_to_filtered_db(data)
        self.assertTrue(np.isnan(result[7, 7]))
        self.assertTrue(np.isnan(result[7, 10]))
        self.assertAlmostEqual(float(result[7, 11]), -20, places=4)

    def test_threshold_tunes_before_only_and_ties_are_deterministic(self):
        data = np.array([[-20, -20, -10, -10]], dtype='float32')
        reference = np.array([[True, True, False, False]])
        best, curve = sentinel.tune_threshold(data, reference, np.ones(data.shape, bool))
        self.assertEqual(best['iou'], 1)
        self.assertEqual(best['threshold_db'], -19.75)
        self.assertEqual(len(curve), 101)
        with self.assertRaises(ValueError):
            sentinel.tune_threshold(data, np.zeros(data.shape, bool), np.ones(data.shape, bool))

    def test_same_orbit_preferred_only_when_coverage_is_valid(self):
        same = {'orbit': 27, 'coverage_fraction': .99, 'time': '2023-07-16'}
        other = {'orbit': 136, 'coverage_fraction': 1., 'time': '2023-07-12'}
        self.assertIs(sentinel.select_scene([other, same], 27), same)
        same['coverage_fraction'] = .1
        self.assertIs(sentinel.select_scene([other, same], 27), other)

    def test_requested_windows_are_not_silently_extended(self):
        calls = []
        def query(client, url, **kwargs):
            calls.append(kwargs['params']['datetime'])
            return {'features': []}
        with tempfile.TemporaryDirectory() as directory, patch.object(sentinel, 'CACHE', Path(directory)), patch.object(sentinel, 'get_json', side_effect=query), patch.object(sentinel, 'note'):
            sentinel.discover(None)
        self.assertEqual(calls[0], '2023-07-01T00:00:00Z/2023-07-06T23:59:59Z')
        self.assertEqual(calls[1], '2023-07-12T00:00:00Z/2023-07-18T23:59:59Z')
        self.assertEqual(calls[2], '2023-07-20T00:00:00Z/2023-07-26T23:59:59Z')

    def test_classification_preserves_nodata_dates_and_source_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);derived = root / 'derived';derived.mkdir();cache = root / 'raw';cache.mkdir()
            profile = {'driver': 'GTiff', 'height': 35, 'width': 35, 'count': 1, 'dtype': 'float32', 'crs': 'EPSG:32643', 'transform': from_origin(714880, 3179180, 10, 10)}
            river = np.zeros((35, 35), bool);river[:, :10] = True
            sentinel.save_raster(derived / 'aoi_mask.tif', np.ones((35, 35), dtype='uint8'), profile, 0)
            scenes = {}
            for period, width, date, orbit in [('before', 10, '2023-07-04T12:55:20Z', 27), ('peak', 16, '2023-07-16T12:55:21Z', 27), ('recession', 12, '2023-07-24T00:52:34Z', 136)]:
                values = np.full((35, 35), .1, dtype='float32');values[:, :width] = .01
                if period == 'peak':values[20, 20] = -9999
                path = cache / (period + '.tif');sentinel.save_raster(path, values, profile, -9999)
                scenes[period] = [{'id': period, 'time': date, 'orbit': orbit, 'direction': 'fixture', 'file': str(path), 'coverage_fraction': 1.}]
            with patch.object(sentinel, 'DERIVED', derived), patch.object(sentinel, 'CACHE', cache), patch.object(sentinel, 'river_mask', return_value=(river, {'source': 'isolated fixture'})), patch.object(sentinel, 'note'):
                meta = sentinel.classify(scenes, profile, 'isolated test fixture')
            self.assertFalse(meta['independent_ground_truth'])
            self.assertIn('OSM-tuned', meta['classification'])
            self.assertEqual(meta['before_dates'], ['2023-07-04T12:55:20Z'])
            self.assertTrue(any('recession orbit' in warning for warning in meta['warnings']))
            with rasterio.open(derived / 's1_flood_new.tif') as raster:
                result = raster.read(1)
                self.assertEqual(raster.nodata, 255)
                self.assertEqual(raster.transform, profile['transform'])
                self.assertEqual(result[20, 20], 255)
                self.assertTrue(np.any(result == 1))
            self.assertEqual(json.loads((derived / 's1_meta.json').read_text())['threshold_db'], meta['threshold_db'])

    def test_missing_before_refuses_to_emit_masks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            profile = {'driver': 'GTiff', 'height': 2, 'width': 2, 'count': 1, 'dtype': 'float32', 'crs': 'EPSG:32643', 'transform': from_origin(0, 20, 10, 10)}
            sentinel.save_raster(path / 'aoi_mask.tif', np.ones((2, 2), dtype='uint8'), profile, 0)
            with patch.object(sentinel, 'DERIVED', path):
                with self.assertRaisesRegex(ValueError, 'before window'):
                    sentinel.classify({'before': [], 'peak': [], 'recession': []}, profile, 'fixture')
            self.assertFalse((path / 's1_water_before.tif').exists())


if __name__ == '__main__':
    unittest.main()
