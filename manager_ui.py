"""Button-based manager workspace. Financial writes go through Store transactions."""
import asyncio
from datetime import datetime
from html import escape
import re
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Inline, ReplyKeyboardMarkup
from telegram.error import BadRequest
from core import now, iso, MSK, cutoff
from settings import settings

PAGE_SIZE = 8

class ManagerPanel:
    def __init__(self, runtime):
        self.r = runtime
        self.s = runtime.store

    def keyboard(self, uid):
        rows = [['👥 Авторы', '💸 День выплат'], ['🧾 Подготовленные выплаты', '📚 История выплат'], ['🔄 Обновить данные', '📊 Таблица'], ['📋 Условия пользования'], ['👤 Режим автора']]
        if self.s.user(uid)['role']=='owner': rows.insert(-1,['⚙️ Сотрудники'])
        return ReplyKeyboardMarkup(rows,resize_keyboard=True)

    async def show(self, update, text, rows):
        kwargs = {'parse_mode':'HTML','reply_markup':Inline(rows),'disable_web_page_preview':True}
        if update.callback_query:
            try: await update.callback_query.edit_message_text(text,**kwargs)
            except BadRequest as e:
                if 'not modified' not in str(e).lower(): raise
        else: await update.effective_message.reply_text(text,**kwargs)

    async def home(self, update, context):
        context.user_data.pop('state',None)
        context.user_data.pop('manager_tx',None)
        await update.effective_message.reply_text('🛠 Панель менеджера\n\n👥 Авторы — карточки, ролики, баланс и переписка.\n💸 День выплат — очередь авторов к расчёту за прошлые месяцы.\n\nПорядок: выбрать автора → проверить ролики → подготовить выплату → перевести USDT → указать хэш → подтвердить выплату.',reply_markup=self.keyboard(update.effective_user.id))

    def authors(self):
        return self.s.rows("""SELECT u.* FROM users u WHERE (u.role='author' AND u.accepted=1)
          OR u.affiliate_id IS NOT NULL OR EXISTS(SELECT 1 FROM channels c WHERE c.user_id=u.id)
          OR EXISTS(SELECT 1 FROM videos v WHERE v.user_id=u.id) ORDER BY u.joined DESC""")

    def queue(self):
        result=[]
        for u in self.authors():
            overview=self.s.payout_overview(u['id'])
            if not u['blocked'] and (overview['prepared'] or overview['gross']>=2000): result.append((u,overview))
        return result

    def label(self, u):
        name='@'+u['username'] if u['username'] else u['name'] or 'Без имени'
        return f"{u['id']} · {name[:24]}"

    async def profile(self, uid, context):
        try:
            chat=await context.bot.get_chat(uid)
            name=' '.join(x for x in (chat.first_name,chat.last_name) if x)
            self.s.user(uid,name,chat.username or '')
        except Exception:
            pass # Keep the saved profile if Telegram is temporarily unavailable.

    async def listing(self, update, context, kind='authors', page=0, notice=''):
        if kind=='queue':
            pairs=self.queue();title='💸 День выплат';intro='Авторы с суммой от $20 за прошлые месяцы, включая выплаты, ожидающие перевода. Проверять ролики можно в любой день; подготовка выплат — с 5-го по 10-е число.'
        else:
            pairs=[(u,None) for u in self.authors()];title='👥 Авторы';intro='Выберите автора, чтобы открыть его карточку.'
        page=max(0,min(page,max(0,(len(pairs)-1)//PAGE_SIZE)))
        await asyncio.gather(*(self.profile(u['id'],context) for u,_ in pairs[page*PAGE_SIZE:(page+1)*PAGE_SIZE]))
        text=[escape(notice),f'<b>{title}</b>',intro,f'Всего: {len(pairs)}'] if notice else [f'<b>{title}</b>',intro,f'Всего: {len(pairs)}']
        buttons=[]
        for u,o in pairs[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
            u=self.s.one('SELECT * FROM users WHERE id=?',(u['id'],))
            amount=o['prepared']['amount'] if o and o['prepared'] else o['gross'] if o else self.s.balance(u['id'])
            status=' · перевод' if o and o['prepared'] else ' · проверка' if o and o['pending_videos'] else ''
            buttons.append([Button(self.label(u)+' · '+self.r.money(amount)+status,callback_data=f'mgr:author:{u["id"]}')])
        if not pairs: text.append('Очередь пуста.' if kind=='queue' else 'Авторов пока нет.')
        nav=[]
        if page:nav.append(Button('⬅️ Назад',callback_data=f'mgr:{kind}:{page-1}'))
        if (page+1)*PAGE_SIZE<len(pairs):nav.append(Button('Далее ➡️',callback_data=f'mgr:{kind}:{page+1}'))
        if nav:buttons.append(nav)
        buttons += [[Button('🔄 Обновить список',callback_data=f'mgr:{kind}:{page}')],[Button('🏠 Панель менеджера',callback_data='mgr:home')]]
        await self.show(update,'\n\n'.join(text),buttons)

    async def author(self,update,context,uid,notice=''):
        await self.profile(uid,context)
        u=self.s.one('SELECT * FROM users WHERE id=?',(uid,))
        if not u:raise ValueError('Автор не найден.')
        info=self.s.payout_overview(uid);income=self.s.income(uid)
        claim=self.s.one('SELECT * FROM review_claims WHERE user_id=? AND expires>?',(uid,iso()))
        lines=[f'<b>👤 Автор {uid}</b>',escape(u['name'] or 'Без имени'),escape('@'+u['username'] if u['username'] else 'Никнейм не указан'),f'<b>Баланс: {self.r.money(self.s.balance(uid))}</b>']
        if notice:lines.insert(0,escape(notice))
        for source,label in [('views','Просмотры'),('cpa','Оформление карт'),('turnover','Оборот по картам')]:lines.append(f'{label}: {self.r.money(income[source]["balance"])}')
        lines += ['',f'За прошлые месяцы до проверки: {self.r.money(info["gross"])}',f'Одобрено к расчёту: {self.r.money(info["approved"])}',f'Роликов на проверке за расчётный период: {len(info["pending_videos"])}',f'Кошелёк TRC20: <code>{escape(u["wallet"] or "не указан")}</code>']
        if info['prepared']:lines.append('Подготовлено к переводу: '+self.r.money(info['prepared']['amount']))
        if u['blocked']:lines.append('⛔ Автор заблокирован.')
        if claim:
            manager=self.s.user(claim['manager_id'])
            lines.append('Проверяет: '+escape(manager['name'] or str(manager['id'])))
        try:
            author_url=await asyncio.to_thread(self.r.sheets.author_url,uid,self.r.store_snapshot())
            table_button=Button('📊 Таблица автора',url=author_url)
        except Exception:
            table_button=Button('📊 Таблица автора',callback_data=f'mgr:table:{uid}')
        rows=[[Button('🔎 Проверить ролики',callback_data=f'mgr:review:{uid}:0')],
              [Button('💬 Написать автору',callback_data=f'mgr:reply:{uid}')],
              [Button('📜 История выплат автора',callback_data=f'mgr:history:{uid}:0')],
              [table_button]]
        if info['prepared']:rows.insert(0,[Button('💸 Выплата готова к переводу',callback_data=f'mgr:payout:{info["prepared"]["id"]}')])
        elif not u['blocked']:rows.append([Button('💸 Подготовить выплату',callback_data=f'mgr:prepare:{uid}')])
        if claim and claim['manager_id']==update.effective_user.id:rows.append([Button('🔓 Освободить проверку',callback_data=f'mgr:release:{uid}')])
        if u['role']=='author':rows.append([Button('✅ Разблокировать' if u['blocked'] else '⛔ Заблокировать',callback_data=f'mgr:blockask:{uid}')])
        rows += [[Button('⬅️ Авторы',callback_data='mgr:authors:0'),Button('💸 День выплат',callback_data='mgr:queue:0')]]
        await self.show(update,'\n'.join(lines),rows)

    async def review(self,update,context,uid,index=0):
        self.s.claim_review(uid,update.effective_user.id)
        info=self.s.payout_overview(uid)
        videos=self.s.rows('SELECT * FROM videos WHERE user_id=? ORDER BY published,id',(uid,))
        # Due videos first, followed by all others for inspection without typing IDs.
        videos=sorted(videos,key=lambda v:(v['id'] not in info['pending_videos'],v['published']))
        if not videos:
            await self.author(update,context,uid,'У автора нет роликов. CPA и оборот проверяются по данным партнёрской программы.');return
        index=max(0,min(index,len(videos)-1));v=videos[index]
        state={'pending':'ожидает проверки','approved':'одобрен','rejected':'отклонён'}[v['status']]
        lines=[f'<b>🔎 Проверка автора {uid}</b>',f'Ролик {index+1} из {len(videos)}',escape(v['title']),f'https://youtube.com/shorts/{v["id"]}',f'Просмотры: {v["views"]:,}',f'Начислено: {self.r.money(v["earned"])}',f'Статус: {state}',escape(v['reason']), '', 'Проверьте баннер на протяжении всего ролика и соответствие контента правилам. Наличие ссылки дополнительно проверит бот.']
        rows=[]
        if v['status']!='rejected':rows.append([Button('✅ Одобрить',callback_data=f'mgr:approve:{v["id"]}'),Button('❌ Отклонить',callback_data=f'mgr:reject:{v["id"]}')])
        nav=[]
        if index:nav.append(Button('⬅️ Предыдущий',callback_data=f'mgr:review:{uid}:{index-1}'))
        if index+1<len(videos):nav.append(Button('Следующий ➡️',callback_data=f'mgr:review:{uid}:{index+1}'))
        if nav:rows.append(nav)
        rows += [[Button('👤 Карточка автора',callback_data=f'mgr:author:{uid}')],[Button('💸 Перейти к выплате',callback_data=f'mgr:prepare:{uid}')]]
        await self.show(update,'\n'.join(lines),rows)

    async def payments(self,update,context,uid=0,page=0,prepared=False):
        if prepared:
            all_rows=self.s.rows("SELECT * FROM payouts WHERE status='review' ORDER BY id")
            title='🧾 Подготовленные выплаты'
        else:
            all_rows=self.s.rows('SELECT * FROM payouts '+('WHERE user_id=? ' if uid else '')+'ORDER BY id DESC',(uid,) if uid else ())
            title='📚 История выплат'+(f' автора {uid}' if uid else '')
        rows=[];page=max(0,min(page,max(0,(len(all_rows)-1)//PAGE_SIZE)))
        for p in all_rows[page*PAGE_SIZE:(page+1)*PAGE_SIZE]:
            status={'review':'ожидает перевода','paid':'выплачено','cancelled':'отменено'}[p['status']]
            rows.append([Button(f'№{p["id"]} · {p["user_id"]} · {self.r.money(p["amount"])} · {status}',callback_data=f'mgr:payout:{p["id"]}')])
        prefix='prepared' if prepared else f'history:{uid}'
        nav=[]
        if page:nav.append(Button('⬅️ Назад',callback_data=f'mgr:{prefix}:{page-1}'))
        if (page+1)*PAGE_SIZE<len(all_rows):nav.append(Button('Далее ➡️',callback_data=f'mgr:{prefix}:{page+1}'))
        if nav:rows.append(nav)
        rows.append([Button('🏠 Панель менеджера',callback_data='mgr:home')])
        await self.show(update,f'<b>{title}</b>\nВсего: {len(all_rows)}'+('\nВыплат пока нет.' if not all_rows else ''),rows)

    async def payout(self,update,context,pid,notice=''):
        p=self.s.one('SELECT * FROM payouts WHERE id=?',(pid,))
        if not p:raise ValueError('Выплата не найдена.')
        lines=[escape(notice),f'<b>💸 Выплата №{pid}</b>',f'Автор: {p["user_id"]}',f'<b>Сумма: {self.r.money(p["amount"])} USDT</b>',f'Сеть: TRC20',f'Кошелёк: <code>{escape(p["wallet"])}</code>']
        for row in self.s.rows('SELECT source,SUM(amount) amount FROM ledger l JOIN payout_items i ON i.ledger_id=l.id WHERE payout_id=? GROUP BY source',(pid,)):
            lines.append({'views':'Просмотры','cpa':'Оформление карт','turnover':'Оборот по картам'}.get(row['source'],row['source'])+': '+self.r.money(row['amount']))
        rows=[]
        if p['status']=='review':
            lines.append('\nПереведите указанную сумму на этот кошелёк. После фактического перевода нажмите кнопку и отправьте хэш транзакции.')
            rows += [[Button('💸 Перевод выполнен — указать хэш',callback_data=f'mgr:hash:{pid}')],[Button('↩️ Отменить подготовку',callback_data=f'mgr:cancelask:{pid}')]]
        elif p['status']=='paid':lines += ['✅ Выплата завершена.',f'Хэш: <code>{escape(p["tx"])}</code>']
        else:lines.append('↩️ Подготовка отменена. Деньги с баланса не списаны.')
        rows += [[Button('👤 Карточка автора',callback_data=f'mgr:author:{p["user_id"]}')],[Button('💸 Вернуться в очередь',callback_data='mgr:queue:0')]]
        await self.show(update,'\n'.join(lines),rows)

    async def handle_callback(self,update,context):
        if not self.r.staff(update.effective_user.id):raise ValueError('Нет доступа к панели менеджера.')
        args=update.callback_query.data.split(':')[1:];action=args[0];actor=update.effective_user.id
        # Navigation cancels any pending text input without executing it.
        context.user_data.pop('state',None)
        if action=='home':await self.home(update,context)
        elif action in ('authors','queue'):await self.listing(update,context,action,int(args[1]))
        elif action=='author':await self.author(update,context,int(args[1]))
        elif action=='table':
            uid=int(args[1])
            url=await asyncio.to_thread(self.r.sheets.author_url,uid,self.r.store_snapshot())
            await self.show(update,f'📊 Таблица автора {uid}',[[Button('📊 Открыть вкладку автора',url=url)],[Button('⬅️ Карточка автора',callback_data=f'mgr:author:{uid}')]])
        elif action=='review':await self.review(update,context,int(args[1]),int(args[2]))
        elif action=='release':
            self.s.release_review(int(args[1]),actor);await self.author(update,context,int(args[1]))
        elif action=='reply':
            uid=int(args[1]);context.user_data['state']=('manager_reply',uid)
            await self.show(update,f'💬 Напишите сообщение автору {uid}. Оно будет отправлено через бот.',[[Button('❌ Отмена',callback_data=f'mgr:author:{uid}')]])
        elif action in ('approve','reject'):
            v=self.s.one('SELECT * FROM videos WHERE id=?',(args[1],))
            if not v or v['status']=='rejected':raise ValueError('Ролик уже отклонён или не найден.')
            self.s.claim_review(v['user_id'],actor)
            if action=='reject':
                context.user_data['state']=('manager_reject',v['id'])
                await self.show(update,'❌ Напишите причину отказа. Автор получит её в боте с указанием ролика.',[[Button('❌ Отмена',callback_data=f'mgr:author:{v["user_id"]}')]])
            else:
                remote=await asyncio.to_thread(self.r.youtube.videos,[v['id']])
                if not remote or remote[0]['status'].get('privacyStatus')!='public':raise ValueError('Видео недоступно. Одобрить его нельзя.')
                if not await self.r.check_video_link(context,v,remote[0]):raise ValueError('В описании нет партнёрской ссылки автора.')
                self.s.run("UPDATE videos SET status='approved',reason='' WHERE id=?",(v['id'],));self.s.audit(actor,'approve',v['id'])
                if v['status']!='approved':await self.notify(context,v['user_id'],'✅ Менеджер одобрил ролик: https://youtube.com/shorts/'+v['id'])
                await self.review(update,context,v['user_id'],0)
        elif action=='prepare':
            uid=int(args[1]);self.s.claim_review(uid,actor)
            await self.r.sync_partner(context,required=True)
            if self.r.refresh_lock.locked():
                async with self.r.refresh_lock:pass
            await self.r.refresh(context)
            info=self.s.payout_overview(uid)
            if info['prepared']:await self.payout(update,context,info['prepared']['id']);return
            if info['pending_videos']:
                await self.author(update,context,uid,'Сначала проверьте все ролики расчётного периода.');return
            pid=self.s.prepare_payout(uid,actor)
            await self.payout(update,context,pid)
        elif action=='payout':await self.payout(update,context,int(args[1]))
        elif action=='hash':
            pid=int(args[1]);p=self.s.one("SELECT * FROM payouts WHERE id=? AND status='review'",(pid,))
            if not p:raise ValueError('Выплата уже завершена или отменена.')
            self.s.claim_review(p['user_id'],actor);context.user_data['state']=('manager_hash',pid)
            await self.show(update,f'Отправьте хэш перевода {self.r.money(p["amount"])} USDT TRC20 для выплаты №{pid}.',[[Button('❌ Отмена',callback_data=f'mgr:payout:{pid}')]])
        elif action=='finish':
            pid=int(args[1]);saved=context.user_data.get('manager_tx')
            if not saved or saved[0]!=pid:raise ValueError('Сначала отправьте хэш транзакции.')
            p=self.s.one('SELECT * FROM payouts WHERE id=?',(pid,))
            self.s.claim_review(p['user_id'],actor)
            p=self.s.pay(pid,saved[1],actor)
            context.user_data.pop('manager_tx',None);self.s.release_review(p['user_id'],actor)
            await self.notify(context,p['user_id'],f'✅ Выплачено {self.r.money(p["amount"])} USDT TRC20.\nХэш: {saved[1]}\nОстаток баланса: {self.r.money(self.s.balance(p["user_id"]))}')
            await self.listing(update,context,'queue',notice=f'✅ Выплата №{pid} завершена. Автор убран из очереди, если расчёт за прошлые месяцы закрыт.')
        elif action in ('cancelask','cancelpay'):
            pid=int(args[1]);p=self.s.one("SELECT * FROM payouts WHERE id=? AND status='review'",(pid,))
            if not p:raise ValueError('Подготовленная выплата не найдена.')
            self.s.claim_review(p['user_id'],actor)
            if action=='cancelask':await self.show(update,'Отменить подготовку выплаты? Используйте это только если перевод ещё не выполнен.',[[Button('↩️ Да, отменить подготовку',callback_data=f'mgr:cancelpay:{pid}')],[Button('⬅️ Назад',callback_data=f'mgr:payout:{pid}')]])
            else:
                self.s.cancel_payout(pid,actor);self.s.release_review(p['user_id'],actor)
                await self.author(update,context,p['user_id'],'Подготовка отменена. Сумма остаётся на балансе.')
        elif action=='prepared':await self.payments(update,context,page=int(args[1]),prepared=True)
        elif action=='history':await self.payments(update,context,int(args[1]),int(args[2]))
        elif action in ('blockask','block'):
            uid=int(args[1]);u=self.s.one('SELECT * FROM users WHERE id=?',(uid,))
            if not u or u['role']!='author':raise ValueError('Можно блокировать только авторов.')
            if action=='blockask':await self.show(update,('Разблокировать' if u['blocked'] else 'Заблокировать')+f' автора {uid}?',[[Button('✅ Подтвердить',callback_data=f'mgr:block:{uid}:{int(not u["blocked"])}')],[Button('❌ Отмена',callback_data=f'mgr:author:{uid}')]])
            else:
                desired=int(args[2])
                if desired not in (0,1):raise ValueError('Неизвестное действие.')
                self.s.run('UPDATE users SET blocked=? WHERE id=?',(desired,uid));self.s.audit(actor,'block' if desired else 'unblock',uid)
                await self.author(update,context,uid)
        elif action=='staff':await self.employees(update,context)
        elif action=='addstaff':
            self.require_owner(actor);context.user_data['state']=('manager_add_role',)
            await self.show(update,'Отправьте числовой Telegram ID сотрудника. Он должен сначала запустить бота.',[[Button('❌ Отмена',callback_data='mgr:staff')]])
        elif action in ('roleremoveask','roleremove'):
            self.require_owner(actor);uid=int(args[1]);u=self.s.one("SELECT * FROM users WHERE id=? AND role='manager'",(uid,))
            if not u:raise ValueError('Менеджер не найден.')
            if action=='roleremoveask':await self.show(update,f'Снять роль менеджера у {escape(u["name"])} ({uid})?',[[Button('✅ Снять роль',callback_data=f'mgr:roleremove:{uid}')],[Button('❌ Отмена',callback_data='mgr:staff')]])
            else:
                self.s.run("UPDATE users SET role='author' WHERE id=?",(uid,));self.s.audit(actor,'unmanager',uid)
                self.s.run('DELETE FROM review_claims WHERE manager_id=?',(uid,));await self.employees(update,context)

    def require_owner(self,actor):
        if self.s.user(actor)['role']!='owner':raise ValueError('Это действие доступно только владельцу.')

    async def employees(self,update,context):
        self.require_owner(update.effective_user.id)
        rows=[];text=['<b>⚙️ Сотрудники</b>']
        for u in self.s.rows("SELECT * FROM users WHERE role IN ('owner','manager')"):
            text.append(escape(self.label(u))+' · '+('владелец' if u['role']=='owner' else 'менеджер'))
            if u['role']=='manager':rows.append([Button('Снять роль · '+self.label(u),callback_data=f'mgr:roleremoveask:{u["id"]}')])
        rows += [[Button('➕ Назначить менеджера',callback_data='mgr:addstaff')],[Button('🏠 Панель менеджера',callback_data='mgr:home')]]
        await self.show(update,'\n'.join(text),rows)

    async def notify(self,context,uid,text):
        try:await context.bot.send_message(uid,text)
        except Exception:self.r.log.warning('Could not deliver manager notification to user %s',uid)

    async def input(self,update,context):
        state=context.user_data.get('state')
        if not state or not state[0].startswith('manager_'):return False
        actor=update.effective_user.id
        if not self.r.staff(actor):raise ValueError('Нет доступа к панели менеджера.')
        body=update.effective_message.text.strip();kind=state[0]
        if kind=='manager_reply':
            if len(body)>3900:raise ValueError('Отправьте сообщение длиной до 3900 символов.')
            uid=state[1];await context.bot.send_message(uid,'Менеджер ChinChin Partners:\n'+body)
            self.s.run('INSERT INTO messages(user_id,staff_id,text,at) VALUES(?,?,?,?)',(uid,actor,body,iso()))
            context.user_data.pop('state',None);await self.author(update,context,uid,'✅ Сообщение отправлено.')
        elif kind=='manager_reject':
            v=self.s.one('SELECT * FROM videos WHERE id=?',(state[1],));self.s.claim_review(v['user_id'],actor)
            if not 5<=len(body)<=1000:raise ValueError('Причина отказа должна содержать от 5 до 1000 символов.')
            self.s.reject(v['id'],body,actor);context.user_data.pop('state',None)
            await self.notify(context,v['user_id'],f'❌ Отказ по ролику https://youtube.com/shorts/{v["id"]}\nПричина: {body}\nНевыплаченное вознаграждение за ролик отменено.')
            await self.review(update,context,v['user_id'],0)
        elif kind=='manager_hash':
            if not re.fullmatch('[a-fA-F0-9]{64}',body):raise ValueError('Хэш TRON должен содержать 64 шестнадцатеричных символа. Отправьте его повторно.')
            pid=state[1];p=self.s.one("SELECT * FROM payouts WHERE id=? AND status='review'",(pid,))
            if not p:raise ValueError('Выплата уже завершена или отменена.')
            self.s.claim_review(p['user_id'],actor);context.user_data['manager_tx']=(pid,body.lower());context.user_data.pop('state',None)
            await self.show(update,f'<b>Подтвердить выплату №{pid}?</b>\nАвтор: {p["user_id"]}\nСумма: {self.r.money(p["amount"])} USDT TRC20\nКошелёк: <code>{escape(p["wallet"])}</code>\nХэш: <code>{body.lower()}</code>\n\nПосле подтверждения сумма будет списана с баланса и сохранена в истории.',[[Button('✅ Подтвердить выплату',callback_data=f'mgr:finish:{pid}')],[Button('✏️ Изменить хэш',callback_data=f'mgr:hash:{pid}')],[Button('⬅️ К выплате',callback_data=f'mgr:payout:{pid}')]])
        elif kind=='manager_add_role':
            self.require_owner(actor)
            if not body.isdigit():raise ValueError('Отправьте числовой Telegram ID.')
            uid=int(body);u=self.s.one('SELECT * FROM users WHERE id=?',(uid,))
            if not u:raise ValueError('Сотрудник должен сначала запустить бота.')
            if u['role']=='owner':raise ValueError('Это владелец бота.')
            self.s.run("UPDATE users SET role='manager' WHERE id=?",(uid,));self.s.audit(actor,'manager',uid)
            context.user_data.pop('state',None);await self.employees(update,context)
        return True

    async def handle_text(self,update,context):
        text=update.effective_message.text
        if text=='👤 Режим автора':
            context.user_data.pop('state',None)
            await update.effective_message.reply_text('Меню автора. Вернуться можно кнопкой «Панель менеджера».',reply_markup=self.r.menu(update.effective_user.id));return True
        if text=='👥 Авторы':await self.listing(update,context)
        elif text=='💸 День выплат':
            await update.effective_message.reply_text('Обновляю начисления…')
            await self.r.sync_partner(context,required=True)
            await self.listing(update,context,'queue')
        elif text=='🧾 Подготовленные выплаты':await self.payments(update,context,prepared=True)
        elif text=='📚 История выплат':await self.payments(update,context)
        elif text=='⚙️ Сотрудники':await self.employees(update,context)
        elif text=='📊 Таблица':await self.show(update,'📊 Общая таблица авторов',[[Button('📊 Открыть таблицу',url=settings.spreadsheet_url)]])
        elif text=='🔄 Обновить данные':
            await update.effective_message.reply_text('Обновляю начисления, ролики и таблицу…')
            await self.r.sync_partner(context,required=True);await self.r.refresh(context)
            await update.effective_message.reply_text('✅ Информация обновлена.',reply_markup=self.keyboard(update.effective_user.id))
        else:return False
        context.user_data.pop('state',None)
        return True
