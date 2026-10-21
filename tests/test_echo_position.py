from unittest.mock import patch

import pytest
from skill import device_mapping, echo_position
from test_playback import AUTH, event, invoke, push

FLOW = "/flow/session/queue/item/Echo%20Dot%201"
# The pushed URL as the Echo receives it: on the public host, rewritten to MP3.
ECHO_TOKEN = "https://streams.example.com/music" + FLOW + ".mp3"


@pytest.fixture(autouse=True)
def no_reports(monkeypatch):
    monkeypatch.setattr(echo_position, "_reports", {})


@pytest.fixture
def playing(client):
    device_mapping.set_player_for_device("echo-1", "Echo Dot 1")
    push(client, streamUrl="http://ma.local:8097" + FLOW + ".flac")
    return client


def with_activity(payload, activity):
    payload["context"]["AudioPlayer"]["playerActivity"] = activity
    return payload


def test_pause_for_a_voice_request_is_reported_for_the_current_stream(playing):
    with patch.object(echo_position.time, "time", return_value=1000.0):
        invoke(event("AudioPlayer.PlaybackStopped", token=ECHO_TOKEN, offset=90_500))
    with patch.object(echo_position.time, "time", return_value=1003.0):
        report = echo_position.report("Echo Dot 1")
    assert report == {
        "playerId": "Echo Dot 1",
        "current": True,
        "offsetMs": 90_500,
        "activity": "STOPPED",
        "observedAt": 1000.0,
        "ageSeconds": 3.0,
    }


def test_resume_after_the_voice_request_is_reported(playing):
    invoke(event("AudioPlayer.PlaybackStopped", token=ECHO_TOKEN, offset=90_500))
    invoke(event("AudioPlayer.PlaybackStarted", token=ECHO_TOKEN, offset=90_500))
    assert echo_position.report("Echo Dot 1")["activity"] == "PLAYING"


def test_every_request_updates_the_position(playing):
    payload = event("IntentRequest", intent="WhatsPlayingIntent", token=None)
    payload["context"]["AudioPlayer"].update(
        token=ECHO_TOKEN, offsetInMilliseconds=200_000
    )
    invoke(with_activity(payload, "STOPPED"))
    report = echo_position.report("Echo Dot 1")
    assert (report["offsetMs"], report["activity"]) == (200_000, "STOPPED")


def test_requests_without_player_state_keep_the_last_report(playing):
    invoke(event("AudioPlayer.PlaybackStarted", token=ECHO_TOKEN, offset=5_000))
    invoke(event("IntentRequest", intent="AMAZON.HelpIntent"))
    assert echo_position.report("Echo Dot 1")["offsetMs"] == 5_000


def test_a_report_for_an_older_stream_is_not_current(playing):
    old = ECHO_TOKEN.replace("/session/", "/old-session/")
    invoke(event("AudioPlayer.PlaybackStarted", token=old, offset=5_000))
    assert echo_position.report("Echo Dot 1")["current"] is False


def test_unmapped_echos_are_not_recorded(client):
    invoke(event("AudioPlayer.PlaybackStarted", token=ECHO_TOKEN, offset=5_000))
    assert echo_position.report("Echo Dot 1") == {
        "playerId": "Echo Dot 1",
        "current": False,
    }


def test_endpoint_returns_the_report_to_music_assistant(playing):
    invoke(event("AudioPlayer.PlaybackStarted", token=ECHO_TOKEN, offset=5_000))
    response = playing.get(
        "/alexa/playback-state", query_string={"player_id": "Echo Dot 1"}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json["current"] is True
    assert response.json["offsetMs"] == 5_000
    assert response.json["activity"] == "PLAYING"


def test_endpoint_needs_credentials_and_a_player(client):
    query = {"player_id": "Echo Dot 1"}
    assert client.get("/alexa/playback-state", query_string=query).status_code == 401
    assert client.get("/alexa/playback-state", headers=AUTH).status_code == 400
