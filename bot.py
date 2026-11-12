import asyncio
from datetime import datetime, timedelta
import logging
import re
import sqlite3
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters
from core import Store, ROOT, now, iso, video_id, MSK, contains_partner_link
from services import YouTube, Sheets, RemoteError, Tracknow
from terms import CARD_REWARD, RULES, WELCOME
from settings import settings
from partner_program import PartnerProgram
from materials import MaterialKit

settings.validate()

store = Store()
youtube = YouTube()
sheets = Sheets()
tracknow = Tracknow()
campaign_id = settings.tracknow_campaign_id
partner_program = PartnerProgram(store,tracknow,settings)
material_kit = MaterialKit(store,ROOT,settings.telegram_token.split(':',1)[0])
log = logging.getLogger('cincin')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(levelname)s %(message)s')
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)



def money(cents):
    return f'${cents / 100:.2f}'

def staff(uid):
    u = store.one('SELECT * FROM users WHERE id=?', (uid,))
    return bool(u and u['role'] in ('owner', 'manager') and not u['blocked'])

def menu(uid):
    rows = [['📹 Добавить ролик', '📊 Мои ролики'], ['📺 Мои каналы', '➕ Подключить канал'], ['💰 Баланс', '💳 CPA и оборот'], ['🔗 Партнёрская ссылка'], ['👛 Кошелёк', '📜 Выплаты'], ['🎨 Материалы', '📋 Условия пользования'], ['✉️ Менеджер']]
    if staff(uid):
        rows.append(['🛠 Панель менеджера'])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

