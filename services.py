import json
import requests
import threading
from google.oauth2 import service_account
from google.auth.transport.requests import AuthorizedSession
from core import ROOT, iso, now, video_id, income_breakdown, linked_authors, has_partner_link
from terms import CARD_REWARD, CAMPAIGN_TITLE, RULES
from settings import settings

class RemoteError(ValueError):
    pass

def checked(response):
    if not response.ok:
        raise RemoteError(f'Сервис вернул HTTP {response.status_code}. Попробуйте позже.')
    result = response.json()
    if isinstance(result, dict) and result.get('error'):
        raise RemoteError('API отклонил запрос. Проверьте права доступа.')
    return result

class YouTube:
    def get(self, resource, **params):
        return checked(requests.get('https://www.googleapis.com/youtube/v3/' + resource, params={**params, 'key': settings.youtube_api_key}, timeout=25))

    def videos(self, ids):
        return self.get('videos', part='snippet,statistics,contentDetails,status', id=','.join(ids)).get('items', [])

    def channel(self, text):
        text = text.strip().rstrip('/')
        if '/channel/' in text:
            params = {'id': text.split('/channel/')[-1].split('/')[0]}
        elif '/@' in text or text.startswith('@'):
            params = {'forHandle': '@' + text.split('@')[-1].split('/')[0]}
        elif text.startswith('UC'):
            params = {'id': text}
        else:
            raise ValueError('Пришлите ссылку вида youtube.com/@имя или youtube.com/channel/UC…')
        items = self.get('channels', part='snippet', **params).get('items', [])
        if not items:
            raise ValueError('Канал не найден.')
        return items[0]

class Sheets:
    def __init__(self):
        creds = service_account.Credentials.from_service_account_info(settings.google_service_account, scopes=['https://www.googleapis.com/auth/spreadsheets'])
        self.session = AuthorizedSession(creds)
        self.url = 'https://sheets.googleapis.com/v4/spreadsheets/' + settings.google_spreadsheet_id
        self.lock=threading.RLock()

    def author_url(self, uid, store):
        if not has_partner_link(store,uid):
            raise RemoteError('Вкладка автора появится после получения партнёрской ссылки.')
        with self.lock:
            return self._author_url(uid,store)

    def _author_url(self, uid, store):
        title = f'Автор {uid}'
        def lookup():
            metadata = checked(self.session.get(self.url, params={'fields':'sheets.properties(sheetId,title)'}, timeout=30))
            return next((s['properties']['sheetId'] for s in metadata['sheets'] if s['properties']['title']==title),None)
        gid = lookup()
        if gid is None:
            self.sync(store)
            gid = lookup()
        if gid is None:
            raise RemoteError('Вкладка автора ещё не создана. Обновите данные и попробуйте снова.')
        return f'https://docs.google.com/spreadsheets/d/{settings.google_spreadsheet_id}/edit?gid={gid}#gid={gid}'

    def sync(self, store):
        with self.lock:
            return self._sync(store)

    def _sync(self, store):
        metadata = checked(self.session.get(self.url, timeout=30))
        titles = {s['properties']['title'] for s in metadata['sheets']}
        users = linked_authors(store)
        names = ['Обзор', 'Выплаты', 'Условия'] + ['Автор ' + str(u['id']) for u in users]
        missing = [name for name in names if name not in titles]
        if missing:
            checked(self.session.post(self.url + ':batchUpdate', json={'requests': [{'addSheet': {'properties': {'title': name, 'gridProperties': {'frozenRowCount': 1}}}} for name in missing]}, timeout=30))
        values = []
        overview = [['Telegram ID', 'Имя', 'Роль', 'Кошелёк TRC20', 'Баланс USD', 'Заблокирован', 'Обновлено UTC', 'Просмотры: остаток USD', 'CPA: остаток USD', 'Оборот: остаток USD', 'Просмотры: начислено USD', 'CPA: начислено USD', 'Оборот: начислено USD']]
        for u in users:
            income = income_breakdown(store, u['id'])
            overview.append([str(u['id']), u['name'], u['role'], u['wallet'], store.balance(u['id']) / 100, bool(u['blocked']), iso()] + [income[s]['balance']/100 for s in ('views','cpa','turnover')] + [income[s]['earned']/100 for s in ('views','cpa','turnover')])
            rows = [['Видео', 'Название', 'Публикация UTC', 'Конец 60 дней UTC', 'Просмотры', 'Начислено USD', 'Проверка', 'Причина отказа', 'Обновлено UTC', 'Учёт завершён', 'Партнёрская ссылка']]
            for v in store.rows('SELECT * FROM videos WHERE user_id=? ORDER BY published', (u['id'],)):
                checks = store.rows('SELECT valid FROM video_link_checks WHERE video_id=?', (v['id'],))
                link_status = ('Найдена' if checks[0]['valid'] else 'Не найдена') if checks else 'Не проверена'
                rows.append(['https://youtube.com/shorts/' + v['id'], v['title'], v['published'], v['deadline'], v['views'], v['earned']/100, v['status'], v['reason'], v['checked'], bool(v['final']), link_status])
            values.append({'range': f"'Автор {u['id']}'!A1", 'values': rows})
            summary = [['Источник дохода', 'Начислено USD', 'Выплачено USD', 'Остаток USD']]
            for source, label in [('views','Просмотры'),('cpa','Оформление карт'),('turnover','Оборот по картам — 0,1%')]:
                r = income[source]
                summary.append([label,r['earned']/100,r['paid']/100,r['balance']/100])
            # Empty cells also remove the two obsolete columns from existing reports.
            values.append({'range': f"'Автор {u['id']}'!L1", 'values': [row + ['', ''] for row in summary]})
        payments = [['ID', 'Автор', 'USD', 'Кошелёк', 'Статус', 'Хэш', 'Менеджер', 'Создано UTC']]
        payments += [[p['id'], str(p['user_id']), p['amount']/100, p['wallet'], p['status'], p['tx'] or '', str(p['manager_id']), p['created']] for p in store.rows('SELECT * FROM payouts')]
        values += [{'range': "'Обзор'!A1", 'values': overview}, {'range': "'Выплаты'!A1", 'values': payments}]
        values.append({'range': "'Условия'!A1", 'values': [
            ['Параметр', 'Условие'],
            ['Кампания', CAMPAIGN_TITLE],
            ['CPA', '$10 за подтверждённое оформление карты'],
            ['Процент от оборота', '0,1% от оборота по всем доступным операциям карт, открытых по партнёрской ссылке автора'],
            ['Суммирование', 'CPA + процент от оборота + одобренные просмотры'],
            ['Просмотры', '$5 за каждый полный блок 100 000 просмотров отдельного Shorts; для каждого видео свой период 60 дней с публикации'],
            ['Период 60 дней', 'Применяется только к просмотрам. Не удаляйте ролик, баннер и ссылку до окончания 60 дней.'],
            ['Выплаты', 'С 5-го по 10-е число каждого месяца за предыдущий месяц, от $20 суммарно'],
            ['Перевод', 'USDT TRC20'],
            ['Начисления', 'Подтверждённые комиссии автоматически добавляются в баланс.'],
            ['Полные условия', RULES],
        ]})
        checked(self.session.post(self.url + '/values:batchClear',json={'ranges':["'Обзор'!A2:M"]},timeout=30))
        checked(self.session.post(self.url + '/values:batchUpdate', json={'valueInputOption': 'RAW', 'data': values}, timeout=30))

