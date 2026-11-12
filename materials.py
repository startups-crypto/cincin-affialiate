"""Cards media kit with per-bot Telegram file-id caching."""
import asyncio
from contextlib import ExitStack
import hashlib
from pathlib import Path
from telegram import InputMediaVideo
from telegram.error import BadRequest

class MaterialKit:
    def __init__(self, store, root, bot_id):
        self.store=store
        self.root=Path(root)
        self.bot_id=str(bot_id)
        self.lock=asyncio.Lock()
        self.store.run('CREATE TABLE IF NOT EXISTS material_files(cache_key TEXT PRIMARY KEY,file_id TEXT)')

    async def send_cards(self, bot, uid):
        async with self.lock:
            assets=[self.root/'assets/cards/banner.mp4',self.root/'assets/cards/card.mp4']
            captions=[
                '🎬 Материалы CinCin Cards — пополнение рублями РФ через СБП.\n\nИспользуйте эти материалы для своих роликов. Баннер должен присутствовать на протяжении всего видео, а ваша партнёрская ссылка — в описании.',
                '🎬 Дополнительный материал CinCin Cards — пополнение рублями РФ через СБП.',
            ]
            keys=[];cached=[]
            for path in assets:
                if not path.is_file() or path.stat().st_size>50*1024*1024:
                    raise ValueError('Видеоматериалы временно недоступны.')
                digest=hashlib.sha256(path.read_bytes()).hexdigest()
                key=f'{self.bot_id}:cards:{path.name}:{digest}'
                row=self.store.one('SELECT file_id FROM material_files WHERE cache_key=?',(key,))
                keys.append(key);cached.append(row['file_id'] if row else None)
            with ExitStack() as stack:
                media=[InputMediaVideo(file_id or stack.enter_context(path.open('rb')),caption=caption,supports_streaming=True)
                       for path,file_id,caption in zip(assets,cached,captions)]
                try:
                    messages=await bot.send_media_group(chat_id=uid,media=media,write_timeout=120,read_timeout=120)
                except BadRequest:
                    for key,file_id in zip(keys,cached):
                        if file_id:self.store.run('DELETE FROM material_files WHERE cache_key=?',(key,))
                    raise
            for key,message in zip(keys,messages):
                if message.video:
                    self.store.run('INSERT INTO material_files VALUES(?,?) ON CONFLICT(cache_key) DO UPDATE SET file_id=excluded.file_id',(key,message.video.file_id))
            return messages
