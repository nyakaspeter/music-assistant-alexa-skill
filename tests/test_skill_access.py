import json
import logging
import unittest
from types import SimpleNamespace
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


def test_upgrade_trusts_surviving_verification_beyond_complete(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(state))
    # A wizard re-entry after a successful deploy leaves phase "ready" but
    # keeps verified_at — that skill was verified, so the upgrade must not
    # strand the install into 503.
    state.write_text(
        json.dumps({"phase": "ready", "skill_id": "verified", "verified_at": 1.0})
    )
    assert access.get_allowed_skill_id() == "verified"
    # Reconcile clears verified_at (owner attestation is not API verification);
    # without a surviving verification the migration stays fail-closed.
    access.access_path().unlink()
    state.write_text(json.dumps({"phase": "reconciled", "skill_id": "attested"}))
    assert access.get_allowed_skill_id() is None


def test_corrupt_access_file_does_not_fall_back(tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(state))
    state.write_text(json.dumps({"phase": "complete", "skill_id": "old"}))
    access.access_path().write_text("invalid")
    assert access.get_allowed_skill_id() is None


def test_unwritable_access_cache_does_not_deny_verified_skill(
    tmp_path, monkeypatch, caplog
):
    # skill-access.json only caches what the deployment state already attests,
    # and the ID is re-derived on every request. A cache write that fails
    # (read-only or full data directory) must log the problem instead of
    # turning an already verified installation into a 503.
    state = tmp_path / "state.json"
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(state))
    state.write_text(json.dumps({"phase": "complete", "skill_id": "verified"}))

    def deny(skill_id, state_path=None):
        raise OSError("read-only file system")

    monkeypatch.setattr(access, "save_allowed_skill_id", deny)
    with caplog.at_level(logging.WARNING, logger="private_access"):
        assert access.get_allowed_skill_id() == "verified"
    assert "Cannot persist the Alexa skill restriction" in caplog.text


class RecordingLock:
    """Stand-in for the deployment manager's state lock."""

    def __init__(self):
        self.held = False
        self.acquisitions = 0

    def __enter__(self):
        self.held = True
        self.acquisitions += 1
        return self

    def __exit__(self, *exc_info):
        self.held = False
        return False


def test_migration_holds_state_lock_during_derivation(tmp_path, monkeypatch):
    # A deploy can rewrite the deployment state between the migration's read
    # and its derived write; the migration must hold the same lock as every
    # deployment-state write so it cannot authorise an outdated skill ID.
    state = tmp_path / "state.json"
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(state))
    state.write_text(json.dumps({"phase": "complete", "skill_id": "verified"}))
    state_lock = RecordingLock()
    held_during_save = []
    real_save = access.save_allowed_skill_id

    def spy(skill_id, state_path=None):
        held_during_save.append(state_lock.held)
        real_save(skill_id, state_path)

    monkeypatch.setattr(access, "save_allowed_skill_id", spy)
    app = Flask(__name__)
    app.extensions["skill_deployment"] = SimpleNamespace(lock=state_lock)
    with app.app_context():
        assert access.get_allowed_skill_id() == "verified"
    assert held_during_save == [True]
    assert state_lock.held is False
    assert state_lock.acquisitions == 1


def test_migration_locks_via_factory_before_registration(tmp_path, monkeypatch):
    # The manager is not registered until the application finishes
    # initialising, so an upgrade read during startup must still reach it
    # through the factory rather than falling back to an unlocked context.
    import skill_deployment

    state = tmp_path / "state.json"
    monkeypatch.setenv("SKILL_DEPLOYMENT_PATH", str(state))
    state.write_text(json.dumps({"phase": "complete", "skill_id": "verified"}))
    state_lock = RecordingLock()
    monkeypatch.setattr(
        skill_deployment, "manager", lambda: SimpleNamespace(lock=state_lock)
    )
    app = Flask(__name__)  # the extension is deliberately left unregistered
    with app.app_context():
        assert access.get_allowed_skill_id() == "verified"
    assert state_lock.acquisitions == 1
    assert state_lock.held is False


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

    def test_malformed_shapes_and_non_ascii_are_denied(self):
        # Hostile bodies must be plain 403s, never unhandled 500s.
        with patch.object(
            private_access, "get_allowed_skill_id", return_value=self.skill
        ):
            self.assertEqual(
                self.client.post("/", json={"context": "x"}).status_code, 403
            )
            self.assertEqual(
                self.client.post("/", json={"session": "x"}).status_code, 403
            )
            self.assertEqual(
                self.client.post(
                    "/",
                    json={
                        "context": {
                            "System": {"application": {"applicationId": "café"}}
                        }
                    },
                ).status_code,
                403,
            )

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


def test_simulator_denied_when_app_credentials_unreadable(monkeypatch, client):
    # The root POST endpoint is exempt from app-level auth, so a simulator
    # request that cannot have its credentials read must be denied here
    # rather than falling through to ordinary dispatch.
    from test_playback import event

    import app as app_module

    def unreadable(name):
        raise OSError("cannot read the secret file")

    monkeypatch.setattr(app_module, "get_env_secret", unreadable)
    response = client.post("/", json=event(), headers={"X-Simulator-Bypass": "true"})
    assert response.status_code == 403
