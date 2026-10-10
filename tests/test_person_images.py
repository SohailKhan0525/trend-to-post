import unittest

from app.person_images import (
    _candidate_from_page,
    _license_allowed,
    _needs_attribution,
    attribution_reply,
)


def commons_page(license_name="CC BY 4.0", artist="Alice Example"):
    return {
        "title": "File:Sam Altman portrait at a conference.jpg",
        "imageinfo": [{
            "thumburl": "https://upload.wikimedia.org/wikipedia/commons/a/aa/sam_altman.jpg",
            "thumbmime": "image/jpeg",
            "mime": "image/jpeg",
            "thumbwidth": 1600,
            "width": 4000,
            "descriptionurl": "https://commons.wikimedia.org/wiki/File:Sam_Altman_portrait.jpg",
            "extmetadata": {
                "ImageDescription": {"value": "Sam Altman speaking at a technology conference"},
                "LicenseShortName": {"value": license_name},
                "UsageTerms": {"value": license_name},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
                "Artist": {"value": artist},
            },
        }],
    }


class PersonImageLicenseTests(unittest.TestCase):
    def test_allows_public_domain_and_attribution_licenses(self):
        self.assertTrue(_license_allowed("Public domain"))
        self.assertTrue(_license_allowed("CC0 1.0 Universal"))
        self.assertTrue(_license_allowed("CC BY 4.0"))
        self.assertTrue(_license_allowed("CC BY-SA 4.0"))

    def test_rejects_restrictive_licenses(self):
        self.assertFalse(_license_allowed("CC BY-NC 4.0"))
        self.assertFalse(_license_allowed("CC BY-ND 4.0"))
        self.assertFalse(_license_allowed("All rights reserved"))
        self.assertFalse(_license_allowed("Fair use"))

    def test_only_returns_a_vetted_subject_match_with_license_metadata(self):
        result = _candidate_from_page(commons_page(), ("Sam Altman", "AI"))
        self.assertIsNotNone(result)
        self.assertEqual(result["subject"], "Sam Altman")
        self.assertEqual(result["mime"], "image/jpeg")
        self.assertEqual(result["license_name"], "CC BY 4.0")
        self.assertTrue(result["needs_attribution"])

    def test_rejects_photo_with_no_attribution_when_license_requires_it(self):
        result = _candidate_from_page(commons_page(artist=""), ("Sam Altman", "AI"))
        self.assertIsNone(result)

    def test_public_domain_does_not_force_a_credit_reply(self):
        self.assertFalse(_needs_attribution("Public domain"))
        self.assertFalse(_needs_attribution("CC0 1.0 Universal"))
        self.assertTrue(_needs_attribution("CC BY-SA 4.0"))

    def test_attribution_reply_includes_source_and_license_and_fits_x(self):
        image = {
            "artist": "Alice Example",
            "license_name": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "file_page_url": "https://commons.wikimedia.org/wiki/File:Sam_Altman_portrait.jpg",
        }
        reply = attribution_reply(image)
        self.assertIn("Alice Example", reply)
        self.assertIn("CC BY 4.0", reply)
        self.assertIn(image["file_page_url"], reply)
        self.assertLessEqual(len(reply), 280)


if __name__ == "__main__":
    unittest.main()
