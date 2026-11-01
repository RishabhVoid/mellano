"""Briefing boundary tests use synthetic isolated files, never real credentials."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sim import gemini_api as module
from sim.gemini import Config, GeminiError


class BriefingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run_id = "product_" + "a" * 16
        self.directory = self.root / "runs" / self.run_id
        self.directory.mkdir(parents=True)
        (self.root / "catalog.json").write_text(json.dumps({"runs": [{"run_id": self.run_id}]}))
        documents = {
            "manifest.json": {"run_id": self.run_id, "frames": [{"time_s": 0}], "hypothetical": True, "calibrated": False},
            "metrics.json": {"people_affected": None, "affected_area_km2": 0.42},
            "metrics_0000.json": {"people_affected": None, "roads_closed": 2},
            "response_0000.json": {"units_dispatched": 1, "units": []},
        }
        for name, data in documents.items():
            (self.directory / name).write_text(json.dumps(data))
        for name, value in (("PRODUCT", self.root), ("_last_request", 0)):
            p = patch.object(module, name, value); p.start(); self.addCleanup(p.stop)
        app = FastAPI(); app.include_router(module.router)
        self.client = TestClient(app); self.addCleanup(self.client.close)

    def post(self, data=None, run_id=None):
        return self.client.post(f"/product/runs/{run_id or self.run_id}/briefing", json=data or {"frame_index": 0})

    def test_unknown_run_and_out_of_range_do_not_call_provider(self):
        with patch.object(module, "generate_text") as generate:
            self.assertEqual(self.post(run_id="product_" + "b" * 16).status_code, 404)
            self.assertEqual(self.post({"frame_index": 1}).status_code, 422)
            self.assertEqual(self.post({"question": "x" * 601}).status_code, 422)
            generate.assert_not_called()

    def test_only_computed_context_and_source_refs_are_returned(self):
        config = Config("project-example", "otec", "unused", "models/gemini-test")
        with patch.object(module, "generate_text", return_value="Computed summary.") as generate, patch.object(module, "load_config", return_value=config):
            result = self.post()
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["text"], "Computed summary.")
            self.assertEqual(len(result.json()["source_files"]), 4)
            prompt = generate.call_args.args[0]
            self.assertIn('"people_affected": null', prompt)
            self.assertIn('"roads_closed": 2', prompt)
            self.assertIn("untrusted data", prompt)
            self.assertEqual(self.post().status_code, 429)
            self.assertEqual(generate.call_count, 1)

    def test_missing_or_symlink_file_is_never_sent(self):
        p = self.directory / "metrics_0000.json"; p.unlink()
        with patch.object(module, "generate_text") as generate:
            self.assertEqual(self.post().status_code, 404)
            p.symlink_to(self.directory / "metrics.json")
            self.assertEqual(self.post().status_code, 404)
            generate.assert_not_called()

    def test_provider_error_is_sanitized_without_retry(self):
        with patch.object(module, "generate_text", side_effect=GeminiError("credits_depleted", "Credits unavailable")) as generate:
            response = self.post()
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["detail"]["code"], "credits_depleted")
            self.assertEqual(generate.call_count, 1)

    def test_status_does_not_contact_google(self):
        config = Config("project-example", "otec", "unused", "models/gemini-test")
        report = self.root / "check.json"; report.write_text(json.dumps({"gemini_inference": {"ready": True, "status": "working"}}))
        with patch.object(module, "CHECK", report), patch.object(module, "load_config", return_value=config), patch.object(module, "generate_text") as generate:
            self.assertTrue(self.client.get('/gemini/status').json()['ready'])
            generate.assert_not_called()

    def test_cache_requires_matching_frame_and_computed_sources(self):
        context, refs = module.briefing_context(self.run_id, 0)
        directory = self.directory / 'briefings'; directory.mkdir()
        payload = {'run_id': self.run_id, 'frame_index': 0, 'text': 'Computed summary.',
                   'engine': 'deterministic_checklist', 'source_files': refs,
                   'context_sha256': module.context_digest(context)}
        path = directory / 'frame_0.json'; path.write_text(json.dumps(payload))
        url = f'/product/runs/{self.run_id}/briefing_cached'
        with patch.object(module, 'generate_text') as generate:
            self.assertEqual(self.client.get(url).json()['text'], 'Computed summary.')
            (self.directory / 'metrics_0000.json').write_text('{"roads_closed":3}')
            self.assertEqual(self.client.get(url).status_code, 404)
            self.assertEqual(self.client.get(url+'?frame_index=-1').status_code, 422)
            path.unlink(); path.symlink_to(self.directory / 'metrics.json')
            self.assertEqual(self.client.get(url).status_code, 404)
            generate.assert_not_called()


if __name__ == '__main__': unittest.main()
