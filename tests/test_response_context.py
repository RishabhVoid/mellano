import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import tempfile

import numpy as np
from rasterio.transform import from_origin

from sim.response_context import BoundedRangeFile, build_graph, conservative_population, directions, download_population, speed


class Identity:
    def transform(self, x, y):
        return x, y


def way(identifier, ids, coords, **tags):
    return {'type': 'way', 'id': identifier, 'nodes': ids,
            'geometry': [{'lon': x, 'lat': y} for x, y in coords],
            'tags': {'highway': 'residential', **tags}}


class ResponseContextTests(unittest.TestCase):
    def test_crossing_bridge_does_not_create_intersection(self):
        graph = build_graph([way(1, [10, 11], [(0, -5), (10, -5)]),
                             way(2, [20, 21], [(5, 0), (5, -10)], bridge='yes', layer='1')],
                            [0, 0], (0, 0, 10, 10), Identity())
        self.assertEqual(graph['meta']['node_count'], 4)
        self.assertEqual(graph['meta']['weak_component_count'], 2)
        self.assertEqual(graph['meta']['directed_edge_count'], 4)

    def test_shared_nodes_and_reverse_oneway(self):
        graph = build_graph([way(1, [10, 11], [(0, -5), (5, -5)], oneway='-1'),
                             way(2, [11, 12], [(5, -5), (10, -5)])],
                            [0, 0], (0, 0, 10, 10), Identity())
        self.assertEqual(graph['meta']['weak_component_count'], 1)
        self.assertEqual(graph['edges'][0]['from'], 'osm:11')
        self.assertEqual(graph['edges'][0]['to'], 'osm:10')
        self.assertEqual(len(graph['edges']), 3)

    def test_boundary_clips_never_join_unrelated_roads(self):
        graph = build_graph([way(1, [10, 11], [(-5, -5), (5, -5)]),
                             way(2, [20, 21], [(-5, -5), (5, -5)])],
                            [0, 0], (0, 0, 10, 10), Identity())
        self.assertEqual(graph['meta']['weak_component_count'], 2)
        self.assertTrue(all(edge['length_m'] == 5 for edge in graph['edges']))

    def test_speeds_and_access_are_explicit(self):
        value, source = speed({'highway': 'primary', 'maxspeed': '30 mph'}, 'forward')
        self.assertAlmostEqual(value, 48.28032)
        self.assertIn('OSM', source)
        self.assertIn('assumed', speed({'highway': 'residential'}, 'forward')[1])
        self.assertEqual(directions({'highway': 'motorway'}), [1])
        self.assertEqual(directions({'highway': 'motorway', 'oneway': 'no'}), [1, -1])
        self.assertEqual(directions({'highway': 'primary', 'oneway': 'reversible'}), [])

    def test_population_count_conservation_and_partial_boundary(self):
        src = np.ma.array([[100.]], mask=False)
        dest, meta = conservative_population(src, from_origin(0, 20, 20, 20), 'EPSG:32643',
                                            from_origin(0, 20, 10, 10), (2, 2))
        np.testing.assert_allclose(dest, 25.)
        self.assertAlmostEqual(float(dest.sum()), 100.)
        self.assertAlmostEqual(meta['expected_valid_population'], 100.)
        clipped, _ = conservative_population(src, from_origin(0, 20, 20, 20), 'EPSG:32643',
                                             from_origin(10, 20, 10, 10), (2, 1))
        self.assertAlmostEqual(float(clipped.sum()), 50.)

    def test_population_nodata_never_becomes_zero_population(self):
        src = np.ma.array([[100., 200.]], mask=[[False, True]])
        dest, meta = conservative_population(src, from_origin(0, 10, 10, 10), 'EPSG:32643',
                                            from_origin(0, 10, 10, 10), (1, 2))
        self.assertEqual(dest[0, 0], 100.)
        self.assertTrue(np.isnan(dest[0, 1]))
        self.assertEqual(meta['coverage_fraction'], .5)

    def test_http_server_ignoring_range_is_rejected_before_body_read(self):
        session = MagicMock()
        response = session.get.return_value.__enter__.return_value
        response.status_code = 200
        with self.assertRaisesRegex(OSError, 'full country download refused'):
            BoundedRangeFile('https://example.test/population.tif', session)
        response.raw.read.assert_not_called()

    def test_partial_full_download_is_preserved_if_resume_not_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = Path(directory)
            partial = raw / 'worldpop_2020_india.tif.part'
            partial.write_bytes(b'partial-source')
            session = MagicMock()
            response = session.get.return_value.__enter__.return_value
            response.status_code = 200
            response.headers = {'Content-Length': '10000'}
            with patch('sim.response_context.RAW', raw), patch('sim.response_context.DERIVED', raw), \
                 patch('sim.response_context.grid_contract', return_value={}), \
                 patch('sim.response_context.shutil.disk_usage', return_value=MagicMock(free=10*1024**3)), \
                 patch('sim.response_context.requests.Session', return_value=session), \
                 patch('sim.response_context.update') as update:
                self.assertEqual(download_population(), 2)
                self.assertEqual(partial.read_bytes(), b'partial-source')
                response.iter_content.assert_not_called()
                self.assertEqual(update.call_args.kwargs['status'], 'blocked')


if __name__ == '__main__':
    unittest.main()
