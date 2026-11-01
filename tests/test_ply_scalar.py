import json
from pathlib import Path
import struct
import tempfile
import unittest

from sim.ply_scalar import checksum, sampled_preview, scalar_ply


class PlyScalarTests(unittest.TestCase):
    def fixture(self, path, endian='<'):
        encoding = 'binary_little_endian' if endian == '<' else 'binary_big_endian'
        header = f'ply\nformat {encoding} 1.0\nelement vertex 2\nproperty double x\nproperty list ushort int16 observations\nproperty float y\nproperty uint8 red\nproperty list uint8 float32 weights\nproperty float z\nelement face 1\nproperty list uchar uint vertex_indices\nend_header\n'
        data = bytearray(header.encode())
        for x, y, z, obs, weights in [(1.25, 2.5, 3.75, [4, 9], [.2, .8]), (5., 6., 7., [], [.3])]:
            data.extend(struct.pack(endian+'dH', x, len(obs)))
            data.extend(struct.pack(endian+str(len(obs))+'h', *obs))
            data.extend(struct.pack(endian+'fBB', y, 128, len(weights)))
            data.extend(struct.pack(endian+str(len(weights))+'f', *weights))
            data.extend(struct.pack(endian+'f', z))
        data.extend(struct.pack(endian+'BIII', 3, 0, 1, 0))
        path.write_bytes(data)

    def test_actual_types_interleaved_lists_endianness_and_preservation(self):
        for endian in ['<', '>']:
            with self.subTest(endian=endian), tempfile.TemporaryDirectory() as directory:
                source, output = Path(directory)/'source.ply', Path(directory)/'scalar.ply'
                self.fixture(source, endian); before = source.read_bytes()
                meta = scalar_ply(source, output)
                result = output.read_bytes().split(b'end_header\n', 1)[1]
                expected = struct.pack(endian+'dfBfdfBf', 1.25, 2.5, 128, 3.75, 5., 6., 128, 7.)
                self.assertEqual(result, expected)
                self.assertEqual(source.read_bytes(), before)
                self.assertEqual(meta['vertex_count'], 2)
                self.assertEqual(meta['output_sha256'], checksum(output))
                self.assertEqual(meta['removed_other_elements'], ['face'])
                self.assertEqual(scalar_ply(source, output), meta)

    def test_truncated_list_fails_without_publishing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'source.ply', Path(directory)/'scalar.ply'
            self.fixture(source); source.write_bytes(source.read_bytes()[:-5])
            with self.assertRaisesRegex(ValueError, 'truncated'):
                scalar_ply(source, output)
            self.assertFalse(output.exists())
            self.assertTrue(source.exists())

    def test_changed_source_cannot_reuse_adapter_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, output = Path(directory)/'source.ply', Path(directory)/'scalar.ply'
            self.fixture(source); scalar_ply(source, output)
            source.write_bytes(source.read_bytes()+b'changed')
            with self.assertRaisesRegex(ValueError, 'provenance mismatch'):
                scalar_ply(source, output)

    def test_original_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'source.ply'; self.fixture(path); before = path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'preserved'):
                scalar_ply(path, path)
            self.assertEqual(path.read_bytes(), before)

    def test_preview_contains_only_deterministic_original_scalar_records(self):
        with tempfile.TemporaryDirectory() as directory:
            source, scalar, preview = [Path(directory)/name for name in ['source.ply','scalar.ply','preview.ply']]
            self.fixture(source);scalar_ply(source,scalar);before = scalar.read_bytes()
            meta = sampled_preview(scalar, preview, maximum_points=1)
            self.assertEqual(meta['source_point_count'],2)
            self.assertEqual(meta['preview_point_count'],1)
            self.assertEqual(meta['sampling_stride'],2)
            data = preview.read_bytes().split(b'end_header\n',1)[1]
            self.assertEqual(data,struct.pack('<dfBf',1.25,2.5,128,3.75))
            self.assertEqual(scalar.read_bytes(),before)
            first_hash = meta['output_sha256']
            self.assertEqual(sampled_preview(scalar,preview,1)['output_sha256'],first_hash)


if __name__ == '__main__':
    unittest.main()
