import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

from music_assistant_models.errors import ProviderUnavailableError
from skill import voice_music as v


class RecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recommendation_uses_any_provider_and_random_track(self):
        rows = [NS(provider='spotify--1', item_id='recommendations')]
        c = NS(music=NS(recommendations=AsyncMock(return_value=rows)),
               send_command=AsyncMock(return_value=[{'media_type': 'album', 'uri': 'album'},
                    {'media_type': 'track', 'uri': 'one'}, {'media_type': 'track', 'uri': 'two'}]))
        with patch.object(v.random, 'shuffle') as shuffle, patch.object(v.random, 'choice', side_effect=lambda xs: xs[-1]):
            self.assertEqual(await v.recommended_seed(c), 'two')
            shuffle.assert_called_once_with(rows)
        self.assertEqual(c.send_command.call_args.kwargs['provider'], 'spotify--1')

    async def test_failed_row_falls_back_to_favorites(self):
        c = NS(music=NS(recommendations=AsyncMock(return_value=[NS(provider='broken', item_id='row')]),
                       get_library_tracks=AsyncMock(return_value=[NS(uri='local://track/1', available=True)])),
               send_command=AsyncMock(side_effect=ProviderUnavailableError('offline')))
        self.assertEqual(await v.recommended_seed(c), 'local://track/1')
        self.assertTrue(c.music.get_library_tracks.call_args.kwargs['favorite'])

    async def test_empty_recommendations_and_library_fail(self):
        c = NS(music=NS(recommendations=AsyncMock(return_value=[]), get_library_tracks=AsyncMock(return_value=[])))
        with self.assertRaisesRegex(ValueError, 'no recommended songs'):
            await v.recommended_seed(c)

    async def test_recommended_starts_radio_on_requesting_player(self):
        c = NS(providers=[NS(available=True, supported_features={v.ProviderFeature.SIMILAR_TRACKS})],
               player_queues=NS(get_active_queue=AsyncMock(return_value=NS(queue_id='dot3')), play_media=AsyncMock()))
        manager = AsyncMock(); manager.__aenter__.return_value = c
        with patch.object(v, 'MusicAssistantClient', return_value=manager), patch.object(v, 'get_env_secret', return_value='configured'), patch.object(v, 'recommended_seed', AsyncMock(return_value='spotify://track/seed')):
            await v.play_recommended('Echo Dot 3')
        c.player_queues.play_media.assert_awaited_once_with('dot3', 'spotify://track/seed', option=v.QueueOption.REPLACE, radio_mode=True)

    async def test_without_similar_support_plays_normal_selection(self):
        c = NS(providers=[], send_command=AsyncMock(),
               player_queues=NS(get_active_queue=AsyncMock(return_value=NS(queue_id='dot3')), play_media=AsyncMock()))
        manager = AsyncMock(); manager.__aenter__.return_value = c
        with patch.object(v, 'MusicAssistantClient', return_value=manager), patch.object(v, 'get_env_secret', return_value='configured'), patch.object(v, 'recommended_seed', AsyncMock(return_value='local://track/seed')), patch.object(v, 'library_selection', AsyncMock(return_value=['local://track/seed', 'local://track/other'])):
            await v.play_recommended('Echo Dot 3')
        self.assertEqual(c.send_command.call_args.kwargs['media'], ['local://track/seed', 'local://track/other'])
        self.assertFalse(c.send_command.call_args.kwargs['radio_mode'])
        c.player_queues.play_media.assert_not_awaited()
