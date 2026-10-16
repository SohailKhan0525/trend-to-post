import io
import unittest

from PIL import Image

from app.generated_images import (
    GeneratedImageError,
    IMAGE_HEIGHT,
    IMAGE_WIDTH,
    PNG_SIGNATURE,
    generate_comparison_image,
)


class GeneratedComparisonImageTests(unittest.TestCase):
    def test_renders_png_locally_without_cloud_credentials(self):
        image_bytes = generate_comparison_image("Cursor or VS Code?")
        self.assertTrue(image_bytes.startswith(PNG_SIGNATURE))
        with Image.open(io.BytesIO(image_bytes)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (IMAGE_WIDTH, IMAGE_HEIGHT))
            self.assertEqual(image.mode, "RGB")

    def test_comparison_visuals_are_deterministic(self):
        first = generate_comparison_image("ChatGPT or Claude?")
        second = generate_comparison_image("ChatGPT or Claude?")
        self.assertEqual(first, second)

    def test_different_comparisons_change_the_visual(self):
        code = generate_comparison_image("Cursor or VS Code?")
        design = generate_comparison_image("Figma or Dark Mode?")
        self.assertNotEqual(code, design)

    def test_supports_or_vs_and_versus_separators(self):
        for headline in ("A or B?", "A vs B?", "A versus B?"):
            with self.subTest(headline=headline):
                self.assertTrue(generate_comparison_image(headline).startswith(PNG_SIGNATURE))

    def test_rejects_headline_without_two_choices(self):
        with self.assertRaises(GeneratedImageError):
            generate_comparison_image("Only one option?")

    def test_no_network_credentials_or_remote_image_service_are_needed(self):
        image_bytes = generate_comparison_image("Dark Mode or Light Mode?")
        self.assertGreater(len(image_bytes), 10_000)


if __name__ == "__main__":
    unittest.main()
