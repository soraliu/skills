import base64
import importlib.util
import json
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import sys
import tempfile
import unittest


ROOT = Path(__file__).parents[1]
SCRIPT = ROOT / "skills/imagegen-spec-auth/scripts/generate_image.py"
SPEC = importlib.util.spec_from_file_location("imagegen_spec_auth", SCRIPT)
imagegen = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(imagegen)


class ImagegenSpecAuthTest(unittest.TestCase):
    def test_default_output_is_user_scoped(self):
        with patch.object(sys, "argv", ["generate_image.py", "--prompt", "数码 3C 产品静物图"]):
            args = imagegen.parse_args()

        self.assertEqual(args.out, "~/.output/imagegen/image.png")
        self.assertEqual(Path(args.out).expanduser(), Path.home() / ".output/imagegen/image.png")

    def auth_and_args(self, directory: str, output: Path, request_id: str = "smoke-1"):
        auth = Path(directory) / "auth.json"
        auth.write_text(json.dumps({"key": "secret", "url": "https://api.example.test"}))
        return auth, [
            "generate_image.py",
            "--auth",
            str(auth),
            "--prompt",
            "数码 3C 产品静物图",
            "--out",
            str(output),
            "--request-id",
            request_id,
        ]

    def test_timeout_is_not_retried_or_fallback(self):
        calls = []

        class APITimeoutError(Exception):
            pass

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                raise APITimeoutError("read timed out")

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(data=[SimpleNamespace(id="gpt-image-2"), SimpleNamespace(id="grok-imagine-image")])
                )
                self.options = kwargs

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output)
            stderr = StringIO()
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    imagegen.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["model"], "gpt-image-2")
        self.assertEqual(calls[0]["extra_headers"]["Idempotency-Key"], imagegen.attempt_idempotency_key("smoke-1", "gpt-image-2"))
        self.assertEqual(stderr.getvalue().count("Calling "), 1)
        self.assertIn("结果未知", stderr.getvalue())

    def test_provider_approved_retry_reuses_key_once(self):
        calls = []
        sleep_calls = []

        class Retryable524(Exception):
            status_code = 524
            body = {"retryable": True, "retry_after": 2}
            request_id = "provider-524-1"

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    raise Retryable524("origin timeout")
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode(), url=None)]
                )

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="gpt-image-2")]))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output, "retry-1")
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), patch.object(imagegen.time, "sleep", side_effect=sleep_calls.append):
                self.assertEqual(imagegen.main(), 0)
            generated = output.read_bytes()

        self.assertEqual(generated, b"png")
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0]["extra_headers"]["Idempotency-Key"], calls[1]["extra_headers"]["Idempotency-Key"]
        )
        self.assertEqual(sleep_calls, [2.0])

    def test_provider_approved_retry_is_bounded(self):
        calls = []

        class Retryable524(Exception):
            status_code = 524
            body = {"retryable": True, "retry_after": 0}

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                raise Retryable524("origin timeout")

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(list=lambda: SimpleNamespace(data=[SimpleNamespace(id="gpt-image-2")]))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output, "retry-bounded")
            stderr = StringIO()
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), patch.object(imagegen.time, "sleep") as sleep, redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    imagegen.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(calls), 2)
        sleep.assert_called_once_with(0.0)
        self.assertIn("结果未知", stderr.getvalue())

    def test_unstable_model_fails_over_within_five_attempts(self):
        calls = []
        sleep_calls = []

        class Retryable524(Exception):
            status_code = 524
            body = {"retryable": True, "retry_after": 0}

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                if kwargs["model"] == "gpt-image-2":
                    raise Retryable524("origin timeout")
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode(), url=None)]
                )

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(
                        data=[
                            SimpleNamespace(id="gpt-image-2"),
                            SimpleNamespace(id="grok-imagine-image"),
                        ]
                    )
                )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output, "failover-1")
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), patch.object(imagegen.time, "sleep", side_effect=sleep_calls.append):
                self.assertEqual(imagegen.main(), 0)
            generated = output.read_bytes()

        self.assertEqual(generated, b"png")
        self.assertEqual([call["model"] for call in calls], ["gpt-image-2", "gpt-image-2", "grok-imagine-image"])
        self.assertEqual(calls[0]["extra_headers"]["Idempotency-Key"], calls[1]["extra_headers"]["Idempotency-Key"])
        self.assertNotEqual(calls[1]["extra_headers"]["Idempotency-Key"], calls[2]["extra_headers"]["Idempotency-Key"])
        self.assertEqual(sleep_calls, [0.0])

    def test_capacity_unavailable_retries_same_model_before_success(self):
        calls = []
        sleep_calls = []

        class CapacityUnavailable503(Exception):
            status_code = 503
            body = {"detail": "No available channel for model grok-imagine-image"}

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                if kwargs["model"] == "gpt-image-2":
                    raise RuntimeError("model not found")
                if len([call for call in calls if call["model"] == "grok-imagine-image"]) < 3:
                    raise CapacityUnavailable503("dispatcher capacity unavailable")
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode(), url=None)]
                )

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(
                        data=[
                            SimpleNamespace(id="gpt-image-2"),
                            SimpleNamespace(id="grok-imagine-image"),
                        ]
                    )
                )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output, "capacity-retry")
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), patch.object(imagegen.time, "sleep", side_effect=sleep_calls.append):
                self.assertEqual(imagegen.main(), 0)

        self.assertEqual(
            [call["model"] for call in calls],
            [
                "gpt-image-2",
                "grok-imagine-image",
                "grok-imagine-image",
                "grok-imagine-image",
            ],
        )
        self.assertEqual(sleep_calls, [imagegen.CAPACITY_RETRY_DELAY_SECONDS] * 2)
        self.assertEqual(
            calls[1]["extra_headers"]["Idempotency-Key"],
            calls[2]["extra_headers"]["Idempotency-Key"],
        )

    def test_capacity_unavailable_on_gpt_fails_over_to_grok(self):
        calls = []
        sleep_calls = []

        class CapacityUnavailable503(Exception):
            status_code = 503
            body = {"detail": "No available channel for model gpt-image-2"}

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                if kwargs["model"] == "gpt-image-2":
                    raise CapacityUnavailable503("dispatcher capacity unavailable")
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode(), url=None)]
                )

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(
                        data=[
                            SimpleNamespace(id="gpt-image-2"),
                            SimpleNamespace(id="grok-imagine-image"),
                        ]
                    )
                )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output, "gpt-capacity-failover")
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), patch.object(imagegen.time, "sleep", side_effect=sleep_calls.append):
                self.assertEqual(imagegen.main(), 0)

        self.assertEqual([call["model"] for call in calls], [
            "gpt-image-2",
            "gpt-image-2",
            "grok-imagine-image",
        ])
        self.assertEqual(sleep_calls, [imagegen.CAPACITY_RETRY_DELAY_SECONDS])

    def test_capacity_retries_stop_at_global_attempt_limit(self):
        calls = []
        sleep_calls = []

        class CapacityUnavailable503(Exception):
            status_code = 503
            body = {"detail": "No available channel for model grok-imagine-image"}

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                if kwargs["model"] == "gpt-image-2":
                    raise RuntimeError("model not found")
                raise CapacityUnavailable503("dispatcher capacity unavailable")

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(
                        data=[
                            SimpleNamespace(id="gpt-image-2"),
                            SimpleNamespace(id="grok-imagine-image"),
                        ]
                    )
                )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output, "capacity-max-five")
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), patch.object(imagegen.time, "sleep", side_effect=sleep_calls.append):
                with self.assertRaises(SystemExit) as raised:
                    imagegen.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertEqual(len(calls), imagegen.MAX_GENERATION_ATTEMPTS)
        self.assertEqual([call["model"] for call in calls], [
            "gpt-image-2",
            "grok-imagine-image",
            "grok-imagine-image",
            "grok-imagine-image",
            "grok-imagine-image",
        ])
        self.assertEqual(sleep_calls, [imagegen.CAPACITY_RETRY_DELAY_SECONDS] * 3)

    def test_total_generation_attempts_never_exceed_five(self):
        calls = []

        class Retryable524(Exception):
            status_code = 524
            body = {"retryable": True, "retry_after": 0}

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                raise Retryable524("origin timeout")

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(
                        data=[
                            SimpleNamespace(id="gpt-image-2"),
                            SimpleNamespace(id="grok-imagine-image"),
                            SimpleNamespace(id="dall-e-3"),
                        ]
                    )
                )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output, "max-five")
            stderr = StringIO()
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(
                sys, "argv", argv
            ), redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    imagegen.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertLessEqual(len(calls), imagegen.MAX_GENERATION_ATTEMPTS)
        self.assertEqual(len(calls), 4)
        self.assertNotIn("dall-e-3", [call["model"] for call in calls])

    def test_only_requested_models_are_selected(self):
        selected = imagegen.select_image_models(
            {
                "data": [
                    {"id": "gpt-image-2-pro"},
                    {"id": "grok-imagine-image"},
                    {"id": "nano-banana-2"},
                    {"id": "gpt-image-2"},
                    {"id": "dall-e-3"},
                ]
            }
        )
        self.assertEqual(selected, ["gpt-image-2", "grok-imagine-image"])

    def test_fallback_uses_distinct_idempotency_keys_and_atomic_output(self):
        calls = []
        client_options = []

        class Images:
            def generate(self, **kwargs):
                calls.append(kwargs)
                if kwargs["model"] == "gpt-image-2":
                    raise RuntimeError("model not found")
                return SimpleNamespace(
                    data=[SimpleNamespace(b64_json=base64.b64encode(b"png").decode(), url=None)]
                )

        class Client:
            def __init__(self, **kwargs):
                self.images = Images()
                self.models = SimpleNamespace(
                    list=lambda: SimpleNamespace(
                        data=[SimpleNamespace(id="gpt-image-2"), SimpleNamespace(id="grok-imagine-image")]
                    )
                )
                client_options.append(kwargs)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "out.png"
            auth, argv = self.auth_and_args(directory, output)
            with patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=Client)}), patch.object(sys, "argv", argv):
                self.assertEqual(imagegen.main(), 0)

            self.assertEqual(output.read_bytes(), b"png")
            self.assertEqual([call["model"] for call in calls], ["gpt-image-2", "grok-imagine-image"])
            self.assertNotEqual(
                calls[0]["extra_headers"]["Idempotency-Key"], calls[1]["extra_headers"]["Idempotency-Key"]
            )
            self.assertTrue(all(call["extra_headers"]["Idempotency-Key"].startswith("smoke-1:") for call in calls))
            self.assertEqual(client_options[0]["max_retries"], 0)


if __name__ == "__main__":
    unittest.main()
