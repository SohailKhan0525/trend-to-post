import base64
import json
import os
import unittest
from unittest.mock import patch

from app.generated_images import GeneratedImageError, PNG_SIGNATURE, generate_comparison_image

JPEG_SIGNATURE = bytes.fromhex("ffd8ff")


class GeneratedComparisonImageTests(unittest.TestCase):
    def test_requires_cloudflare_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(GeneratedImageError):
                generate_comparison_image("Grok or ChatGPT?", "Which one?")

    def test_accepts_png_response_and_requests_no_text(self):
        class Response:
            headers = {"Content-Type": "image/png"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return PNG_SIGNATURE + b"image-data"

        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "token"}):
            with patch("app.generated_images.urllib.request.urlopen", return_value=Response()) as open_url:
                image = generate_comparison_image("Grok Bot or ChatGPT Dots?", "Which one?")
        self.assertTrue(image.startswith(PNG_SIGNATURE))
        request = open_url.call_args.args[0]
        payload = json.loads(request.data)
        self.assertIn("no text", payload["prompt"].lower())
        self.assertIn("no text overlays", payload["prompt"].lower())
        self.assertEqual(payload["steps"], 4)
        self.assertEqual(set(payload), {"prompt", "steps"})

    def test_decodes_json_base64_png_response(self):
        class Response:
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit):
                return json.dumps({"result": {"image": base64.b64encode(PNG_SIGNATURE + b"png").decode()}}).encode()
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "token"}):
            with patch("app.generated_images.urllib.request.urlopen", return_value=Response()):
                self.assertTrue(generate_comparison_image("A or B?", "Which?").startswith(PNG_SIGNATURE))

    def test_accepts_documented_jpeg_response(self):
        class Response:
            headers = {"Content-Type": "application/json"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit):
                return json.dumps({"result": {"image": base64.b64encode(JPEG_SIGNATURE + b"jpeg").decode()}}).encode()
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "token"}):
            with patch("app.generated_images.urllib.request.urlopen", return_value=Response()):
                self.assertTrue(generate_comparison_image("A or B?", "").startswith(JPEG_SIGNATURE))

    def test_rejects_non_image_response(self):
        class Response:
            headers = {"Content-Type": "image/jpeg"}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, limit): return b"not a png"
        with patch.dict(os.environ, {"CLOUDFLARE_ACCOUNT_ID": "account", "CLOUDFLARE_API_TOKEN": "token"}):
            with patch("app.generated_images.urllib.request.urlopen", return_value=Response()):
                with self.assertRaises(GeneratedImageError):
                    generate_comparison_image("A or B?", "Which?")


if __name__ == "__main__":
    unittest.main()
