"""Where each Echo really is in its stream, as it reports with every request.

Alexa pauses skill audio for every voice request, so Music Assistant's clock runs ahead
of the Echo. Every request carries the Echo's AudioPlayer state, and the playback events
carry the offset at which playback stopped or resumed. Music Assistant's Alexa player
reads the latest report per player from /alexa/playback-state to correct itself.
"""

import threading
import time
from urllib.parse import unquote, urlsplit

import shared_store

from . import device_mapping

# Activity implied by AudioPlayer events, which report the offset in the request itself.
EVENT_ACTIVITY = {
    "AudioPlayer.PlaybackStarted": "PLAYING",
    "AudioPlayer.PlaybackStopped": "STOPPED",
    "AudioPlayer.PlaybackFinished": "FINISHED",
}

_lock = threading.Lock()
_reports = {}


def record(envelope):
    """Remember the Echo's stream offset and activity for its Music Assistant player."""
    request = envelope.request
    device = getattr(getattr(envelope.context, "system", None), "device", None)
    player = device_mapping.get_player_for_device(getattr(device, "device_id", None))
    if not player:
        return
    activity = EVENT_ACTIVITY.get(getattr(request, "object_type", None))
    if activity:
        token = getattr(request, "token", None)
        offset = getattr(request, "offset_in_milliseconds", None)
    else:
        audio_player = getattr(envelope.context, "audio_player", None)
        token = getattr(audio_player, "token", None)
        offset = getattr(audio_player, "offset_in_milliseconds", None)
        activity = getattr(
            getattr(audio_player, "player_activity", None), "value", None
        )
    if not token or offset is None or not activity:
        return
    with _lock:
        _reports[player] = {
            "token": token,
            "offsetMs": offset,
            "activity": activity,
            "observedAt": time.time(),
        }


def _stream_key(url):
    # The Echo gets the pushed URL rewritten to MP3; compare the path without extension.
    path = unquote(urlsplit(url or "").path)
    return path.rsplit(".", 1)[0] if path else ""


def report(player):
    """Return the latest report for the player, and whether it is for its current stream."""
    with _lock:
        latest = _reports.get(player)
    if latest is None:
        return {"playerId": player, "current": False}
    snapshot = shared_store.get_player_snapshot(player) or {}
    pushed = _stream_key(snapshot.get("streamUrl"))
    return {
        "playerId": player,
        "current": bool(pushed) and _stream_key(latest["token"]) == pushed,
        "offsetMs": latest["offsetMs"],
        "activity": latest["activity"],
        "observedAt": latest["observedAt"],
        "ageSeconds": max(0.0, time.time() - latest["observedAt"]),
    }
