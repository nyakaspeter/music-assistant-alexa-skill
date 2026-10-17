"""Spoken music selection and queue controls using Music Assistant's APIs."""
import asyncio
import logging
import random
import re
import threading
import unicodedata
from contextlib import asynccontextmanager
from xml.sax.saxutils import escape

import aiohttp
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from env_secrets import get_env_secret
from music_assistant_client import MusicAssistantClient
from music_assistant_models.enums import MediaType, ProviderFeature, QueueOption
from music_assistant_models.errors import MusicAssistantError

from . import device_mapping

logger = logging.getLogger(__name__)


@asynccontextmanager
async def connection():
    server = get_env_secret('MA_API_URL')
    token = get_env_secret('MA_API_TOKEN')
    if not server or not token:
        raise ValueError('Please connect the Music Assistant API token first.')
    async with (aiohttp.ClientSession() as session,
                MusicAssistantClient(server, session, token=token) as client):
        yield client


def normalized(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', value.casefold()) if c.isalnum())


def choose_playlist(items, query):
    matches = [item for item in items if normalized(item.name) == normalized(query)]
    if len(matches) == 1:
        return matches[0]
    if len(items) == 1:
        return items[0]
    raise ValueError('I could not identify one playlist. Please use its full name.')


def title_and_artist(query):
    # SearchQuery cannot share an Alexa sample with another slot. Keep the
    # unrestricted query and interpret its final "by artist" qualifier here.
    matches = list(re.finditer(r'\s+by\s+', query, re.IGNORECASE))
    if matches:
        match = matches[-1]
        title, artist = query[:match.start()].strip(), query[match.end():].strip()
        if title and artist:
            return title, artist
    return query.strip(), None


def artist_matches(item, requested):
    def key(name):
        return normalized(re.sub(r'^the\s+', '', name, flags=re.IGNORECASE))
    return any(key(artist.name) == key(requested) for artist in getattr(item, 'artists', []))


async def find_music(client, query, kind):
    title, artist = title_and_artist(query) if kind in ('track', 'album') else (query, None)
    searches = [query] if not artist else [query, title]
    candidates = []
    for search in searches:
        results = await client.music.search(search, media_types=[MediaType(kind)], limit=25)
        items = getattr(results, {'track': 'tracks', 'artist': 'artists', 'album': 'albums'}[kind])
        candidates.extend(item for item in items if getattr(item, 'available', True))
        if artist:
            # Titles such as Stand by Me contain 'by' without an artist qualifier.
            exact_phrase = [item for item in candidates if normalized(item.name) == normalized(query)]
            if exact_phrase:
                return exact_phrase[0]
            matches = [item for item in candidates if artist_matches(item, artist)
                       and normalized(re.sub(r'\s*[\(\[].*$', '', item.name)) == normalized(title)]
            if matches:
                return min(matches, key=lambda item: normalized(item.name) != normalized(title))
        elif candidates:
            return min(candidates, key=lambda item: normalized(item.name) != normalized(title))
    raise ValueError('I could not find that ' + kind + (' by that artist.' if artist else '.'))


async def active_queue(client, player_id):
    queue = await client.player_queues.get_active_queue(player_id)
    if queue is None:
        raise ValueError('This Echo does not have a Music Assistant queue yet.')
    return queue


async def select_and_play(player_id, query, playlist=False, kind='track', option=QueueOption.REPLACE):
    async with connection() as client:
        if playlist or kind == 'playlist':
            item = choose_playlist(await client.music.get_library_playlists(search=query, limit=20), query)
        else:
            item = await find_music(client, query, kind)
        queue = await active_queue(client, player_id)
        media = item.uri
        if kind == 'artist':
            tracks = await client.music.get_artist_tracks(item.item_id, item.provider)
            if not tracks:
                raise ValueError('I could not find tracks for that artist.')
            media = [track.uri for track in tracks]
        radio = kind == 'track' and not playlist and option == QueueOption.REPLACE
        if radio:
            await client.player_queues.play_media(queue.queue_id, media, option=option, radio_mode=True)
        else:
            # The server accepts a per-request shuffle flag; the current client
            # wrapper does not expose it. Explicit False prevents inherited shuffle.
            await client.send_command('player_queues/play_media', queue_id=queue.queue_id,
                                      media=media, option=option, radio_mode=False, shuffle=False)
        return item.name


async def library_selection(client):
    for favorite in (True, None):
        tracks = await client.music.get_library_tracks(favorite=favorite, limit=50, order_by='random')
        tracks = [track for track in tracks if getattr(track, 'available', True)]
        if tracks:
            random.shuffle(tracks)
            return [track.uri for track in tracks]
    return []


async def recommended_seed(client):
    """Random playable recommendation, independent of music provider."""
    rows = list(await client.music.recommendations())
    random.shuffle(rows)
    for row in rows:
        try:
            items = await client.send_command('music/recommendations/items',
                                              provider=row.provider, item_id=row.item_id)
            tracks = [item for item in items if item.get('media_type') == 'track'
                      and item.get('uri') and item.get('available', True)]
            if tracks:
                return random.choice(tracks)['uri']
        except MusicAssistantError:
            logger.warning('Skipping unavailable recommendation row')
    tracks = await library_selection(client)
    if tracks:
        return random.choice(tracks)
    raise ValueError('Music Assistant has no recommended songs or library tracks available right now.')


async def play_recommended(player_id):
    async with connection() as client:
        queue = await active_queue(client, player_id)
        seed = await recommended_seed(client)
        radio = any(provider.available and ProviderFeature.SIMILAR_TRACKS in provider.supported_features
                    for provider in client.providers)
        if radio:
            try:
                await client.player_queues.play_media(queue.queue_id, seed,
                                                     option=QueueOption.REPLACE, radio_mode=True)
                return
            except MusicAssistantError:
                logger.warning('Recommendation mix unavailable; using normal playback')
        tracks = await library_selection(client)
        media = [seed] + [uri for uri in tracks if uri != seed]
        await client.send_command('player_queues/play_media', queue_id=queue.queue_id,
                                 media=media, option=QueueOption.REPLACE, radio_mode=False, shuffle=False)


async def play_similar(player_id):
    async with connection() as client:
        queue = await active_queue(client, player_id)
        media = getattr(getattr(queue, 'current_item', None), 'media_item', None)
        if media is None or media.media_type != MediaType.TRACK:
            raise ValueError('Please play a song first.')
        await client.player_queues.play_media(queue.queue_id, media.uri,
                                             option=QueueOption.REPLACE_NEXT, radio_mode=True)


def describe_item(item):
    media = getattr(item, 'media_item', None)
    title = getattr(media, 'name', None) or getattr(item, 'name', 'an unknown track')
    artists = ', '.join(a.name for a in getattr(media, 'artists', []))
    return title + (' by ' + artists if artists else '')


async def queue_information(player_id, which):
    async with connection() as client:
        queue = await active_queue(client, player_id)
        if queue.current_index is None or queue.current_item is None:
            return 'There is no current song.'
        if which == 'current':
            item = queue.current_item
        elif which == 'next' and queue.next_item is not None:
            item = queue.next_item
        else:
            index = queue.current_index + (1 if which == 'next' else -1)
            if index < 0 or index >= queue.items:
                return 'There is no ' + which + ' song in this queue.'
            items = await client.player_queues.get_queue_items(queue.queue_id, limit=1, offset=index)
            item = items[0] if items else None
        if item is None:
            return 'There is no ' + which + ' song in this queue.'
        prefix = {'current': 'This is ', 'next': 'Next is ', 'previous': 'Before this was '}[which]
        return prefix + describe_item(item) + '.'


def same_album(left, right):
    return left is not None and right is not None and (left.uri == right.uri or (
        getattr(left, 'name', None) and getattr(right, 'name', None)
        and normalized(left.name) == normalized(right.name)))


async def shuffle_selection(player_id, kind):
    """Reorder only a finite album/playlist tail; never enable shuffle mode."""
    async with connection() as client:
        queue = await active_queue(client, player_id)
        current = getattr(queue, 'current_item', None)
        media = getattr(current, 'media_item', None)
        album = getattr(media, 'album', None)
        if current is None or queue.current_index is None or queue.is_dynamic:
            raise ValueError('Please start ' + ('an album' if kind == 'album' else 'a playlist') + ' first.')
        sources = getattr(queue, 'sources', [])
        if len(sources) != 1 or sources[0].media_type != MediaType(kind):
            raise ValueError('Please start ' + ('an album' if kind == 'album' else 'a playlist') + ' first.')
        if kind == 'album' and not same_album(sources[0], album):
            raise ValueError('Please start an album first.')
        if queue.shuffle_enabled:
            raise ValueError('This queue already has shuffle enabled. Please start it normally first.')
        boundary = max(queue.current_index, queue.index_in_buffer if queue.index_in_buffer is not None else -1)
        items = []
        offset = boundary + 1
        while offset < queue.items:
            page = await client.player_queues.get_queue_items(queue.queue_id, limit=500, offset=offset)
            if not page:
                break
            items.extend(page)
            offset += len(page)
        if len(items) < 2:
            raise ValueError('There are not enough unbuffered tracks left to shuffle.')
        if kind == 'album' and any(not same_album(getattr(getattr(item, 'media_item', None), 'album', None), album) for item in items):
            raise ValueError('The remaining queue contains other music. Please start an album first.')
        order = list(items)
        random.shuffle(order)
        if order == items:
            order = order[1:] + order[:1]
        ids = [item.queue_item_id for item in items]
        for target, item in enumerate(order):
            latest = await active_queue(client, player_id)
            if (latest.queue_id != queue.queue_id or latest.current_item is None
                    or latest.current_item.queue_item_id != current.queue_item_id
                    or latest.index_in_buffer != queue.index_in_buffer):
                raise ValueError('Playback advanced while shuffling. Please try again.')
            position = ids.index(item.queue_item_id)
            if position != target:
                await client.player_queues.move_item(queue.queue_id, item.queue_item_id, pos_shift=target-position)
                ids.insert(target, ids.pop(position))


async def shuffle_album(player_id):
    await shuffle_selection(player_id, 'album')


async def shuffle_playlist(player_id):
    await shuffle_selection(player_id, 'playlist')


MUSIC_INTENTS = {
    'PlaySongIntent': ('track', QueueOption.REPLACE),
    'PlayPlaylistIntent': ('playlist', QueueOption.REPLACE),
    'PlayArtistIntent': ('artist', QueueOption.REPLACE),
    'PlayAlbumIntent': ('album', QueueOption.REPLACE),
}
INFO_INTENTS = {'WhatsPlayingIntent': 'current', 'WhatsNextIntent': 'next', 'WhatsPreviousIntent': 'previous'}
EXTRA_INTENTS = {'PlaySimilarIntent', 'PlayRecommendedIntent', 'ShuffleAlbumIntent', 'ShufflePlaylistIntent'}


class VoiceMusicHandler(AbstractRequestHandler):
    def can_handle(self, handler_input):
        intent = getattr(handler_input.request_envelope.request, 'intent', None)
        return intent is not None and intent.name in (MUSIC_INTENTS.keys() | INFO_INTENTS.keys() | EXTRA_INTENTS)

    def handle(self, handler_input):
        intent = handler_input.request_envelope.request.intent
        slot = (intent.slots or {}).get('query')
        query = getattr(slot, 'value', '') or ''
        system = handler_input.request_envelope.context.system
        player = device_mapping.get_player_for_device(system.device.device_id)
        response = handler_input.response_builder
        if not player:
            return response.speak('Please map this Echo to its player in the bridge devices page first.').set_should_end_session(True).response
        if intent.name in MUSIC_INTENTS and not query.strip():
            return response.speak('Please say a song, artist, album or playlist name.').set_should_end_session(True).response
        if intent.name in INFO_INTENTS or intent.name in ('ShuffleAlbumIntent', 'ShufflePlaylistIntent'):
            async def immediate():
                async with asyncio.timeout(5):
                    if intent.name in INFO_INTENTS:
                        return await queue_information(player, INFO_INTENTS[intent.name])
                    kind = 'album' if intent.name == 'ShuffleAlbumIntent' else 'playlist'
                    await shuffle_selection(player, kind)
                    return 'The remaining ' + kind + ' tracks are shuffled.'
            try:
                message = asyncio.run(immediate())
            except ValueError as exc:
                message = str(exc)
            except Exception:
                logger.exception('Voice queue request failed')
                message = 'I could not finish that queue request. Please try again.'
            return response.speak(escape(message)).set_should_end_session(True).response

        def start_playback():
            async def run():
                async with asyncio.timeout(90):
                    if intent.name == 'PlayRecommendedIntent':
                        return await play_recommended(player)
                    if intent.name == 'PlaySimilarIntent':
                        return await play_similar(player)
                    kind, option = MUSIC_INTENTS[intent.name]
                    return await select_and_play(player, query, kind == 'playlist', kind=kind, option=option)
            try:
                asyncio.run(run())
                logger.info('Voice playback queued for %s', player)
            except Exception:
                logger.exception('Background voice playback failed for %s', player)
        threading.Thread(target=start_playback, name='voice-playback', daemon=True).start()
        message = 'Switching to similar songs after this track.' if intent.name == 'PlaySimilarIntent' else 'Starting your music.'
        return response.speak(message).set_should_end_session(True).response
