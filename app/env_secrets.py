import hashlib
import os
import secrets

from app_settings import saved_secret

# Per-process salt. An unreadable secret file must never fall back to the
# path itself, because that path would then be compared as the credential.
_UNREADABLE_SALT = secrets.token_bytes(32)


def _unreadable_secret(name):
    """An unguessable stand-in for a configured but unreadable secret.

    Returning None instead would read as "not configured", which lets the
    auth checks skip enforcement entirely, and returning the path would make
    the path the credential. This value is per-name (so username and password
    never collapse into one guessable string) and unguessable per process, so
    every comparison against it fails until the file is readable again.
    """
    return hashlib.sha256(_UNREADABLE_SALT + name.encode()).hexdigest()


def get_env_secret(name: str):
    """Return the environment variable value or, if it points to an existing
    file, return the file contents trimmed.

    This allows Docker secrets passed as `/run/secrets/NAME` to work when the
    compose file sets the env var to the secret file path.
    """
    saved, value = saved_secret(name)
    if saved:
        return value or None
    val = os.environ.get(name)
    if not val:
        return None
    try:
        if os.path.exists(val) and os.path.isfile(val):
            with open(val, "r") as fh:
                return fh.read().strip()
    except Exception:
        # Fail closed rather than returning the configured path.
        return _unreadable_secret(name)
    return val
