from unittest.mock import AsyncMock, patch

from skill import playback_requests
from skill import voice_music as v
from test_radio import handler_input


def handle(intent, query="", **patches):
    hi = handler_input(intent, query)
    with (
        patch.object(v.device_mapping, "get_player_for_device", return_value="echo"),
        patch.object(v.threading, "Thread") as thread,
    ):
        with patch.multiple(v, **patches):
            v.VoiceMusicHandler().handle(hi)
    return hi.response_builder.speak.call_args.args[0], thread


def test_failed_search_keeps_the_pending_start():
    pending = playback_requests.begin("echo")
    spoken, thread = handle(
        "PlaySongIntent",
        "Missing",
        prepare_music=AsyncMock(side_effect=ValueError("I could not find that track.")),
    )
    assert spoken == "I could not find that track."
    thread.assert_not_called()
    assert playback_requests.current(pending)


def test_ready_request_replaces_the_pending_start():
    pending = playback_requests.begin("echo")
    handle("PlaySongIntent", "Song", prepare_music=AsyncMock(return_value={}))
    assert not playback_requests.current(pending)


def test_similar_songs_without_a_song_is_answered_without_starting_anything():
    spoken, thread = handle(
        "PlaySimilarIntent",
        check_similar=AsyncMock(side_effect=ValueError("Please play a song first.")),
    )
    assert spoken == "Please play a song first."
    thread.assert_not_called()


def test_similar_songs_are_confirmed_after_the_check():
    spoken, thread = handle("PlaySimilarIntent", check_similar=AsyncMock())
    assert spoken == "Switching to similar songs after this track."
    thread.return_value.start.assert_called_once()


def test_longer_answers_wait_longer_before_the_music_starts():
    short = v.speaking_time("Starting your music.")
    long = v.speaking_time("Resuming Teardrop by Massive Attack.")
    assert 1 < short < long < 3