async def start(update, context):
    u = store.user(update.effective_user.id, update.effective_user.full_name, update.effective_user.username or '')
    if staff(u['id']):
        await manager_panel.home(update, context)
        return
    if not u['accepted']:
        await update.message.reply_text(WELCOME + '\n\nПеред началом ознакомьтесь с условиями участия.\n\n' + RULES, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Принимаю условия', callback_data='accept')]]))
    else:
        await update.message.reply_text(WELCOME + '\n\nВыберите действие в меню.', reply_markup=menu(u['id']))

async def show_rules(update, context):
    context.user_data.pop('state',None)
    u=store.user(update.effective_user.id,update.effective_user.full_name,update.effective_user.username or '')
    if u['accepted']:
        await update.effective_message.reply_text(RULES)
    else:
        await update.effective_message.reply_text(RULES,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Принимаю условия',callback_data='accept')]]))

async def identify(update, context):
    await update.message.reply_text(f'Ваш Telegram ID: {update.effective_user.id}')

async def admin(update, context):
    uid = update.effective_user.id
    store.user(uid, update.effective_user.full_name, update.effective_user.username or '')
    if staff(uid):
        await manager_panel.home(update, context)

async def authors(update, context):
    if staff(update.effective_user.id):
        await manager_panel.listing(update, context)

async def review(update, context):
    if not staff(update.effective_user.id): return
    if not context.args: raise ValueError('Укажите /review ID_АВТОРА')
    uid = int(context.args[0])
    store.claim_review(uid, update.effective_user.id)
    for v in store.rows('SELECT * FROM videos WHERE user_id=? ORDER BY published', (uid,)):
        await update.message.reply_text(f"https://youtube.com/shorts/{v['id']}\n{v['title']}\nПросмотры: {v['views']:,}\nНачислено: {money(v['earned'])}\nПроверка: {v['status']}\n{v['reason']}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('✅ Одобрить', callback_data='approve:' + v['id']), InlineKeyboardButton('❌ Отклонить', callback_data='reject:' + v['id'])]]))
    if not store.one('SELECT 1 FROM videos WHERE user_id=?', (uid,)):
        await update.message.reply_text('У автора пока нет роликов.')

async def callback(update, context):
    q = update.callback_query
    await q.answer()
    uid = update.effective_user.id
    u = store.user(uid, update.effective_user.full_name, update.effective_user.username or '')
    if q.data.startswith('mgr:'):
        await manager_panel.handle_callback(update, context)
        return
    if q.data == 'cancel:affiliate_email':
        if (context.user_data.get('state') or ('',))[0] == 'affiliate_email':
            context.user_data.pop('state', None)
            await q.message.reply_text('Действие отменено.', reply_markup=menu(uid))
        await q.edit_message_reply_markup(reply_markup=None)
    elif q.data.startswith('materials:'):
        if not u['accepted'] or u['blocked']: raise ValueError('Сначала примите условия участия.')
        await send_materials(update,context,q.data.split(':',1)[1])
    elif q.data.startswith('partner:'):
        if not u['accepted'] or u['blocked']: raise ValueError('Сначала примите условия участия.')
        await show_link(update,context,None if q.data=='partner:choose' else q.data.split(':',1)[1])
    elif q.data == 'accept':
        first=store.accept_terms(uid)
        if first:
            await q.edit_message_reply_markup(reply_markup=None)
        await q.message.reply_text('✅ Условия приняты. Начните с подключения канала.' if first else '✅ Вы уже приняли условия. Выберите действие в меню.',reply_markup=menu(uid))
    elif q.data.startswith('income:') and u['accepted']:
        source = q.data.split(':', 1)[1]
        if source not in ('views','cpa','turnover'): return
        await sync_partner(context)
        await q.message.reply_text(source_text(uid, source))
    elif staff(uid) and q.data.startswith('approve:'):
        vid = q.data.split(':', 1)[1]
        v = store.one('SELECT * FROM videos WHERE id=?', (vid,))
        if not v or v['status'] == 'rejected':
            raise ValueError('Отклонённый ролик нельзя одобрить повторно. Обратитесь к владельцу.')
        store.claim_review(v['user_id'],uid)
        remote = await asyncio.to_thread(youtube.videos, [vid])
        if not remote or remote[0]['status'].get('privacyStatus') != 'public':
            raise ValueError('Видео недоступно. Одобрение невозможно.')
        if not await check_video_link(context, v, remote[0]):
            raise ValueError('В описании нет партнёрской ссылки автора. Одобрение невозможно.')
        store.run("UPDATE videos SET status='approved',reason='' WHERE id=?", (vid,))
        store.audit(uid, 'approve', vid)
        await q.message.reply_text('Ролик одобрен.')
        await context.bot.send_message(v['user_id'], f'Менеджер одобрил ролик: https://youtube.com/shorts/{vid}')
    elif staff(uid) and q.data.startswith('reject:'):
        v = store.one('SELECT * FROM videos WHERE id=?',(q.data.split(':',1)[1],))
        if not v:raise ValueError('Ролик не найден.')
        store.claim_review(v['user_id'],uid)
        context.user_data['state'] = ('reject', q.data.split(':', 1)[1])
        await q.message.reply_text('Напишите причину отказа. Автор получит её в боте. /cancel — отмена.')

async def cancel(update, context):
    context.user_data.pop('state', None)
    await update.message.reply_text('Действие отменено.', reply_markup=menu(update.effective_user.id))

async def add_video(uid, text):
    vid = video_id(text)
    if store.one('SELECT 1 FROM videos WHERE id=?', (vid,)):
        raise ValueError('Этот ролик уже добавлен.')
    items = await asyncio.to_thread(youtube.videos, [vid])
    if not items: raise ValueError('Видео недоступно или не найдено.')
    v = items[0]
    if v['status'].get('privacyStatus') != 'public':
        raise ValueError('Ролик должен быть общедоступным.')
    if not store.one('SELECT 1 FROM channels WHERE id=? AND user_id=?', (v['snippet']['channelId'], uid)):
        raise ValueError('Сначала подтвердите канал, на котором опубликован ролик.')
    published = datetime.fromisoformat(v['snippet']['publishedAt'].replace('Z', '+00:00'))
    u = store.one('SELECT * FROM users WHERE id=?', (uid,))
    links = store.partner_links(uid)
    if not links:
        raise ValueError('Сначала получите свою ссылку кнопкой «Партнёрская ссылка» и добавьте её в описание видео.')
    matched = next((link for link in links if contains_partner_link(v['snippet'].get('description', ''),link['link'])),None)
    if not matched:
        raise ValueError('В описании видео не найдена ваша партнёрская ссылка. Добавьте ссылку и отправьте ролик повторно в течение 24 часов после публикации.')
    if published < datetime.fromisoformat(u['joined']):
        raise ValueError('Принимаются только ролики после принятия условий.')
    if now() - published > timedelta(hours=24) or published > now():
        raise ValueError('Ссылку нужно подать в течение 24 часов после публикации.')
    duration = v['contentDetails']['duration']
    m = re.fullmatch(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration)
    seconds = sum(int(x or 0) * n for x,n in zip(m.groups(), [3600,60,1])) if m else 9999
    if seconds > 180:
        raise ValueError('Принимаются только Shorts длительностью до 3 минут.')
    # The public API does not expose a reliable isShort flag or banner presence: manager checks these.
    store.run('INSERT INTO videos(id,user_id,title,published,deadline) VALUES(?,?,?,?,?)', (vid, uid, v['snippet']['title'], iso(published), iso(published + timedelta(days=60))))
    store.record_link_check(vid, matched['link'], True)
    store.run('INSERT INTO video_campaigns VALUES(?,?)',(vid,matched['campaign_id']))
    store.update_views(vid, int(v['statistics'].get('viewCount', 0)))
    return '✅ Ролик добавлен. Партнёрская ссылка найдена в описании. Просмотры и наличие ссылки отслеживаются автоматически; формат Shorts и баннер проверит менеджер перед выплатой.'

async def text(update, context):
    uid = update.effective_user.id
    u = store.user(uid, update.effective_user.full_name, update.effective_user.username or '')
    message = update.message.text.strip()
    if message in ('📋 Условия пользования','📋 Правила'):
        await show_rules(update,context)
        return
    if u['blocked']:
        await update.message.reply_text('Участие заблокировано. Для уточнения обратитесь к менеджеру через /support.')
        return
    if staff(uid):
        if await manager_panel.handle_text(update, context): return
        if await manager_panel.input(update, context): return
    if not u['accepted']:
        await start(update, context); return
    state = context.user_data.get('state')
    buttons = {'📹 Добавить ролик','📊 Мои ролики','📺 Мои каналы','➕ Подключить канал','💰 Баланс','💳 CPA','💳 CPA и оборот','🔗 Партнёрская ссылка','👛 Кошелёк','📜 Выплаты','🎨 Материалы','📋 Правила','📋 Условия пользования','✉️ Менеджер','🛠 Панель менеджера'}
    if message in buttons:
        context.user_data.pop('state', None)
        state = None
    if state:
        kind = state[0]
        if kind == 'video':
            result = await add_video(uid, message)
            await update.message.reply_text(result)
        elif kind == 'channel':
            c = await asyncio.to_thread(youtube.channel, message)
            if u['code'] not in c['snippet'].get('description', ''):
                raise ValueError('Код пока не найден в описании канала. Добавьте его и повторите отправку ссылки.')
            existing = store.one('SELECT * FROM channels WHERE id=?', (c['id'],))
            if existing and existing['user_id'] != uid:
                raise ValueError('Канал уже принадлежит другому участнику. Напишите менеджеру.')
            store.run('INSERT OR IGNORE INTO channels VALUES(?,?,?)', (c['id'], uid, c['snippet']['title']))
            await update.message.reply_text('✅ Канал подключён! Оставьте код в описании канала на всё время работы с нами. Теперь можно добавлять ролики.')
        elif kind == 'wallet':
            if not valid_tron(message): raise ValueError('Неверный адрес TRON. Проверьте адрес USDT TRC20.')
            store.run('UPDATE users SET wallet=? WHERE id=?', (message, uid))
            store.audit(uid, 'wallet_changed', message)
            await update.message.reply_text('Кошелёк сохранён: ' + message)
        elif kind == 'affiliate_email':
            if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',message) or len(message)>254:
                raise ValueError('Отправьте корректный email для создания партнёрского аккаунта.')
            direction = state[1] if len(state)>1 else 'exchange'
            await show_link(update,context,direction,email=message)
            return
        elif kind == 'support':
            store.run('INSERT INTO messages(user_id,text,at) VALUES(?,?,?)', (uid, message, iso()))
            for manager in store.rows("SELECT id FROM users WHERE role IN ('owner','manager') AND blocked=0"):
                await context.bot.send_message(manager['id'], f'Сообщение от {u["name"]} ({uid}):\n{message}', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('💬 Ответить автору', callback_data=f'mgr:reply:{uid}')]]))
            await update.message.reply_text('Сообщение передано менеджерам.')
        elif kind == 'reject' and staff(uid):
            vid = state[1]
            if len(message) < 5: raise ValueError('Опишите причину отказа подробнее.')
            store.reject(vid, message, uid)
            v = store.one('SELECT * FROM videos WHERE id=?', (vid,))
            await context.bot.send_message(v['user_id'], f'Отказ по ролику https://youtube.com/shorts/{vid}\nПричина: {message}\nНевыплаченное вознаграждение за этот ролик отменено.')
            await update.message.reply_text('Отказ сохранён, автор уведомлён.')
        context.user_data.pop('state', None)
        return
    if message == '📹 Добавить ролик':
        context.user_data['state'] = ('video',)
        await update.message.reply_text('Отправьте ссылку на новый Shorts. Срок подачи — 24 часа с публикации. /cancel — отмена.')
    elif message == '➕ Подключить канал':
        context.user_data['state'] = ('channel',)
        await update.message.reply_text(f'<b>📺 Подключение канала</b>\n\n1. Откройте описание своего YouTube-канала для редактирования.\n\n2. Добавьте в описание этот код отдельной строкой:\n\n<code>{u["code"]}</code>\n\n3. Сохраните изменения. Оставьте код в описании на всё время работы с нами.\n\n4. После добавления кода отправьте ссылку на свой канал в этот чат.\n\nПодойдёт ссылка вида:\nyoutube.com/@имя\nили youtube.com/channel/UC…\n\nБот проверит код и подтвердит подключение канала.', parse_mode='HTML')
    elif message == '📺 Мои каналы':
        rows = store.rows('SELECT * FROM channels WHERE user_id=?', (uid,))
        await update.message.reply_text('\n'.join(f'{c["title"]}\nhttps://youtube.com/channel/{c["id"]}' for c in rows) or 'Каналов пока нет.')
    elif message == '📊 Мои ролики':
        rows = store.rows('SELECT * FROM videos WHERE user_id=? ORDER BY published DESC LIMIT 20', (uid,))
        for v in rows:
            await update.message.reply_text(f'https://youtube.com/shorts/{v["id"]}\nПросмотры: {v["views"]:,}\nРасчёт: {money(v["earned"])}\nПроверка: {v["status"]}\nКонец учёта: {v["deadline"]}\n{v["reason"]}')
        if not rows: await update.message.reply_text('Роликов пока нет.')
    elif message == '💰 Баланс':
        await sync_partner(context)
        income = store.income(uid)
        lines = [f'<b>💰 Сумма баланса: {money(store.balance(uid))}</b>', '', f"Просмотры: {money(income['views']['balance'])}", f"Оформление карт: {money(income['cpa']['balance'])}", f"Оборот по картам (0,1%): {money(income['turnover']['balance'])}", '', 'Просмотры требуют проверки менеджером. Выплаты от $20 суммарно, 5–10 числа за предыдущий месяц.', '', partner_sync_text()]
        profile = store.one('SELECT tracking_link FROM users WHERE id=?', (uid,))
        if not (profile['tracking_link'] or '').strip():
            lines += ['', '<code>Для учёта карт подключите партнёрский аккаунт кнопкой «Партнёрская ссылка».</code>']
        await update.message.reply_text('\n'.join(lines), parse_mode='HTML', reply_markup=income_buttons())
    elif message in ('💳 CPA', '💳 CPA и оборот'):
        await sync_partner(context)
        await update.message.reply_text(CARD_REWARD + '\n\n' + source_text(uid, 'cpa') + '\n\n' + source_text(uid, 'turnover'), reply_markup=income_buttons())
    elif message == '🔗 Партнёрская ссылка':
        await show_link(update, context)
    elif message == '👛 Кошелёк':
        context.user_data['state'] = ('wallet',)
        await update.message.reply_text('Текущий кошелёк: ' + (u['wallet'] or 'не указан') + '\nОтправьте адрес USDT в сети TRC20. /cancel — отмена.')
    elif message == '📜 Выплаты':
        rows = store.rows('SELECT * FROM payouts WHERE user_id=? ORDER BY id DESC LIMIT 20', (uid,))
        await update.message.reply_text('\n\n'.join(f'№{p["id"]}: {money(p["amount"])} · {p["status"]}\n{p["tx"] or "Ожидает перевода"}' for p in rows) or 'Выплат пока нет.')
    elif message == '🎨 Материалы':
        await send_materials(update,context,'all')
    elif message == '✉️ Менеджер': await support(update, context)
    elif message == '🛠 Панель менеджера': await admin(update, context)
    else: await update.message.reply_text('Выберите действие в меню.', reply_markup=menu(uid))

