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
            add_item_to_library=AsyncMock(),
            remove_item_from_library=AsyncMock(),
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
async def test_add_to_favorites_hearts_the_current_song(connected):
    c = client(track("Teardrop", "ytmusic", "abc"))
    connected(c)
    message = await v.update_current_song("echo", "favorites", True)
    assert message == "Added Teardrop to your favorites."
    c.music.add_item_to_favorites.assert_awaited_once_with("ytmusic://track/abc")


@run_async
async def test_remove_from_favorites_clears_the_heart_of_the_library_copy(connected):
    library = track("Teardrop", item_id="12", favorite=True)
    c = client(track("Teardrop", "ytmusic", "abc"), library=library)
    connected(c)
    message = await v.update_current_song("echo", "favorites", False)
    assert message == "Removed Teardrop from your favorites."
    c.music.get_library_item_by_prov_id.assert_awaited_once_with(
        MediaType.TRACK, "abc", "ytmusic"
    )
    c.music.remove_item_from_favorites.assert_awaited_once_with(MediaType.TRACK, "12")
    c.music.remove_item_from_library.assert_not_awaited()


@pytest.mark.parametrize(
    "collection, add, current, message",
    [
        (
            "favorites",
            True,
            track("A", favorite=True),
            "A is already in your favorites.",
        ),
        ("favorites", False, track("A"), "A is not in your favorites."),
        ("library", True, track("A"), "A is already in your library."),
        ("library", False, track("A", "ytmusic"), "A is not in your library."),
    ],
)
@run_async
async def test_nothing_changes_when_already_in_that_state(
    connected, collection, add, current, message
):
    c = client(current)
    connected(c)
    assert await v.update_current_song("echo", collection, add) == message
    for action in (
        "add_item_to_favorites",
        "remove_item_from_favorites",
        "add_item_to_library",
        "remove_item_from_library",
    ):
        getattr(c.music, action).assert_not_awaited()


@run_async
async def test_add_to_library_saves_a_provider_song(connected):
    c = client(track("Teardrop", "ytmusic", "abc"))
    connected(c)
    message = await v.update_current_song("echo", "library", True)
    assert message == "Added Teardrop to your library."
    c.music.add_item_to_library.assert_awaited_once_with("ytmusic://track/abc")
    c.music.add_item_to_favorites.assert_not_awaited()


@run_async
async def test_remove_from_library_removes_the_library_copy(connected):
    library = track("Teardrop", item_id="12")
    c = client(track("Teardrop", "ytmusic", "abc"), library=library)
    connected(c)
    message = await v.update_current_song("echo", "library", False)
    assert message == "Removed Teardrop from your library."
    c.music.remove_item_from_library.assert_awaited_once_with(MediaType.TRACK, "12")


@run_async
async def test_unknown_library_copy_counts_as_not_saved(connected):
    from music_assistant_models.errors import MediaNotFoundError

    c = client(track("Teardrop", "ytmusic", "abc"))
    c.music.get_library_item_by_prov_id.side_effect = MediaNotFoundError("gone")
    connected(c)
    message = await v.update_current_song("echo", "library", False)
    assert message == "Teardrop is not in your library."


@run_async
async def test_song_commands_need_a_song_playing(connected):
    connected(client(NS(media_type=MediaType.RADIO, name="BBC")))
    with pytest.raises(ValueError, match="play a song first"):
        await v.update_current_song("echo", "favorites", True)


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


@pytest.mark.parametrize(
    "intent, args",
    [
        ("AddFavoriteIntent", ("favorites", True)),
        ("RemoveFavoriteIntent", ("favorites", False)),
        ("AddToLibraryIntent", ("library", True)),
        ("RemoveFromLibraryIntent", ("library", False)),
    ],
)
def test_song_commands_are_spoken_without_cancelling_pending_starts(intent, args):
    hi = handler_input(intent)
    with (
        patch.object(v.device_mapping, "get_player_for_device", return_value="echo"),
        patch.object(v, "update_current_song", AsyncMock(return_value="Done.")) as up,
        patch.object(v.playback_requests, "begin") as begin,
    ):
        assert v.VoiceMusicHandler().can_handle(hi)
        v.VoiceMusicHandler().handle(hi)
    up.assert_awaited_once_with("echo", *args)
    hi.response_builder.speak.assert_called_once_with("Done.")
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
    assert "add this song to my favorites" in intents["AddFavoriteIntent"]
    assert "remove this song from my favorites" in intents["RemoveFavoriteIntent"]
    assert "add this song to my library" in intents["AddToLibraryIntent"]
    assert "remove this song from my library" in intents["RemoveFromLibraryIntent"]
    # "like" and "unlike" sound too alike for Alexa to tell apart.
    samples = [sample for phrases in intents.values() for sample in phrases]
    assert "like this song" not in samples and "unlike this song" not in samples
    assert "play my favorites" in intents["PlayFavoritesIntent"]
