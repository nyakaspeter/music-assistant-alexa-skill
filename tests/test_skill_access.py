import json
import unittest
from unittest.mock import patch

import private_access
import private_access as access
from flask import Flask


def test_access_setting_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(tmp_path / "state.json"))
    access.save_allowed_skill_id("amzn1.ask.skill.mine")
    assert access.get_allowed_skill_id() == "amzn1.ask.skill.mine"
    assert access.access_path().stat().st_mode & 0o777 == 0o600


def test_upgrade_trusts_only_completed_deployment(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(state))
    state.write_text(json.dumps({"phase": "ready", "skill_id": "unverified"}))
    assert access.get_allowed_skill_id() is None
    state.write_text(json.dumps({"phase": "complete", "skill_id": "verified"}))
    assert access.get_allowed_skill_id() == "verified"
    assert json.loads(access.access_path().read_text()) == {"skill_id": "verified"}


def test_corrupt_access_file_does_not_fall_back(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(state))
    state.write_text(json.dumps({"phase": "complete", "skill_id": "old"}))
    access.access_path().write_text("invalid")
    assert access.get_allowed_skill_id() is None


class PrivateAccessTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.before_request(private_access.check_private_access)
        self.app.add_url_rule("/", "skill", lambda: "ok", methods=["POST"])
        self.client = self.app.test_client()
        self.skill = "mine"

    def invoke(self, user="owner", skill="mine", headers=None):
        with patch.object(
            private_access, "get_allowed_skill_id", return_value=self.skill
        ):
            return self.client.post(
                "/",
                json={
                    "context": {
                        "System": {
                            "application": {"applicationId": skill},
                            "user": {"userId": user},
                        }
                    }
                },
                headers=headers or {},
            )

    def test_skill_required_and_owner_not_restricted(self):
        self.assertEqual(self.invoke().status_code, 200)
        self.assertEqual(self.invoke(user="someone-else").status_code, 200)
        self.assertEqual(self.invoke(skill="other-skill").status_code, 403)

    def test_fail_closed(self):
        self.skill = None
        self.assertEqual(self.invoke().status_code, 503)

    def test_simulator_skips_skill_check_for_app_auth_to_decide(self):
        self.assertEqual(
            self.invoke(skill="", headers={"X-Simulator-Bypass": "1"}).status_code, 200
        )
        self.assertEqual(
            self.invoke(skill="", headers={"X-Simulator-Other": "1"}).status_code, 403
        )


def test_simulator_still_requires_app_credentials(client):
    from test_playback import AUTH, event

    access.save_allowed_skill_id("amzn1.ask.skill.mine")
    headers = {"X-Simulator-Bypass": "true"}
    assert client.post("/", json=event(), headers=headers).status_code == 403
    response = client.post("/", json=event(), headers={**AUTH, **headers})
    assert response.status_code == 200
