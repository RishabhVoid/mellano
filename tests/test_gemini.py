"""Mocked Gemini transport/credential checks; no live requests or real secrets."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import traceback
import unittest
from unittest.mock import Mock, patch

import requests
from sim import gemini

FAKE_KEY = "unit-test-secret-abcdefghijklmnopqrstuvwxyz"
RESOURCE = "projects/123456789012/locations/global/keys/otec-gemini-server"


class GeminiTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        temporary = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.path = Path(temporary).resolve() / "gemini_config.json"
        self.config = {"project": "test-project", "configuration": "otec", "key_resource": RESOURCE, "model": "models/gemini-3.5-flash-lite"}
        self.write_config()
        self.stack.enter_context(patch.object(gemini, "CONFIG_PATH", self.path))
        self.run = self.stack.enter_context(patch.object(gemini.subprocess, "run"))
        self.session_factory = self.stack.enter_context(patch.object(gemini.requests, "Session"))
        self.session = self.session_factory.return_value.__enter__.return_value
        self.response = Mock(status_code=200)
        self.response.json.return_value = self.answer("Completed answer")
        self.session.post.return_value = self.response
        self.description = {"name": RESOURCE, "restrictions": {"apiTargets": [{"service": gemini.SERVICE}]}}
        self.run.side_effect = self.command

    def write_config(self):
        self.path.write_text(json.dumps(self.config))

    def command(self, arguments, **kwargs):
        if "get-key-string" in arguments:
            value = {"keyString": FAKE_KEY}
        elif "api-keys" in arguments:
            value = self.description
        else:
            value = {"projectId": "test-project", "projectNumber": "123456789012"}
        return Mock(returncode=0, stdout=json.dumps(value), stderr="")

    @staticmethod
    def answer(text, finish="STOP"):
        return {"candidates": [{"finishReason": finish, "content": {"parts": [{"text": text}]}}]}

    def test_success_uses_header_only_fixed_host_and_one_request(self):
        self.assertEqual(gemini.generate_text("Test prompt", max_output_tokens=2048), "Completed answer")
        self.session.post.assert_called_once()
        args, kwargs = self.session.post.call_args
        self.assertEqual(args[0], "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent")
        self.assertNotIn(FAKE_KEY, args[0]); self.assertNotIn("?", args[0])
        self.assertEqual(kwargs["headers"]["x-goog-api-key"], FAKE_KEY)
        self.assertNotIn(FAKE_KEY, json.dumps(kwargs["json"]))
        self.assertEqual(kwargs["json"]["generationConfig"]["maxOutputTokens"], 2048)
        self.assertNotIn("temperature", kwargs["json"]["generationConfig"])
        self.assertEqual(kwargs["timeout"], (10, 90)); self.assertFalse(kwargs["allow_redirects"])
        self.assertTrue(kwargs["verify"]); self.assertFalse(self.session.trust_env)
        adapter = self.session.mount.call_args.args[1]
        self.assertEqual(adapter.max_retries.total, 0)
        for call in self.run.call_args_list:
            self.assertIn("--configuration=otec", call.args[0]); self.assertIn("--project=test-project", call.args[0])
            self.assertIn("--no-log-http", call.args[0]); self.assertNotIn(FAKE_KEY, repr(call.args))
            self.assertEqual(call.kwargs["env"]["CLOUDSDK_CORE_DISABLE_FILE_LOGGING"], "1")
            self.assertEqual(call.kwargs["stderr"], subprocess.PIPE)
            self.assertEqual(call.kwargs["stdout"], subprocess.PIPE)

    def test_only_gemini_restrictions_accepted_before_secret_retrieval(self):
        for restrictions in ({}, {"apiTargets": []}, {"apiTargets": [{"service": "storage.googleapis.com"}]}, {"apiTargets": [{"service": gemini.SERVICE}, {"service": "storage.googleapis.com"}]}):
            with self.subTest(restrictions=restrictions):
                self.run.reset_mock(); self.description["restrictions"] = restrictions
                with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
                self.assertEqual(error.exception.code, "restrictions")
                self.assertFalse(any("get-key-string" in call.args[0] for call in self.run.call_args_list))
        self.session.post.assert_not_called()

    def test_cross_project_resource_refused(self):
        self.config["key_resource"] = RESOURCE.replace("123456789012", "999999999999"); self.write_config()
        with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
        self.assertEqual(error.exception.code, "restrictions")
        self.assertEqual(self.run.call_count, 1); self.session.post.assert_not_called()

    def test_resource_identity_mismatch_and_deleted_keys_refused(self):
        for field, value in (("name", RESOURCE + "wrong"), ("deleteTime", "2026-11-01T00:00:00Z")):
            with self.subTest(field=field):
                saved = self.description.copy(); self.description[field] = value
                with self.assertRaises(gemini.GeminiError): gemini.generate_text("Test")
                self.description = saved
        self.session.post.assert_not_called()

    def test_config_rejects_secret_fields_injection_and_link_without_echo(self):
        original = self.config.copy()
        for field, value in (("api_key", FAKE_KEY), ("configuration", "otec --log-http"), ("project", "../outside"), ("model", "../../models/other"), ("key_resource", RESOURCE + "?secret=" + FAKE_KEY)):
            with self.subTest(field=field):
                self.config = {**original, field: value}; self.write_config()
                with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
                self.assertNotIn(FAKE_KEY, str(error.exception))
        self.path.unlink(); self.path.symlink_to(self.path.parent / "nonsecret-target.json")
        with self.assertRaises(gemini.GeminiError): gemini.generate_text("Test")
        self.run.assert_not_called(); self.session.post.assert_not_called()

    def test_input_bounds_prevent_paid_requests(self):
        cases = [("", {}), (" " * 2, {}), ("x" * 32769, {}), ("Test", {"max_output_tokens": 0}), ("Test", {"max_output_tokens": 4097}), ("Test", {"max_output_tokens": True}), ("Test", {"temperature": float("nan")}), ("Test", {"temperature": 3}), ("Test", {"model": "models/x?key=evil"})]
        for prompt, options in cases:
            with self.subTest(options=options):
                with self.assertRaises(gemini.GeminiError): gemini.generate_text(prompt, **options)
        self.run.assert_not_called(); self.session.post.assert_not_called()

    def test_402_credits_error_is_sanitized_without_retry_or_fallback(self):
        self.response.status_code = 402
        self.response.json.return_value = {"error": {"message": "depleted " + FAKE_KEY}}
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            self.assertEqual(gemini.main(["ask", "Test"]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("credits_depleted", err.getvalue()); self.assertNotIn(FAKE_KEY, err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        self.session.post.assert_called_once(); self.response.json.assert_not_called()

    def test_other_http_failures_are_not_retried_or_parsed(self):
        for code, expected in ((401, "access_denied"), (403, "access_denied"), (404, "model_unavailable"), (429, "rate_limited"), (500, "upstream"), (302, "upstream")):
            with self.subTest(status=code):
                self.session.post.reset_mock(); self.response.status_code = code
                with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
                self.assertEqual(error.exception.code, expected); self.session.post.assert_called_once()
        self.response.json.assert_not_called()

    def test_transport_and_gcloud_errors_do_not_disclose_secret_or_trace_context(self):
        self.session.post.side_effect = requests.ConnectionError("secret=" + FAKE_KEY)
        try: gemini.generate_text("Test")
        except gemini.GeminiError:
            self.assertNotIn(FAKE_KEY, traceback.format_exc())
        else: self.fail("Expected sanitized network error")
        self.run.side_effect = subprocess.TimeoutExpired(["gcloud"], 30, output=FAKE_KEY, stderr=FAKE_KEY)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err): self.assertEqual(gemini.main(["ask", "Test"]), 1)
        self.assertNotIn(FAKE_KEY, out.getvalue() + err.getvalue()); self.assertNotIn("Traceback", err.getvalue())

    def test_invalid_json_does_not_echo_body(self):
        self.response.json.side_effect = json.JSONDecodeError("secret " + FAKE_KEY, FAKE_KEY, 0)
        with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
        self.assertEqual(error.exception.code, "response"); self.assertNotIn(FAKE_KEY, str(error.exception))
        self.run.side_effect = None; self.run.return_value = Mock(returncode=0, stdout=FAKE_KEY, stderr=FAKE_KEY)
        with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
        self.assertNotIn(FAKE_KEY, str(error.exception))

    def test_thought_parts_ignored_and_visible_text_joined(self):
        self.response.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"thought": True, "text": "Private thought"}, {"text": "The answer "}, {"text": "is complete."}]}}]}
        self.assertEqual(gemini.generate_text("Test"), "The answer is complete.")

    def test_blocked_truncated_empty_and_incomplete_never_return_partial_text(self):
        payloads = [({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"), (self.answer("partial", "MAX_TOKENS"), "truncated"), (self.answer("unsafe", "SAFETY"), "blocked"), (self.answer("partial", "OTHER"), "incomplete"), (self.answer("   "), "empty"), ({"candidates": []}, "empty"), ({"candidates": [{"finishReason": "STOP", "content": {"parts": [{"thought": True, "text": "Private"}]}}]}, "empty")]
        for data, expected in payloads:
            with self.subTest(expected=expected):
                self.response.json.return_value = data
                with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
                self.assertEqual(error.exception.code, expected)

    def test_reflected_credential_withheld(self):
        self.response.json.return_value = self.answer(FAKE_KEY)
        with self.assertRaises(gemini.GeminiError) as error: gemini.generate_text("Test")
        self.assertNotIn(FAKE_KEY, str(error.exception))

    def test_check_requires_successful_generation_with_exact_marker(self):
        out, err = io.StringIO(), io.StringIO()
        self.response.json.return_value = self.answer(gemini.CHECK_MARKER)
        with redirect_stdout(out), redirect_stderr(err): self.assertEqual(gemini.main(["check"]), 0)
        self.session.post.assert_called_once(); self.assertIn(gemini.CHECK_MARKER, out.getvalue())
        self.response.json.return_value = self.answer("Different completed text")
        with redirect_stdout(out), redirect_stderr(err): self.assertEqual(gemini.main(["check"]), 1)
        self.assertIn("check_failed", err.getvalue())

    def test_native_json_schema_and_cloud_secret_do_not_use_gcloud(self):
        schema={'type':'object','properties':{'summary':{'type':'string'}},'required':['summary']}
        with patch.dict(os.environ,{'K_SERVICE':'otec','OTEC_GEMINI_SECRET':FAKE_KEY}):
            gemini.generate_text('Test',response_schema=schema)
        generation=self.session.post.call_args.kwargs['json']['generationConfig']
        self.assertEqual(generation['responseMimeType'],'application/json')
        self.assertEqual(generation['responseJsonSchema'],schema)
        self.run.assert_not_called()


if __name__ == "__main__": unittest.main()
