"""Account connection and single-publication submission navigation.

Only verified accounts with an operational statistics provider appear for submission.
TikTok/Instagram remain unavailable until their OAuth integrations are configured.
"""
from html import escape
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup

PLATFORMS = {'youtube': '▶️ YouTube Shorts', 'instagram': '📸 Instagram Reels', 'tiktok': '🎵 TikTok'}

class AccountFlow:
    def __init__(self, store):
        self.store = store

    def accounts(self, uid):
        return self.store.rows('SELECT * FROM channels WHERE user_id=? ORDER BY title,id', (uid,))

    async def connect_menu(self, message):
        await message.reply_text('Выберите площадку для подключения аккаунта:', reply_markup=Markup([
            [Button(label, callback_data='accounts:connect:'+platform)] for platform,label in PLATFORMS.items()
        ]))

    async def choose_platform(self, message, uid):
        # Never offer an account that has not completed ownership verification.
        if not self.accounts(uid):
            await message.reply_text('Сначала подключите аккаунт кнопкой «➕ Подключить аккаунт».')
            return
        await message.reply_text('Выберите площадку, где опубликован ролик:', reply_markup=Markup([
            [Button(PLATFORMS['youtube'], callback_data='accounts:platform:youtube')]
        ]))

    async def choose_account(self, message, context, uid):
        rows = self.accounts(uid)
        if not rows:
            raise ValueError('Сначала подключите аккаунт YouTube.')
        if len(rows) == 1:
            await self.select_account(message, context, uid, rows[0]['id'])
            return
        await message.reply_text('Выберите аккаунт, где опубликован ролик:', reply_markup=Markup([
            [Button(row['title'][:70], callback_data='accounts:video:'+row['id'])] for row in rows
        ]))

    async def select_account(self, message, context, uid, account_id):
        account = self.store.one('SELECT * FROM channels WHERE id=? AND user_id=?', (account_id,uid))
        if not account:
            raise ValueError('Аккаунт не подключён к вашему профилю.')
        context.user_data['state'] = ('video', account_id)
        await message.reply_text('Отправьте одну ссылку на новый Shorts из аккаунта «'+account['title']+'». Срок подачи — 24 часа с публикации. /cancel — отмена.')

    async def callback(self, update, context):
        q = update.callback_query
        uid = update.effective_user.id
        parts = q.data.split(':',2)
        if len(parts) != 3:
            return
        _,action,key = parts
        context.user_data.pop('state',None)
        if action == 'connect':
            if key not in PLATFORMS:
                raise ValueError('Неизвестная площадка.')
            if key != 'youtube':
                await q.message.reply_text('Подключение '+PLATFORMS[key]+' готовится. Сейчас сбор просмотров доступен для YouTube Shorts. После подключения API здесь появится авторизация аккаунта.')
                return
            user = self.store.user(uid)
            context.user_data['state'] = ('channel',)
            await q.message.reply_text(
                '<b>▶️ Подключение аккаунта YouTube</b>\n\n'
                '1. Откройте описание своего канала.\n\n'
                '2. Добавьте этот код отдельной строкой:\n<code>'+escape(user['code'])+'</code>\n\n'
                '3. Сохраните изменения. Оставьте код на всё время работы с нами.\n\n'
                '4. Отправьте сюда ссылку на канал: youtube.com/@имя или youtube.com/channel/UC…\n\n'
                'Можно подключить несколько аккаунтов. /cancel — отмена.', parse_mode='HTML')
        elif action == 'platform' and key == 'youtube':
            await self.choose_account(q.message,context,uid)
        elif action == 'video':
            await self.select_account(q.message,context,uid,key)
