import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from app.meme_image import HEIGHT, WIDTH, render_meme_image


class MemeImageRenderingTests(unittest.TestCase):
    def test_renders_phone_readable_parody_chat_without_remote_image(self) -> None:
        conversation = [
            {"speaker": "USER", "text": "Can you make this app less addictive?"},
            {"speaker": "AI", "text": "Sure. I added a 45-minute loading screen."},
            {"speaker": "SYSTEM", "text": "You have checked the loading screen 19 times."},
        ]

        with tempfile.TemporaryDirectory() as tmp_dir:
            output = Path(tmp_dir) / "meme.png"
            with patch("app.meme_image._cloudflare_background", return_value=None):
                rendered = render_meme_image(
                    conversation,
                    "surreal phone with a tiny loading wheel, no text or logos",
                    "APP SUPPORT",
                    output,
                )

            self.assertTrue(rendered.exists())
            with Image.open(rendered) as image:
                self.assertEqual(image.size, (WIDTH, HEIGHT))
                self.assertEqual(image.format, "PNG")
                self.assertGreater(rendered.stat().st_size, 10_000)


if __name__ == "__main__":
    unittest.main()
