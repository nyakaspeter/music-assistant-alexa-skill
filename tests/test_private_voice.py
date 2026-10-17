import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import private_access
from flask import Flask
from skill import voice_music


class PrivateAccessTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.before_request(private_access.check_private_access)
        self.app.add_url_rule('/', 'skill', lambda: 'ok', methods=['POST'])
        self.client = self.app.test_client()
        self.skill = 'mine'
    def invoke(self, user='owner', skill='mine', headers=None):
        with patch.object(private_access, 'get_allowed_skill_id', return_value=self.skill):
            return self.client.post('/', json={'context': {'System': {'application': {'applicationId': skill}, 'user': {'userId': user}}}}, headers=headers or {})
    def test_skill_required_and_owner_not_restricted(self):
        self.assertEqual(self.invoke().status_code, 200)
        self.assertEqual(self.invoke(user='someone-else').status_code, 200)
        self.assertEqual(self.invoke(skill='other-skill').status_code, 403)
    def test_fail_closed(self):
        self.skill = None
        self.assertEqual(self.invoke().status_code, 503)
    def test_no_simulator_bypass(self):
        self.assertEqual(self.invoke(headers={'X-Simulator-Bypass':'1'}).status_code, 403)

class SelectionTests(unittest.IsolatedAsyncioTestCase):
    def test_playlist_ambiguity(self):
        a,b=NS(name='Evening'),NS(name='Evening Jazz')
        self.assertIs(voice_music.choose_playlist([a,b],'evening'),a)
        with self.assertRaises(ValueError): voice_music.choose_playlist([a,b],'eve')
    async def check_play(self, playlist):
        item=NS(name='Evening' if playlist else 'Teardrop',uri='ytmusic://track/123')
        client=NS(send_command=AsyncMock(),music=NS(search=AsyncMock(return_value=NS(tracks=[item])),get_library_playlists=AsyncMock(return_value=[item])),player_queues=NS(get_active_queue=AsyncMock(return_value=NS(queue_id='echo-queue')),play_media=AsyncMock()))
        manager=AsyncMock();manager.__aenter__.return_value=client
        with patch.object(voice_music,'MusicAssistantClient',return_value=manager),patch.object(voice_music,'get_env_secret',return_value='configured'):
            await voice_music.select_and_play('echo',item.name,playlist)
        if playlist:
            args = client.send_command.call_args
            self.assertEqual(args.kwargs['media'], item.uri)
            self.assertFalse(args.kwargs['radio_mode'])
            self.assertFalse(args.kwargs['shuffle'])
            client.music.search.assert_not_called()
        else:
            args = client.player_queues.play_media.call_args
            self.assertEqual(args.args, ('echo-queue', item.uri))
            self.assertTrue(args.kwargs['radio_mode'])
    async def test_song_radio(self): await self.check_play(False)
    async def test_library_playlist(self): await self.check_play(True)

if __name__ == '__main__': unittest.main()
