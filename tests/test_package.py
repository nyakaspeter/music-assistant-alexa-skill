import json

import pytest
from skill_deployment import (
    ICON_URLS,
    DeploymentError,
    package_fingerprint,
    package_fingerprints,
    package_mismatch,
    store_icon_assets,
)

ICON = "assets/images/en-US_smallIconUri.png"


def manifest(icon, name="Music Assistant"):
    return json.dumps(
        {
            "manifest": {
                "publishingInformation": {
                    "locales": {"en-US": {"name": name, "smallIconUri": icon}}
                }
            }
        }
    ).encode()


def approved():
    return {
        "skill-package/skill.json": manifest(ICON_URLS["small"]),
        f"skill-package/{ICON}": b"artwork",
    }


def amazon_export(**changes):
    files = {"skill.json": manifest(f"file://{ICON}"), ICON: b"artwork"}
    files.update(changes)
    return files


def test_export_without_the_wrapper_matches():
    assert package_fingerprint(approved()) == package_fingerprint(amazon_export())


def test_changed_member_without_the_wrapper_is_caught():
    tampered = amazon_export(**{ICON: b"changed"})
    assert package_fingerprint(approved()) != package_fingerprint(tampered)


@pytest.mark.parametrize(
    "key",
    ["skill-package/skill-package/skill.json", "models/skill-package/skill.json"],
)
def test_only_one_leading_wrapper_is_ignored(key):
    files = approved()
    files[key] = files.pop("skill-package/skill.json")
    assert package_fingerprint(files) != package_fingerprint(approved())


def test_member_present_with_and_without_the_wrapper_is_refused():
    twins = amazon_export(**{f"skill-package/{ICON}": b"tampered"})
    with pytest.raises(DeploymentError, match="twice"):
        package_fingerprint(twins)


def test_mismatch_reports_the_real_difference_across_naming():
    export = amazon_export(**{"skill.json": manifest(f"file://{ICON}", name="Other")})
    assert package_mismatch(approved(), export) == {
        "skill-package/skill.json": [
            "manifest.publishingInformation.locales.en-US.name: value differs"
        ]
    }


def test_icon_adoption_keeps_the_approved_naming():
    files = approved()
    stored = store_icon_assets(files, amazon_export(**{ICON: b"re-encoded"}))
    assert stored == [f"skill-package/{ICON}"]
    assert files[f"skill-package/{ICON}"] == b"re-encoded"
    assert ICON not in files
    assert package_fingerprint(files) == package_fingerprint(
        amazon_export(**{ICON: b"re-encoded"})
    )


def test_review_saved_before_the_upgrade_still_matches():
    files = approved()
    older = package_fingerprint(files, {key: key for key in files})
    assert older != package_fingerprint(files)
    assert older in package_fingerprints(files)
