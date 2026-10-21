import json
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from music_assistant_models.enums import MediaType, QueueOption
from skill import voice_music as v
from test_radio import handler_input, queue, run_async


@pytest.fixture
def connected(monkeypatch):
    from contextlib import asynccontextmanager

    def use(client):
        @asynccontextmanager
        async def connection():
            yield client

        monkeypatch.setattr(v, "connection", connection)

    return use


def track(name, provider="library", item_id=None, favorite=False, available=True):
    item_id = item_id or name.lower()
    return NS(
        name=name,
        item_id=item_id,
        provider=provider,
        uri=f"{provider}://track/{item_id}",
        media_type=MediaType.TRACK,
        favorite=favorite,
        available=available,
        artists=[],
    )


def client(current=None, library=None, favorites=(), found=()):
    q = queue(current) if current is not None else NS(queue_id="echo-queue")
    if current is None:
        q.current_item = None
    return NS(
        music=NS(
            add_item_to_favorites=AsyncMock(),
            remove_item_from_favorites=AsyncMock(),
            get_library_item_by_prov_id=AsyncMock(return_value=library),
            get_library_tracks=AsyncMock(return_value=list(favorites)),
            search=AsyncMock(return_value=NS(tracks=list(found))),
        ),
        player_queues=NS(
            get_active_queue=AsyncMock(return_value=q), play_media=AsyncMock()
        ),
        send_command=AsyncMock(),
    )


@run_async
async def test_like_hearts_the_current_song(connected):
    c = client(track("Teardrop", "ytmusic", "abc"))
    connected(c)
    assert await v.set_favorite("echo", True) == "Added Teardrop to your favorites."
    c.music.add_item_to_favorites.assert_awaited_once_with("ytmusic://track/abc")


@run_async
async def test_unlike_clears_the_heart_of_the_library_copy(connected):
    library = track("Teardrop", item_id="12", favorite=True)
    c = client(track("Teardrop", "ytmusic", "abc"), library=library)
    connected(c)
    assert (
        await v.set_favorite("echo", False) == "Removed Teardrop from your favorites."
    )
    c.music.get_library_item_by_prov_id.assert_awaited_once_with(
        MediaType.TRACK, "abc", "ytmusic"
    )
    c.music.remove_item_from_favorites.assert_awaited_once_with(MediaType.TRACK, "12")


@run_async
async def test_unlike_a_song_that_is_not_a_favorite_changes_nothing(connected):
    c = client(track("Teardrop", favorite=False))
    connected(c)
    assert await v.set_favorite("echo", False) == "Teardrop is not in your favorites."
    c.music.remove_item_from_favorites.assert_not_awaited()


@run_async
async def test_like_needs_a_song_playing(connected):
    connected(client(NS(media_type=MediaType.RADIO, name="BBC")))
    with pytest.raises(ValueError, match="play a song first"):
        await v.set_favorite("echo", True)


@run_async
async def test_favorites_play_shuffled_once_as_a_finite_queue(connected):
    favorites = [track("A"), track("B"), track("Gone", available=False)]
    c = client(favorites=favorites)
    connected(c)
    with patch.object(v.random, "shuffle", side_effect=lambda xs: xs.reverse()):
        selection = await v.prepare_music("echo", kind="favorites")
    assert selection["media"] == ["library://track/b", "library://track/a"]
    assert selection["radio"] is False
    c.music.get_library_tracks.assert_awaited_once_with(favorite=True, limit=500)


@run_async
async def test_no_favorites_is_spoken(connected):
    connected(client())
    with pytest.raises(ValueError, match="no favorite songs"):
        await v.prepare_music("echo", kind="favorites")


@run_async
async def test_play_next_inserts_the_matched_song_after_the_current_one(connected):
    c = client(
        track("Playing"),
        found=[
            NS(
                **{
                    **vars(track("Teardrop", "ytmusic", "t1")),
                    "artists": [NS(name="Massive Attack")],
                }
            ),
            NS(
                **{
                    **vars(track("Teardrop", "ytmusic", "t2")),
                    "artists": [NS(name="Someone Else")],
                }
            ),
        ],
    )
    connected(c)
    assert await v.play_next("echo", "Teardrop by Massive Attack") == "Teardrop"
    c.player_queues.play_media.assert_awaited_once_with(
        "echo-queue", "ytmusic://track/t1", option=QueueOption.NEXT
    )


@run_async
async def test_play_next_needs_something_playing(connected):
    c = client(found=[track("Teardrop")])
    connected(c)
    with pytest.raises(ValueError, match="Nothing is playing"):
        await v.play_next("echo", "Teardrop")
    c.player_queues.play_media.assert_not_awaited()


def test_play_next_answers_with_the_song_and_keeps_pending_starts():
    hi = handler_input("PlayNextIntent", "Teardrop")
    with (
        patch.object(v.device_mapping, "get_player_for_device", return_value="echo"),
        patch.object(v, "play_next", AsyncMock(return_value="Teardrop")),
        patch.object(v.playback_requests, "begin") as begin,
        patch.object(v.threading, "Thread") as thread,
    ):
        assert v.VoiceMusicHandler().can_handle(hi)
        v.VoiceMusicHandler().handle(hi)
    hi.response_builder.speak.assert_called_once_with("Teardrop will play next.")
    begin.assert_not_called()
    thread.assert_not_called()


def test_like_is_spoken_without_cancelling_pending_starts():
    hi = handler_input("LikeSongIntent")
    with (
        patch.object(v.device_mapping, "get_player_for_device", return_value="echo"),
        patch.object(v, "set_favorite", AsyncMock(return_value="Added A.")) as fav,
        patch.object(v.playback_requests, "begin") as begin,
    ):
        v.VoiceMusicHandler().handle(hi)
    fav.assert_awaited_once_with("echo", True)
    hi.response_builder.speak.assert_called_once_with("Added A.")
    begin.assert_not_called()


def test_favorites_start_in_the_background():
    hi = handler_input("PlayFavoritesIntent")
    selection = {"queue_id": "q", "media": ["a"], "radio": False}
    with (
        patch.object(v.device_mapping, "get_player_for_device", return_value="echo"),
        patch.object(v, "prepare_music", AsyncMock(return_value=selection)) as prep,
        patch.object(v.threading, "Thread") as thread,
    ):
        v.VoiceMusicHandler().handle(hi)
    prep.assert_awaited_once_with("echo", "", "favorites", recommended=False)
    thread.return_value.start.assert_called_once()
    hi.response_builder.speak.assert_called_once_with("Starting your music.")


def test_model_has_the_new_phrases():
    model = json.loads(
        (Path(__file__).resolve().parents[1] / "app/models/en-US.json").read_text()
    )
    intents = {
        i["name"]: i["samples"]
        for i in model["interactionModel"]["languageModel"]["intents"]
    }
    assert "play {query} next" in intents["PlayNextIntent"]
    assert "like this song" in intents["LikeSongIntent"]
    assert "unlike this song" in intents["UnlikeSongIntent"]
    assert "play my favorites" in intents["PlayFavoritesIntent"]
