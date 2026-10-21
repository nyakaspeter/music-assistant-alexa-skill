import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from skill_deployment import APP_DIR, package_fingerprint


class PackageTests(unittest.TestCase):
    def test_amazon_export_and_tampering(self):
        uri = "https://raw.githubusercontent.com/timlaing/music-assistant-alexa-skill/main/assets/icons/ma_108x108.png"

        def manifest(icon):
            return json.dumps(
                {
                    "manifest": {
                        "publishingInformation": {
                            "locales": {"en-US": {"smallIconUri": icon}}
                        }
                    }
                }
            ).encode()

        expected = {
            "skill-package/skill.json": manifest(uri),
            "skill-package/assets/images/en-US_smallIconUri.png": (
                APP_DIR.parent / "assets/icons/ma_108x108.png"
            ).read_bytes(),
        }
        actual = {
            "skill.json": manifest("file://assets/images/en-US_smallIconUri.png"),
            "assets/images/en-US_smallIconUri.png": (
                APP_DIR.parent / "assets/icons/ma_108x108.png"
            ).read_bytes(),
        }
        self.assertEqual(package_fingerprint(expected), package_fingerprint(actual))
        actual["assets/images/en-US_smallIconUri.png"] = b"changed"
        self.assertNotEqual(package_fingerprint(expected), package_fingerprint(actual))