def valid_tron(address):
    import hashlib
    alphabet = '123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
    if len(address) != 34 or not address.startswith('T'): return False
    try:
        n = 0
        for ch in address: n = n * 58 + alphabet.index(ch)
        data = n.to_bytes(25, 'big')
        return data[0] == 0x41 and hashlib.sha256(hashlib.sha256(data[:-4]).digest()).digest()[:4] == data[-4:]
    except (ValueError, OverflowError): return False

def income_buttons():
    return InlineKeyboardMarkup([[InlineKeyboardButton('📹 Просмотры',callback_data='income:views')], [InlineKeyboardButton('💳 Оформление карт',callback_data='income:cpa')], [InlineKeyboardButton('🔄 Оборот по картам — 0,1%',callback_data='income:turnover')]])

def partner_sync_text():
    if not settings.tracknow_enabled: return 'Карточные начисления пока не подключены.'
    state = store.one("SELECT * FROM sync_state WHERE name='tracknow'")
    if not state: return 'Информация ещё не обновлена.'
    if state['error']: return 'Не удалось обновить информацию. Показаны последние сохранённые данные.'
    date = datetime.fromisoformat(state['at']).astimezone(MSK).strftime('%d.%m.%Y %H:%M')
    return f'Информация обновлена: {date} GMT+3.'

