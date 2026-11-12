import tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock
from telegram import InputFile
from telegram.error import BadRequest
from core import Store
from materials import MaterialKit

class MaterialTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root=Path(self.folder.name)
        directory=self.root/'assets/cards';directory.mkdir(parents=True)
        (directory/'banner.mp4').write_bytes(b'video-one')
        (directory/'card.mp4').write_bytes(b'video-two')
        self.store=Store(':memory:')
        self.kit=MaterialKit(self.store,self.root,'test-bot')
        self.messages=[NS(video=NS(file_id='cached-one')),NS(video=NS(file_id='cached-two'))]
        self.bot=NS(send_media_group=AsyncMock(return_value=self.messages))

    async def test_first_upload_has_both_cards_videos_and_caches_ids(self):
        await self.kit.send_cards(self.bot,123)
        args=self.bot.send_media_group.call_args.kwargs
        self.assertEqual(args['chat_id'],123)
        self.assertEqual(len(args['media']),2)
        self.assertTrue(all(isinstance(v.media,InputFile) for v in args['media']))
        self.assertIn('СБП',args['media'][0].caption)
        self.assertEqual(len(self.store.rows('SELECT * FROM material_files')),2)

    async def test_repeat_uses_telegram_ids_instead_of_uploading(self):
        await self.kit.send_cards(self.bot,123)
        await self.kit.send_cards(self.bot,456)
        self.assertEqual([v.media for v in self.bot.send_media_group.call_args.kwargs['media']],['cached-one','cached-two'])

    async def test_cache_is_specific_to_bot(self):
        await self.kit.send_cards(self.bot,123)
        other=MaterialKit(self.store,self.root,'another-bot')
        await other.send_cards(self.bot,123)
        self.assertTrue(all(isinstance(v.media,InputFile) for v in self.bot.send_media_group.call_args.kwargs['media']))

    async def test_updated_file_does_not_reuse_old_content(self):
        await self.kit.send_cards(self.bot,123)
        (self.root/'assets/cards/card.mp4').write_bytes(b'new-video')
        await self.kit.send_cards(self.bot,123)
        media=self.bot.send_media_group.call_args.kwargs['media']
        self.assertEqual(media[0].media,'cached-one')
        self.assertIsInstance(media[1].media,InputFile)

    async def test_invalid_cached_ids_are_cleared_for_manual_retry(self):
        await self.kit.send_cards(self.bot,123)
        self.bot.send_media_group.side_effect=BadRequest('Wrong file identifier')
        with self.assertRaises(BadRequest):await self.kit.send_cards(self.bot,123)
        self.assertEqual(self.store.rows('SELECT * FROM material_files'),[])

    async def test_missing_asset_does_not_send_partial_album(self):
        (self.root/'assets/cards/card.mp4').unlink()
        with self.assertRaises(ValueError):await self.kit.send_cards(self.bot,123)
        self.bot.send_media_group.assert_not_awaited()
