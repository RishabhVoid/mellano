"""Bounded streaming adapter for OpenMVS PLY visibility lists unsupported by PDAL."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

TYPES = {'char': 'b', 'int8': 'b', 'uchar': 'B', 'uint8': 'B', 'short': 'h', 'int16': 'h',
         'ushort': 'H', 'uint16': 'H', 'int': 'i', 'int32': 'i', 'uint': 'I', 'uint32': 'I',
         'float': 'f', 'float32': 'f', 'double': 'd', 'float64': 'd', 'int64': 'q', 'uint64': 'Q'}
INTEGER = set('bBhHiIqQ')


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def header(source):
    if source.readline() != b'ply\n':
        raise ValueError('Expected PLY header')
    elements, encoding = [], None
    for _ in range(10000):
        line = source.readline(65537)
        if not line or len(line) > 65536 or source.tell() > 1024 * 1024:
            raise ValueError('Invalid or oversized PLY header')
        fields = line.decode('ascii').strip().split()
        if not fields:
            continue
        if fields[0] == 'end_header':
            break
        if fields[0] in ('comment', 'obj_info'):
            continue
        if fields[0] == 'format':
            if len(fields) != 3 or fields[2] != '1.0' or fields[1] not in ('binary_little_endian', 'binary_big_endian'):
                raise ValueError('Only binary PLY1.0 is supported; source is preserved')
            encoding = fields[1]
        elif fields[0] == 'element':
            if len(fields) != 3 or int(fields[2]) < 0:
                raise ValueError('Invalid PLY element count')
            elements.append({'name': fields[1], 'count': int(fields[2]), 'properties': []})
        elif fields[0] == 'property':
            if not elements:
                raise ValueError('PLY property before element')
            if len(fields) == 3 and fields[1] in TYPES:
                elements[-1]['properties'].append({'name': fields[2], 'type': fields[1]})
            elif len(fields) == 5 and fields[1] == 'list' and fields[2] in TYPES and fields[3] in TYPES and TYPES[fields[2]] in INTEGER:
                elements[-1]['properties'].append({'name': fields[4], 'type': fields[3], 'count_type': fields[2]})
            else:
                raise ValueError('Unsupported PLY property type')
        else:
            raise ValueError('Unsupported PLY header declaration')
    else:
        raise ValueError('PLY header did not terminate')
    vertex = [element for element in elements if element['name'] == 'vertex']
    if encoding is None or len(vertex) != 1 or not vertex[0]['count']:
        raise ValueError('Expected one nonempty PLY vertex element')
    scalars = [p for p in vertex[0]['properties'] if 'count_type' not in p]
    if not {'x', 'y', 'z'}.issubset(p['name'] for p in scalars):
        raise ValueError('PLY vertex XYZ scalar properties required')
    return encoding, elements, vertex[0], scalars


def scalar_ply(source_path, output_path, progress=None):
    """Preserve every scalar byte/order; drop only non-scalar vertex properties/elements."""
    source_path, output_path = Path(source_path), Path(output_path)
    if source_path.resolve() == output_path.resolve():
        raise ValueError('Original dense cloud must be preserved')
    source_hash = checksum(source_path)
    metadata_path = output_path.with_suffix('.json')
    if output_path.is_file():
        if not metadata_path.is_file():
            raise ValueError('Existing scalar PLY has no provenance; preserve and inspect it')
        metadata = json.loads(metadata_path.read_text())
        if metadata.get('source_sha256') != source_hash or metadata.get('output_sha256') != checksum(output_path):
            raise ValueError('Scalar PLY provenance mismatch; source and output preserved')
        return metadata
    temporary = output_path.with_suffix('.ply.part')
    if temporary.exists():
        raise ValueError('Interrupted scalar PLY preserved; inspect before retry')
    started = datetime.now(timezone.utc).isoformat()
    size = source_path.stat().st_size
    with source_path.open('rb', buffering=1024*1024) as source:
        encoding, elements, vertex, scalars = header(source)
        endian = '<' if encoding == 'binary_little_endian' else '>'
        lines = ['ply', f'format {encoding} 1.0', 'comment Scalar-only adapter; original OpenMVS cloud preserved',
                 f'element vertex {vertex["count"]}', *[f'property {p["type"]} {p["name"]}' for p in scalars], 'end_header', '']
        output_digest, records = hashlib.sha256(), 0
        with temporary.open('wb', buffering=1024*1024) as output:
            initial = '\n'.join(lines).encode('ascii'); output.write(initial); output_digest.update(initial)
            buffered = bytearray()
            for element in elements:
                # Consecutive scalar properties become one read, derived from actual types.
                plan = []
                for prop in element['properties']:
                    if 'count_type' in prop:
                        plan.append(('list', struct.Struct(endian + TYPES[prop['count_type']]), struct.calcsize(endian + TYPES[prop['type']])))
                    elif plan and plan[-1][0] == 'scalar':
                        plan[-1] = ('scalar', plan[-1][1] + struct.calcsize(endian + TYPES[prop['type']]))
                    else:
                        plan.append(('scalar', struct.calcsize(endian + TYPES[prop['type']])))
                for _ in range(element['count']):
                    for operation in plan:
                        if operation[0] == 'scalar':
                            payload = source.read(operation[1])
                            if len(payload) != operation[1]:
                                raise ValueError('Truncated PLY scalar record')
                            if element['name'] == 'vertex':
                                buffered.extend(payload)
                        else:
                            count_bytes = source.read(operation[1].size)
                            if len(count_bytes) != operation[1].size:
                                raise ValueError('Truncated PLY list count')
                            count = operation[1].unpack(count_bytes)[0]
                            byte_count = count * operation[2]
                            if count < 0 or source.tell() + byte_count > size:
                                raise ValueError('Invalid or truncated PLY list payload')
                            source.seek(byte_count, 1)
                    if element['name'] == 'vertex':
                        records += 1
                        if len(buffered) >= 1024 * 1024:
                            output.write(buffered); output_digest.update(buffered); buffered.clear()
                        if progress and records % 1000000 == 0:
                            progress(records, vertex['count'])
            output.write(buffered); output_digest.update(buffered)
    if records != vertex['count']:
        raise ValueError('Scalar PLY vertex count mismatch')
    temporary.replace(output_path)
    metadata = {'method': 'Stream parsed PLY binary element/property declarations; preserve scalar bytes and vertex order, skip declared list lengths. No coordinate transform, filtering, or reconstructed point replacement.',
        'source': str(source_path), 'source_sha256': source_hash, 'output': str(output_path),
        'output_sha256': output_digest.hexdigest(), 'vertex_count': records, 'scalar_properties': scalars,
        'removed_vertex_lists': [p for p in vertex['properties'] if 'count_type' in p],
        'removed_other_elements': [e['name'] for e in elements if e['name'] != 'vertex'],
        'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(), 'source_preserved': True,
        'memory_policy': '1MiB input/output buffers plus at most1MiB scalar staging; list payloads skipped without allocation.'}
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
    return metadata


def sampled_preview(source_path, output_path, maximum_points=100000):
    """Deterministic actual-point subset for browser display, never used for metrics."""
    source_path, output_path = Path(source_path), Path(output_path)
    if maximum_points < 1 or maximum_points > 100000:
        raise ValueError('Preview point limit must be between1and100000')
    if source_path.resolve() == output_path.resolve():
        raise ValueError('Full-resolution cloud must be preserved')
    source_hash = checksum(source_path)
    with source_path.open('rb', buffering=1024*1024) as source:
        encoding, elements, vertex, scalars = header(source)
        if len(elements) != 1 or len(scalars) != len(vertex['properties']):
            raise ValueError('Preview input must be verified scalar-only vertexPLY')
        stride = max(1, (vertex['count']+maximum_points-1)//maximum_points)
        count = (vertex['count']+stride-1)//stride
        offset = source.tell()
        record_size = sum(struct.calcsize(('<' if encoding == 'binary_little_endian' else '>')+TYPES[p['type']]) for p in scalars)
        if source_path.stat().st_size != offset+record_size*vertex['count']:
            raise ValueError('Scalar cloud length does not match declared vertexcount')
        lines = ['ply', f'format {encoding} 1.0', f'comment Display-only sampled preview: every{stride}th actual vertex from{vertex["count"]} points',
                 f'element vertex {count}', *[f'property {p["type"]} {p["name"]}' for p in scalars], 'end_header', '']
        temporary = output_path.with_suffix('.ply.part')
        digest = hashlib.sha256()
        with temporary.open('wb', buffering=1024*1024) as output:
            initial = '\n'.join(lines).encode();output.write(initial);digest.update(initial)
            for index in range(0, vertex['count'], stride):
                source.seek(offset+index*record_size)
                data = source.read(record_size)
                if len(data) != record_size:
                    raise ValueError('Truncated sampled source')
                output.write(data);digest.update(data)
    temporary.replace(output_path)
    metadata = {'source': str(source_path), 'source_sha256': source_hash, 'source_point_count': vertex['count'],
        'preview_point_count': count, 'sampling_stride': stride, 'output_sha256': digest.hexdigest(),
        'method': 'Deterministic every-Nth actual vertex in original order. Display only; full-resolution data and all metrics unchanged.',
        'generated_at': datetime.now(timezone.utc).isoformat(), 'full_source_preserved': True}
    output_path.with_suffix('.json').write_text(json.dumps(metadata, indent=2)+'\n')
    return metadata