def source_text(uid, source):
    labels = {'views':'📹 Просмотры', 'cpa':'💳 Оформление карт — $10 за карту', 'turnover':'🔄 Оборот по картам — 0,1%'}
    data = store.income(uid)[source]
    lines = [labels[source], f"Начислено за всё время (с учётом отмен): {money(data['earned'])}", f"Выплачено: {money(data['paid'])}", f"Остаток: {money(data['balance'])}"]
    if source == 'views': lines.append('Предварительно до проверки контента менеджером.')
    else:
        lines += [f"Ожидает подтверждения: ${data['pending_micro']/1000000:.6f}", f"Тестовые операции: ${data['test_micro']/1000000:.6f} (не входят в баланс)"]
        if source == 'turnover':
            lines.append('Все доступные операции карт, открытых по вашей партнёрской ссылке. Доли цента накапливаются; баланс округляется после суммирования.')
        records = store.rows('SELECT status,order_id,commission_micro FROM partner_events WHERE user_id=? AND source=? ORDER BY updated DESC LIMIT 5', (uid, source))
        statuses = {0:'подтверждено',1:'удержание',2:'ожидает',3:'отклонено',4:'пересмотр',5:'тест',6:'обработка',7:'подтверждено в Tracknow'}
        if records:
            lines.append('Последние события:')
            lines += [f"{r['order_id']}: ${r['commission_micro']/1000000:.6f} · {statuses[r['status']]}" for r in records]
        lines.append(partner_sync_text())
    return '\n'.join(lines)

