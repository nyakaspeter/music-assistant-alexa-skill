"""Locked, independent snapshots for MA streams and Alexa playback metadata."""

import time
from copy import deepcopy
from threading import RLock
from urllib.parse import unquote, urlsplit

_lock = RLock()
_ma = None
_alexa = None
_version = 0
_player_snapshots = {}


def get_ma():
    with _lock:
        return deepcopy(_ma)


def get_alexa():
    with _lock:
        return deepcopy(_alexa)


def set_ma(payload):
    global _ma, _version
    with _lock:
        _version += 1
        _ma = deepcopy(payload)
        player = player_from_stream(payload.get("streamUrl"))
        _ma.update(playerId=player, version=_version, timestamp=time.time())
        if player and payload.get("streamUrl"):
            _player_snapshots[player] = deepcopy(_ma)
        return _version


def set_alexa(payload):
    global _alexa
    with _lock:
        _alexa = deepcopy(payload)


def player_from_stream(url):
    """Stock MA stream URLs identify the output player in the final filename."""
    if not isinstance(url, str):
        return None
    parts = urlsplit(url).path.strip("/").split("/")
    if len(parts) < 5 or parts[-5] not in ("flow", "single"):
        return None
    filename, separator, extension = parts[-1].rpartition(".")
    if not separator or extension not in ("mp3", "flac", "wav", "aac", "pcm", "ogg"):
        return None
    return unquote(filename) or None


def get_player_snapshot(player):
    with _lock:
        snapshot = _player_snapshots.get(player)
        if snapshot is None and _ma and not _ma.get("playerId"):
            snapshot = _ma  # stream URLs that do not name their player
        return deepcopy(snapshot)