class Tracknow:
    def __init__(self):
        self.token = settings.tracknow_api_key

    def require_access(self):
        if not settings.tracknow_enabled or not self.token or not settings.active_campaign_ids:
            raise RemoteError('Партнёрская программа пока не подключена. Обратитесь к менеджеру.')

    def get(self, path, **params):
        self.require_access()
        return checked(requests.get(settings.tracknow_base_url + '/' + path, params=params, headers={'Authorization': 'Bearer ' + self.token}, timeout=30))

    def link(self, affiliate_id, campaign_id):
        return self.get('affiliate/tracking-link', affiliateId=affiliate_id, campaignId=campaign_id)

    def write(self, method, path, **kwargs):
        self.require_access()
        return checked(requests.request(method, settings.tracknow_base_url + '/' + path,
                       headers={'Authorization': 'Bearer ' + self.token}, timeout=30, **kwargs))

    def register(self, uid, name, email):
        import secrets
        return self.write('POST', 'affiliate/register', json={
            'firstName': name[:80], 'lastName': 'Telegram Partner', 'email': email,
            'customId': f'cincin_banner_tg_{uid}', 'password': 'Aa1!' + secrets.token_hex(7),
            'sendCredentialsEmail': False, 'promotionalMaterialAgreed': False,
        })

    def assign(self, affiliate_id, campaign_id):
        return self.write('PUT', f'{campaign_id}/affiliates', params={'affiliateIds': affiliate_id, 'status': 'APPROVED'})

    def conversions(self, campaign_id):
        result = {}
        for page in range(10000):
            data = self.get('conversions', campaignSerialOids=campaign_id, pageNumber=page,
                            pageSize=100, sort='asc', sortField='creationDate')
            if not isinstance(data, dict) or not isinstance(data.get('content'), list):
                raise RemoteError('Неизвестный формат ответа Tracknow.')
            for event in data['content']:
                if event.get('campaignId') != campaign_id or not event.get('ogId'):
                    raise RemoteError('Tracknow вернул некорректную привязку события.')
                result[event['ogId']] = event
            if data.get('last') is True:
                return list(result.values())
        raise RemoteError('Превышен предел страниц Tracknow. Импорт не применён.')

    def fetch_income(self, campaign_id):
        data = self.get(f'{campaign_id}/payout', size=100)
        rows = data.get('content', [])
        rules = {}
        for r in rows:
            if r.get('goalValue') in ('card_issued', 'card_open') and r.get('paymentType') == 'CPA' and r.get('affiliateCommission') == 10:
                rules[r['identity']] = 'cpa'
            elif r.get('goalValue') in ('card_turnover', 'card_top_up', 'card_withdrawal', 'chargeback', 'exchange') and r.get('paymentType') == 'CPS' and r.get('affiliateCommission') == 0.1:
                rules[r['identity']] = 'turnover'
            elif r.get('goalValue') == 'card_blocking' and r.get('affiliateCommission') == 0:
                rules[r['identity']] = 'ignore'
        if not {'cpa','turnover'}.issubset(set(rules.values())):
            raise RemoteError('Правила CPA/оборота изменились. Проверьте кампанию перед импортом.')
        return self.conversions(campaign_id), rules