async def show_link(update, context, direction=None, email=None):
    if not settings.tracknow_enabled:
        raise ValueError('Партнёрская программа пока не подключена. Обратитесь к менеджеру.')
    uid=update.effective_user.id
    if direction is None:
        context.user_data.pop('state',None)
        choices=partner_program.directions()
        buttons=[[InlineKeyboardButton(item['button'],callback_data='partner:'+key)] for key,item in choices.items() if item['campaign_id']>0]
        await update.effective_message.reply_text('🔗 Куда направлять пользователей?\n\n💱 CinCin Exchange — пополнение криптовалютой.\n💳 CinCin Cards — пополнение рублями РФ через СБП.\n\nДля каждого направления выдаётся отдельная ссылка. Можно получить обе.',reply_markup=InlineKeyboardMarkup(buttons))
        return
    selected=partner_program.direction(direction)
    user=store.user(uid)
    if not user['affiliate_id'] and not email:
        context.user_data['state']=('affiliate_email',direction)
        await update.effective_message.reply_text('Для создания отдельного партнёрского аккаунта отправьте свой email.\n\nРекламные письма отключены.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('❌ Отмена',callback_data='cancel:affiliate_email')]]))
        return
    selected,link=await partner_program.issue(uid,direction,email)
    context.user_data.pop('state',None)
    await update.effective_message.reply_text('🔗 '+selected['title']+'\n'+selected['description']+'\n\nВаша партнёрская ссылка:\n'+link+'\n\n'+CARD_REWARD,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🎬 Материалы для СБП' if direction=='cards' else '🎬 Материалы для криптовалюты',callback_data='materials:'+direction)]]+[[InlineKeyboardButton('🔀 Другое направление',callback_data='partner:choose')]]),disable_web_page_preview=True)
    await send_materials(update,context,direction)

async def send_materials(update, context, direction):
    if direction not in ('cards','exchange','all'):
        raise ValueError('Неизвестное направление материалов.')
    try:
        await material_kit.send(context.bot,update.effective_user.id,direction)
    except Exception as error:
        log.warning('Material delivery failed: %s',type(error).__name__)
        await update.effective_message.reply_text('Не удалось отправить видео. Ваша партнёрская ссылка сохранена. Нажмите кнопку, чтобы повторить загрузку материалов.',reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton('🔄 Получить материалы',callback_data='materials:'+direction)]]))

