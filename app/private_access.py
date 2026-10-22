"""Restrict Alexa requests to the skill configured by the setup wizard."""

import hmac
import json
import logging
import os
import tempfile
from contextlib import nullcontext
from pathlib import Path

from flask import Response, current_app, request

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
        _fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _fsync_directory(directory):
    """Flush the directory entry so the rename survives a power loss."""
    handle = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


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
    # saved separately. The restriction is re-derived (not written once):
    # deleting skill-access.json must never silently disable it. Trust only a
    # verification the wizard actually recorded — "complete" marks
    # Amazon-confirmed enablement, and verified_at survives a wizard re-entry
    # (prepare keeps it) but is cleared whenever a new deployment is prepared
    # (deploy) or only attested by the owner (reconcile). The read and the
    # derived write run under the deployment manager's lock so a concurrent
    # deploy()/reconcile() cannot clear the verification between them and
    # leave an outdated skill ID authorized.
    try:
        with _migration_lock():
            state = json.loads(deployment_path().read_text())
            value = state.get("skill_id")
            if not isinstance(value, str) or not value:
                return None
            verified_at = state.get("verified_at")
            if state.get("phase") == "complete" or isinstance(
                verified_at, (int, float)
            ):
                save_allowed_skill_id(value)
                return value
    except (OSError, ValueError, AttributeError):
        pass
    return None


def _migration_lock():
    """Lock deployment state while deriving access, when a manager exists.

    Skill-deployment writes take the manager's lock, so holding it here makes
    the state read and the skill-access.json write atomic against them. In
    minimal setups without the skill-deployment extension there is nothing
    concurrent to serialise against, and the migration stays unlocked.
    """
    try:
        manager = current_app.extensions.get("skill_deployment")
    except RuntimeError:  # Outside an application context.
        manager = None
    return manager.lock if manager is not None else nullcontext()


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
    system = body.get("context")
    session = body.get("session")
    system = system.get("System") if isinstance(system, dict) else {}
    session = session if isinstance(session, dict) else {}
    application = system.get("application") or session.get("application")
    actual_skill = (
        application.get("applicationId", "") if isinstance(application, dict) else ""
    )
    # Byte-wise comparison: hmac's str form is ASCII-only and would raise on
    # hostile non-ASCII input, turning a denial into a 500.
    if not hmac.compare_digest(str(actual_skill).encode(), expected_skill.encode()):
        logger.warning("Alexa request rejected: skill ID does not match")
        return Response("Access denied", 403)
    return None
