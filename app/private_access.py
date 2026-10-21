"""Restrict Alexa requests to the skill configured by the setup wizard."""

import hmac
import json
import logging
import os
import tempfile
from pathlib import Path

from flask import Response, request

logger = logging.getLogger(__name__)


def deployment_path():
    return Path(os.environ.get("SKILL_DEPLOYMENT_PATH", "/data/skill-deployment.json"))


def access_path(state_path=None):
    return Path(state_path or deployment_path()).with_name("skill-access.json")


def save_allowed_skill_id(skill_id, state_path=None):
    if not isinstance(skill_id, str) or not skill_id.strip():
        raise ValueError("A skill ID is required.")
    path = access_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".skill-access-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({"skill_id": skill_id.strip()}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_allowed_skill_id():
    path = access_path()
    if path.exists():
        try:
            value = json.loads(path.read_text()).get("skill_id")
            return value if isinstance(value, str) and value else None
        except (OSError, ValueError, AttributeError):
            logger.error("Cannot read the configured Alexa skill restriction")
            return None
    # Upgrade installations whose wizard completed before access settings were
    # saved separately. Never trust an unverified or incomplete deployment.
    try:
        state = json.loads(deployment_path().read_text())
        value = state.get("skill_id")
        if state.get("phase") == "complete" and isinstance(value, str) and value:
            save_allowed_skill_id(value)
            return value
    except (OSError, ValueError, AttributeError):
        pass
    return None


def check_private_access():
    if request.path != "/" or request.method != "POST":
        return None
    if request.headers.get("X-Simulator-Bypass") or request.headers.get(
        "X-Simulator-Signature"
    ):
        # The simulator has no skill ID; invoke_skill() only lets it through
        # with the app's username and password.
        return None
    expected_skill = get_allowed_skill_id()
    if not expected_skill:
        return Response("Complete skill deployment in the setup wizard first", 503)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return Response("Access denied", 403)
    system = (body.get("context") or {}).get("System") or {}
    session = body.get("session") or {}
    actual_skill = (system.get("application") or session.get("application") or {}).get(
        "applicationId", ""
    )
    if not hmac.compare_digest(str(actual_skill), expected_skill):
        logger.warning("Alexa request rejected: skill ID does not match")
        return Response("Access denied", 403)
    return None