async def support(update, context):
    context.user_data['state'] = ('support',)
    await update.message.reply_text('Напишите сообщение менеджеру. Ответ придёт сюда. /cancel — отмена.')

async def staff_command(update, context):
    actor = update.effective_user.id
    if not staff(actor): return
    command = update.message.text.split()[0].split('@')[0][1:]
    args = context.args
    if command == 'payouts':
        rows = store.rows("SELECT * FROM payouts WHERE status='review'")
        await update.message.reply_text('\n\n'.join(f'№{p["id"]} · Автор {p["user_id"]} · {money(p["amount"])}\nКошелёк: {p["wallet"]}\nПосле перевода: /paid {p["id"]} ХЭШ' for p in rows) or 'Подготовленных выплат нет.')
        return
    if command == 'sync':
        await update.message.reply_text('Обновляю статистику и таблицу…')
        await sync_partner(context, required=True)
        await refresh(context)
        await update.message.reply_text('Обновление завершено.')
        return
    if not args: raise ValueError('Укажите ID после команды.')
    target = int(args[0])
    if command == 'bindaffiliate':
        if store.user(actor)['role'] != 'owner': return
        if len(args) != 2: raise ValueError('Формат: /bindaffiliate TELEGRAM_ID AFFILIATE_ID')
        affiliate_id = int(args[1])
        user = store.one('SELECT * FROM users WHERE id=?', (target,))
        if not user: raise ValueError('Автор должен сначала запустить бота.')
        if user['affiliate_id'] and user['affiliate_id'] != affiliate_id:
            raise ValueError('У автора уже другая привязка. Автоматическая замена запрещена.')
        members=[]
        for cid in settings.active_campaign_ids:
            members.extend(await asyncio.to_thread(tracknow.get,f'{cid}/affiliates'))
        if not any(str(m.get('value')) == str(affiliate_id) for m in members):
            raise ValueError('Партнёр не найден среди участников нашей тестовой кампании. Сначала проверьте и добавьте его в Tracknow.')
        owner = store.one('SELECT id FROM users WHERE affiliate_id=?', (affiliate_id,))
        if owner and owner['id'] != target: raise ValueError('Этот партнёр уже привязан к другому автору.')
        store.run('UPDATE users SET affiliate_id=? WHERE id=?', (affiliate_id, target))
        store.audit(actor, 'bind_affiliate', f'{target}:{affiliate_id}')
        await sync_partner(context, required=True)
    elif command in ('manager','unmanager'):
        if store.user(actor)['role'] != 'owner': return
        user = store.one('SELECT * FROM users WHERE id=?', (target,))
        if not user: raise ValueError('Пользователь должен сначала запустить бота.')
        if user['role'] == 'owner': raise ValueError('Роль владельца здесь изменить нельзя.')
        store.run('UPDATE users SET role=? WHERE id=?', ('manager' if command=='manager' else 'author', target))
        store.audit(actor, command, target)
    elif command in ('block','unblock'):
        user = store.one('SELECT * FROM users WHERE id=?', (target,))
        if not user or user['role'] != 'author': raise ValueError('Можно блокировать только авторов.')
        store.run('UPDATE users SET blocked=? WHERE id=?', (int(command=='block'), target))
        store.audit(actor, command, target)
    elif command == 'reply':
        body = ' '.join(args[1:])
        if not body: raise ValueError('Добавьте текст ответа.')
        await context.bot.send_message(target, 'Менеджер CinCin Partners:\n' + body)
        store.run('INSERT INTO messages(user_id,staff_id,text,at) VALUES(?,?,?,?)', (target, actor, body, iso()))
    elif command == 'prepare':
        store.claim_review(target,actor)
        await sync_partner(context, required=True)
        if refresh_lock.locked():
            async with refresh_lock: pass
        await refresh(context)
        pid = store.prepare_payout(target, actor)
        await update.message.reply_text(f'Выплата №{pid} подготовлена. /payouts — реквизиты. После фактического перевода укажите /paid {pid} ХЭШ')
        return
    elif command == 'paid':
        if len(args)!=2: raise ValueError('Формат: /paid ID_ВЫПЛАТЫ ХЭШ')
        payment = store.one('SELECT * FROM payouts WHERE id=?',(target,))
        if not payment:raise ValueError('Выплата не найдена.')
        store.claim_review(payment['user_id'],actor)
        await sync_partner(context, required=True)
        p = store.pay(target, args[1], actor)
        store.release_review(p['user_id'],actor)
        await context.bot.send_message(p['user_id'], f'Выплачено {money(p["amount"])} USDT TRC20.\nХэш: {args[1]}\nОстаток баланса: {money(store.balance(p["user_id"]))}')
    elif command == 'cancelpay':
        with store.db:
            p = store.one("SELECT * FROM payouts WHERE id=? AND status='review'", (target,))
            if not p: raise ValueError('Подготовленная выплата не найдена.')
            store.db.execute('DELETE FROM payout_items WHERE payout_id=?', (target,))
            store.db.execute("UPDATE payouts SET status='cancelled' WHERE id=?", (target,))
        store.audit(actor, command, target)
    await update.message.reply_text('Готово.')

