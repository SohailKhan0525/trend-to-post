import base64
import io
import json
import os
import unittest
from unittest.mock import patch

from PIL import Image

from app.generated_images import (
    GeneratedImageError,
    MODEL,
    build_comparison_image_prompt,
    generate_comparison_image,
)


def _fixture_png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (1024, 1024), (32, 96, 128)).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class GeneratedComparisonImageTests(unittest.TestCase):
    def setUp(self):
        self.image_bytes = _fixture_png()
        self.encoded = base64.b64encode(self.image_bytes).decode("ascii")
        self.env = patch.dict(
            os.environ,
            {
                "CLOUDFLARE_ACCOUNT_ID": "test-account",
                "CLOUDFLARE_API_TOKEN": "test-token",
            },
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_uses_cloudflare_flux_schnell_and_returns_validated_image_bytes(self):
        response = _FakeResponse({"success": True, "result": {"image": self.encoded}})
        with patch("app.generated_images.urllib.request.urlopen", return_value=response) as urlopen:
            image_bytes = generate_comparison_image("Grok Bot or ChatGPT Dots?")
        self.assertEqual(image_bytes, self.image_bytes)
        request = urlopen.call_args.args[0]
        self.assertIn(MODEL, request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["steps"], 4)
        self.assertIn("soft cool light gray", payload["prompt"])
        self.assertIn("vivid mint/turquoise green", payload["prompt"])
        self.assertIn("Grok Bot", payload["prompt"])
        self.assertIn("ChatGPT Dots", payload["prompt"])

    def test_prompt_strictly_matches_reference_layout_and_forbids_extra_text(self):
        prompt = build_comparison_image_prompt("Grok Bot or ChatGPT Dots?")
        self.assertIn("50/50 vertical split", prompt)
        self.assertIn("large centered white", prompt)
        self.assertIn("ONLY permitted letters", prompt)
        self.assertIn("logos", prompt.lower())
        self.assertIn("visual glitches", prompt.lower())
        self.assertLessEqual(len(prompt), 2048)

    def test_supports_or_vs_and_versus_separators(self):
        for headline in ("A or B?", "A vs B?", "A versus B?"):
            with self.subTest(headline=headline):
                response = _FakeResponse({"success": True, "result": {"image": self.encoded}})
                with patch("app.generated_images.urllib.request.urlopen", return_value=response):
                    self.assertEqual(generate_comparison_image(headline), self.image_bytes)

    def test_rejects_headline_without_two_choices_before_network_call(self):
        with patch("app.generated_images.urllib.request.urlopen") as urlopen:
            with self.assertRaises(GeneratedImageError):
                generate_comparison_image("Only one option?")
            urlopen.assert_not_called()

    def test_requires_cloudflare_credentials(self):
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "", "CLOUDFLARE_API_TOKEN": ""}):
            with self.assertRaisesRegex(GeneratedImageError, "CLOUDFLARE_ACCOUNT_ID"):
                generate_comparison_image("A or B?")

    def test_quota_exhaustion_fails_visibly_without_a_local_fallback(self):
        from urllib.error import HTTPError

        error = HTTPError(
            "https://api.cloudflare.com", 429, "quota reached", {}, io.BytesIO(b"daily limit")
        )
        with patch("app.generated_images.urllib.request.urlopen", side_effect=error):
            with self.assertRaisesRegex(GeneratedImageError, "daily-quota-limited"):
                generate_comparison_image("A or B?")

    def test_rejects_corrupt_base64_image(self):
        response = _FakeResponse({"success": True, "result": {"image": "not-base64!"}})
        with patch("app.generated_images.urllib.request.urlopen", return_value=response):
            with self.assertRaises(GeneratedImageError):
                generate_comparison_image("A or B?")


if __name__ == "__main__":
    unittest.main()
