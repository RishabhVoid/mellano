"""Pre-cache published frame summaries without running a simulation or loading secrets.

Run: uv run --frozen --no-env-file python -m sim.briefing_cache
"""
import argparse
import json

from sim import gemini_api
from sim.product import write


def cache_frames(live=False):
    from sim.decision_support import fallback_recommendations, generate_recommendations
    catalog = gemini_api.read_document(gemini_api.PRODUCT / 'catalog.json')
    latest = {record['module']: record['run_id'] for record in catalog.get('runs', []) if isinstance(record, dict)}
    count = 0
    for record in catalog.get('runs', []):
        run_id = record['run_id'] if isinstance(record, dict) else record
        encoded, _ = gemini_api.briefing_context(run_id, 0)
        manifest = gemini_api.read_document(gemini_api.PRODUCT / 'runs' / run_id / 'manifest.json')
        for index in sorted({0, len(manifest['frames']) - 1}):
            encoded, refs = gemini_api.briefing_context(run_id, index)
            context = json.loads(encoded)
            packet = gemini_api.run_decision_packet(run_id, index)
            advisory = generate_recommendations(packet) if live and index == 0 and latest.get(record.get('module')) == run_id else fallback_recommendations(packet, 'precomputed')
            metrics = context['frame_metrics']
            facts = []
            for key, unit in [('affected_area_km2', 'km² affected land'), ('roads_closed', 'modelled road closures')]:
                value = metrics.get(key)
                if type(value) in (int, float):
                    facts.append(f'{value:g} {unit}')
            text = 'Simulated frame. ' + '; '.join(facts) + '. '
            text += 'Hypothetical, not a forecast. Facility availability and actual road access require human verification. Missing population or satellite evidence remains unavailable.'
            payload = {'run_id': run_id, 'frame_index': index, 'text': text,
                       'engine': advisory['engine'], 'label': advisory['label'], 'advisory': advisory,
                       'source_files': refs, 'context_sha256': gemini_api.context_digest(encoded),
                       'evidence_sha256': gemini_api.evidence_digest(packet)}
            if advisory['engine'] == 'gemini': payload['text'] = advisory['summary']
            write(gemini_api.PRODUCT / 'runs' / run_id / 'briefings' / f'frame_{index}.json', payload)
            print(f'{run_id} frame {index}: {advisory["engine"]}', flush=True)
            count += 1
    return count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Generate Gemini advice for the latest run of each module; makes paid provider calls')
    args = parser.parse_args()
    print(f'Cached {cache_frames(args.live)} published frames; no simulations called.')