refresh_lock = asyncio.Lock()
partner_lock = asyncio.Lock()

async def sync_partner(context, required=False):
    if not settings.tracknow_enabled: return
    if partner_lock.locked():
        if required:
            async with partner_lock: pass
        else: return
    async with partner_lock:
        try:
            snapshots = []
            for cid in settings.active_campaign_ids:
                events,rules=await asyncio.to_thread(tracknow.fetch_income,cid)
                snapshots.append((cid,events,rules))
            changes=[]
            for cid,events,rules in snapshots:
                changes.extend(store.apply_partner_events(events,cid,rules))
        except Exception as error:
            previous = store.one("SELECT * FROM sync_state WHERE name='tracknow'")
            code = type(error).__name__
            store.run("INSERT INTO sync_state(name,at,error) VALUES('tracknow','',?) ON CONFLICT(name) DO UPDATE SET error=excluded.error", (code,))
            log.warning('Tracknow sync failed: %s', code)
            if not previous or previous['error'] != code:
                for manager in store.rows("SELECT id FROM users WHERE role IN ('owner','manager') AND blocked=0"):
                    try: await context.bot.send_message(manager['id'], 'Не удалось обновить Tracknow. Последние данные сохранены; подготовка и подтверждение выплат приостановлены до восстановления импорта.')
                    except Exception: pass
            if required: raise ValueError('Сначала необходимо успешно обновить Tracknow. Выплата не изменена.') from None
            return
        totals={}
        for uid,source,delta in changes:
            totals[(uid,source)]=totals.get((uid,source),0)+delta
        for (uid,source),delta in totals.items():
            if not delta: continue
            name = 'Оформление карт' if source == 'cpa' else 'Оборот по картам (0,1%)'
            try:
                await context.bot.send_message(uid, f'{name}: {"начисление" if delta>0 else "корректировка"} {money(delta)}.\nОбщий баланс: {money(store.balance(uid))}\nПодробности — в разделе «Баланс».')
            except Exception: log.warning('Could not deliver income notification to user %s', uid)

async def refresh(context):
    if refresh_lock.locked(): return
    async with refresh_lock:
        videos = store.rows("""SELECT v.* FROM videos v WHERE v.status!='rejected' AND
            (v.final=0 OR EXISTS(SELECT 1 FROM ledger l WHERE l.source='views' AND l.external_id LIKE v.id||':%'
             AND NOT EXISTS(SELECT 1 FROM payout_items i JOIN payouts p ON p.id=i.payout_id WHERE i.ledger_id=l.id AND p.status='paid')
             AND NOT EXISTS(SELECT 1 FROM ledger r WHERE r.source='reversal' AND r.external_id=CAST(l.id AS TEXT))))""")
        for offset in range(0, len(videos), 50):
            batch = videos[offset:offset+50]
            items = await asyncio.to_thread(youtube.videos, [v['id'] for v in batch])
            found = {v['id']: v for v in items}
            for v in batch:
                remote = found.get(v['id'])
                if not remote or remote['status'].get('privacyStatus') != 'public':
                    # Temporary API absence is not proof of deletion. Flag for manual review.
                    if v['reason'] != 'Видео недоступно: требуется ручная проверка':
                        store.run("UPDATE videos SET status='pending',reason=? WHERE id=?", ('Видео недоступно: требуется ручная проверка', v['id']))
                        store.audit(0, 'video_unavailable', v['id'])
                        for manager in store.rows("SELECT id FROM users WHERE role IN ('owner','manager') AND blocked=0"):
                            try:
                                await context.bot.send_message(manager['id'], f'Видео недоступно: https://youtube.com/shorts/{v["id"]}\nАвтор {v["user_id"]}. Одобрение снято до ручной проверки.')
                            except Exception:
                                log.warning('Could not deliver video availability notification')
                    continue
                if not await check_video_link(context, v, remote):
                    continue
                delta = store.update_views(v['id'], int(remote['statistics'].get('viewCount', 0)))
                if delta:
                    try:
                        await context.bot.send_message(v['user_id'], f'Новый порог просмотров! Предварительно начислено {money(delta)} за ролик https://youtube.com/shorts/{v["id"]}. Выплата после проверки менеджером.')
                    except Exception:
                        log.warning('Could not deliver threshold notification to user %s', v['user_id'])
        await asyncio.to_thread(sheets.sync, store_snapshot())

