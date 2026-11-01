"""Audit actual edit influence without changing any production run or physics."""
from datetime import datetime, timezone
import hashlib
import json
import time

import numpy as np
import rasterio
from rasterio.features import rasterize
from scipy.ndimage import binary_dilation, distance_transform_edt
from pyproj import Transformer
from shapely.geometry import shape
from sim.config import DERIVED, RUNS, CRS
from sim.data import write_json
from sim.dynamic import DynamicConfig, _apply_edits
from sim.geometry import transform_xy
from sim.jobs import update
from sim.relative import RelativePipes, river_reference

JOB = 'relative_validation'
BASELINE = 'relative_river_plus_1m'
EDITED = ('relative_breach_near_ito', 'relative_debris_ring_road')


def read(path):
    return json.loads(path.read_text())


def check_inputs(manifest):
    for name, expected in manifest['input_sha256'].items():
        if hashlib.sha256((DERIVED / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Run input changed: {name}; cannot reconstruct this run')


def saved_audit(baseline, candidate, changed, shape):
    if baseline['parameters'] != candidate['parameters'] or baseline['relative_stage_m'] != candidate['relative_stage_m'] or baseline['input_sha256'] != candidate['input_sha256']:
        raise ValueError('Comparison runs do not share inputs and forcing')
    if [frame['elapsed_s'] for frame in baseline['frames']] != [frame['elapsed_s'] for frame in candidate['frames']]:
        raise ValueError('Comparison frame times differ')
    frames = [];maximum_baseline = np.zeros(shape, dtype='float32')
    for a, b in zip(baseline['frames'], candidate['frames']):
        with rasterio.open(RUNS / BASELINE / a['depth_file']) as source:
            original = source.read(1)
        with rasterio.open(RUNS / candidate['run_id'] / b['depth_file']) as source:
            edited = source.read(1)
        if original.shape != shape or edited.shape != shape or not np.isfinite(original).all() or not np.isfinite(edited).all():
            raise ValueError('Invalid saved frame')
        np.maximum(maximum_baseline, original, out=maximum_baseline)
        difference = np.abs(edited - original)
        frames.append({'elapsed_s': a['elapsed_s'], 'bitwise_equal': bool(np.array_equal(original, edited)), 'different_cells': int(np.count_nonzero(difference)), 'max_absolute_difference_m': float(difference.max()), 'max_baseline_depth_on_edit_m': float(original[changed].max()), 'max_edited_depth_on_edit_m': float(edited[changed].max())})
    return {'baseline_run': BASELINE, 'all_saved_frames_bitwise_equal': all(frame['bitwise_equal'] for frame in frames), 'saved_frame_count': len(frames), 'frames': frames, 'minimum_distance_to_saved_baseline_water_m': float(distance_transform_edt(~(maximum_baseline > 1e-6))[changed].min() * candidate['parameters']['cell_size_m'])}


def audit():
    started = time.perf_counter()
    baseline = read(RUNS / BASELINE / 'manifest.json');check_inputs(baseline)
    report = {'status': 'running', 'label': 'Numerical audit of hypothetical runs; not validation against observed floods', 'created_at': datetime.now(timezone.utc).isoformat(), 'baseline_run': BASELINE, 'scenarios': {}}
    with rasterio.open(DERIVED / 'ground_10m.tif') as reference:
        ground = reference.read(1);transform_grid = reference.transform
        def aligned(name):
            with rasterio.open(DERIVED / name) as source:
                if source.shape != reference.shape or source.transform != reference.transform or source.crs != reference.crs:
                    raise ValueError('Misaligned input: ' + name)
                return source.read(1)
        surface = aligned('surface_10m.tif');valid = aligned('aoi_mask.tif').astype(bool);blocked = aligned('building_mask.tif').astype(bool);banks = aligned('embankment_mask.tif').astype(bool)
        project = Transformer.from_crs('EPSG:4326', CRS, always_xy=True).transform
        water = rasterize([(transform_xy(project, shape(feature['geometry'])), 1) for feature in read(DERIVED / 'osm_water.geojson')['features']], out_shape=ground.shape, transform=transform_grid, dtype='uint8').astype(bool)
    river, level_reference, _ = river_reference(ground, water, valid)
    for run_id in EDITED:
        manifest = read(RUNS / run_id / 'manifest.json');check_inputs(manifest)
        config = DynamicConfig(**manifest['parameters']);config.validate()
        bed, changed = _apply_edits(surface, ground, banks, valid, transform_grid, manifest['edits'])
        if not changed.any():
            raise ValueError('Edit mask is empty')
        details = saved_audit(baseline, manifest, changed, ground.shape)
        details.update({'input_hashes_match': True, 'forcing_and_parameters_match': True, 'edit_cells': int(changed.sum()), 'physically_altered_bed_cells': int(np.count_nonzero((bed != surface) & changed)), 'blocked_edit_cells': int((changed & blocked).sum()), 'mapped_water_edit_cells': int((changed & water).sum()), 'bed_change_min_m': float((bed - surface)[changed].min()), 'bed_change_max_m': float((bed - surface)[changed].max()), 'original_surface_range_m': [float(surface[changed].min()), float(surface[changed].max())], 'edited_bed_range_m': [float(bed[changed].min()), float(bed[changed].max())]})
        report['scenarios'][run_id] = details;write_json(RUNS / 'relative_validation.json', report)
        initial = np.where(river & valid & ~blocked, np.maximum(0, level_reference - surface), 0)
        boundary = river & valid & ~blocked & ~changed
        solver = RelativePipes(bed, initial, valid, blocked, boundary, config, reference=level_reference);solver.impose_stage(0)
        neighbourhood = binary_dilation(changed, structure=np.ones((3, 3), bool)) & valid & ~blocked
        faces_x = changed[:, :-1] | changed[:, 1:];faces_y = changed[:-1, :] | changed[1:, :]
        actual_max = float(solver.depth[changed].max());neighbour_max = float(solver.depth[neighbourhood].max()) if neighbourhood.any() else 0.;flux_max = 0.;wet_steps = 0;reconstructed_equal = True;max_reconstruction_error = 0.
        steps = round(config.duration_s / config.dt_s);frame_steps = {round(frame['elapsed_s'] / config.dt_s): frame for frame in manifest['frames']}
        for step in range(steps + 1):
            if step:
                solver.step(manifest['relative_stage_m'] * min(1, step * config.dt_s / config.river_ramp_s))
            depth_on_edit = float(solver.depth[changed].max());actual_max = max(actual_max, depth_on_edit)
            wet_steps += int(depth_on_edit > 1e-6)
            if neighbourhood.any():neighbour_max = max(neighbour_max, float(solver.depth[neighbourhood].max()))
            flux_max = max(flux_max, float(np.abs(solver.qx[faces_x]).max()), float(np.abs(solver.qy[faces_y]).max()))
            if step in frame_steps:
                frame = frame_steps[step]
                computed = np.where(solver.depth > 1e-6, np.maximum(0, solver.bed + solver.depth - ground), 0).astype('float32')
                with rasterio.open(RUNS / run_id / frame['depth_file']) as source:saved = source.read(1)
                reconstructed_equal &= bool(np.array_equal(computed, saved))
                max_reconstruction_error = max(max_reconstruction_error, float(np.abs(computed - saved).max()))
                update(JOB, f'{run_id}: verifying true liquid depth at every 0.25s; {step}/{steps} steps')
        details.update({'internal_steps_checked': steps + 1, 'internal_time_step_s': config.dt_s, 'maximum_true_liquid_depth_on_edit_m': actual_max, 'maximum_true_liquid_depth_in_1cell_neighbourhood_m': neighbour_max, 'timesteps_with_edit_depth_above_1e_6_m': wet_steps, 'maximum_absolute_flux_on_edit_faces_m2_s': flux_max, 'all_reconstructed_frames_bitwise_equal_to_saved': reconstructed_equal, 'maximum_reconstruction_error_m': max_reconstruction_error, 'reconstructed_water_balance': solver.water_balance()})
        details['conclusion'] = ('Selected edit cells stay numerically dry at every solver step; all saved depths equal the unedited +1 m run. No breach/debris effect is demonstrated by this 300-second experiment.' if actual_max == 0 and details['all_saved_frames_bitwise_equal'] else 'Edit cells or depth fields differ; inspect numerical evidence before attributing an effect.')
        write_json(RUNS / 'relative_validation.json', report)
        print(json.dumps({'run_id': run_id, 'actual_edit_depth_max_m': actual_max, 'frames_equal_to_baseline': details['all_saved_frames_bitwise_equal'], 'reconstructed_equal': reconstructed_equal}), flush=True)
        if not reconstructed_equal:
            raise ValueError('Run reconstruction differs from saved depths')
    report.update(status='complete', completed_at=datetime.now(timezone.utc).isoformat(), runtime_s=time.perf_counter() - started)
    write_json(RUNS / 'relative_validation.json', report)
    update(JOB, 'Both edit masks audited at every solver step; exact saved-depth comparison and true-liquid-depth report complete')
    return report


if __name__ == '__main__':
    audit()