async def check_video_link(context, video, remote):
    previous = store.one('SELECT * FROM video_link_checks WHERE video_id=?', (video['id'],))
    links = store.partner_links(video['user_id'])
    matched = next((link['link'] for link in links if contains_partner_link(remote['snippet'].get('description',''),link['link'])), '')
    expected = (previous['expected_link'] if previous else None) or matched or (links[0]['link'] if links else '')
    valid = contains_partner_link(remote['snippet'].get('description', ''), expected)
    changed = store.record_link_check(video['id'], expected, valid)
    if changed and (not valid or (previous and not previous['valid'])):
        url = 'https://youtube.com/shorts/' + video['id']
        if valid:
            message = '✅ Партнёрская ссылка снова найдена в описании видео. Ролик ожидает повторной проверки менеджером.\n' + url
        else:
            message = '⚠️ В описании видео не найдена ваша партнёрская ссылка. Одобрение и новые начисления за ролик приостановлены до проверки менеджером.\n' + url
        store.audit(0, 'video_link_restored' if valid else 'video_link_missing', video['id'])
        recipients = {video['user_id']} | {m['id'] for m in store.rows("SELECT id FROM users WHERE role IN ('owner','manager') AND blocked=0")}
        for recipient in recipients:
            try:
                await context.bot.send_message(recipient, message + (f'\nАвтор: {video["user_id"]}' if recipient != video['user_id'] else ''))
            except Exception:
                log.warning('Could not deliver link check notification to user %s', recipient)
    return valid

def store_snapshot():
    # Sheets runs in a worker thread and must use its own SQLite connection.
    return ThreadStore()

class ThreadStore:
    def rows(self, sql, args=()):
        with sqlite3.connect(settings.database_path) as db:
            db.row_factory = sqlite3.Row
            return db.execute(sql, args).fetchall()
    def balance(self, uid):
        earned = self.rows('SELECT COALESCE(SUM(amount),0) n FROM ledger WHERE user_id=?', (uid,))[0]['n']
        paid = self.rows("SELECT COALESCE(SUM(amount),0) n FROM payouts WHERE user_id=? AND status='paid'", (uid,))[0]['n']
        return earned-paid

async def errors(update, context):
    error = context.error
    log.warning('Operation failed: %s', type(error).__name__)
    text = str(error) if isinstance(error, ValueError) else 'Не удалось выполнить действие. Попробуйте позже или напишите менеджеру.'
    if update and update.effective_message:
        await update.effective_message.reply_text(text)

async def initialized(app):
    await app.bot.set_my_commands([('start','Главное меню'),('id','Мой Telegram ID'),('support','Написать менеджеру'),('terms','Условия пользования'),('cancel','Отменить действие'),('admin','Панель менеджера')])
    app.job_queue.run_repeating(refresh, interval=settings.views_interval, first=15, job_kwargs={'max_instances': 1})
    app.job_queue.run_repeating(sync_partner, interval=settings.partner_interval, first=3, job_kwargs={'max_instances': 1})
    log.info('Local bot started; views interval %s seconds', settings.views_interval)
    log.info('User database directory: %s', settings.data_dir)

def main():
    # Single process lock prevents competing Telegram polling and payouts.
    import fcntl
    lock = (settings.data_dir / 'process.lock').open('w')
    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('Бот уже запущен.')
    app = Application.builder().token(settings.telegram_token).post_init(initialized).build()
    for cmd, fn in [('start',start),('id',identify),('terms',show_rules),('admin',admin),('authors',authors),('review',review),('cancel',cancel),('support',support)]:
        app.add_handler(CommandHandler(cmd, fn))
    for cmd in ['manager','unmanager','block','unblock','reply','prepare','paid','payouts','cancelpay','sync','bindaffiliate']:
        app.add_handler(CommandHandler(cmd, staff_command))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text))
    app.add_error_handler(errors)
    app.run_polling(drop_pending_updates=False)

from manager_ui import ManagerPanel
import sys
manager_panel = ManagerPanel(sys.modules[__name__])

if __name__ == '__main__': main()
