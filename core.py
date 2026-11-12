import calendar
import json
from pathlib import Path
import re
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, parse_qs, parse_qsl
from zoneinfo import ZoneInfo
from decimal import Decimal, ROUND_HALF_UP
from settings import settings

ROOT = Path(__file__).resolve().parent
MSK = ZoneInfo('Europe/Moscow')

def now():
    return datetime.now(timezone.utc)

def iso(value=None):
    return (value or now()).isoformat()

def reward(views, final=False):
    return ((views // 100000) + int(final and views % 100000 >= 90000)) * 500

def cutoff(value=None):
    local = (value or now()).astimezone(MSK)
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)

def video_id(url):
    p = urlparse(url.strip())
    if p.scheme not in ('http', 'https'):
        raise ValueError('Отправьте полную ссылку YouTube.')
    host = (p.hostname or '').lower()
    if host in ('youtu.be', 'www.youtu.be'):
        result = p.path.strip('/')
    elif host in ('youtube.com', 'www.youtube.com', 'm.youtube.com'):
        result = p.path.split('/shorts/', 1)[-1].split('/')[0] if '/shorts/' in p.path else parse_qs(p.query).get('v', [''])[0]
    else:
        raise ValueError('Нужна ссылка YouTube.')
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', result):
        raise ValueError('Не удалось определить видео.')
    return result

def contains_partner_link(description, expected):
    """Match full URL components, not substrings or lookalike domains."""
    def canonical(url):
        try:
            p = urlparse(url)
            if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:
                return None
            return (p.scheme.lower(), p.hostname.lower(), p.port,
                    p.path or '/', tuple(sorted(parse_qsl(p.query, keep_blank_values=True))), p.fragment)
        except ValueError:
            return None
    try:
        target = canonical((expected or '').strip())
        if not target: return False
        for raw in re.findall(r'https?://[^\s<>"\u200b]+', description or '', re.IGNORECASE):
            if canonical(raw) == target or canonical(raw.rstrip('.,;!?)]}»')) == target:
                return True
    except ValueError:
        return False
    return False

class Store:
    def __init__(self, path=None):
        settings.data_dir.mkdir(parents=True,exist_ok=True)
        self.db = sqlite3.connect(path or settings.database_path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY,name TEXT,joined TEXT,accepted INTEGER DEFAULT 0,role TEXT DEFAULT 'author',blocked INTEGER DEFAULT 0,wallet TEXT DEFAULT '',code TEXT,affiliate_id INTEGER,tracking_link TEXT);
        CREATE TABLE IF NOT EXISTS affiliate_links(user_id INTEGER,campaign_id INTEGER,link TEXT,PRIMARY KEY(user_id,campaign_id));
        CREATE TABLE IF NOT EXISTS video_campaigns(video_id TEXT PRIMARY KEY,campaign_id INTEGER);
        CREATE TABLE IF NOT EXISTS channels(id TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),title TEXT);
        CREATE TABLE IF NOT EXISTS videos(id TEXT PRIMARY KEY,user_id INTEGER REFERENCES users(id),title TEXT,published TEXT,deadline TEXT,views INTEGER DEFAULT 0,checked TEXT,status TEXT DEFAULT 'pending',reason TEXT DEFAULT '',earned INTEGER DEFAULT 0,final INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS ledger(id INTEGER PRIMARY KEY,user_id INTEGER,source TEXT,external_id TEXT,amount INTEGER,created TEXT,UNIQUE(source,external_id));
        CREATE TABLE IF NOT EXISTS snapshots(video_id TEXT,at TEXT,views INTEGER);
        CREATE TABLE IF NOT EXISTS payouts(id INTEGER PRIMARY KEY,user_id INTEGER,amount INTEGER,wallet TEXT,created TEXT,status TEXT DEFAULT 'review',tx TEXT UNIQUE,manager_id INTEGER);
        CREATE TABLE IF NOT EXISTS payout_items(payout_id INTEGER,ledger_id INTEGER UNIQUE,PRIMARY KEY(payout_id,ledger_id));
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,actor INTEGER,action TEXT,detail TEXT,at TEXT);
        CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY,user_id INTEGER,staff_id INTEGER,text TEXT,at TEXT);
        CREATE TABLE IF NOT EXISTS conversions(id TEXT PRIMARY KEY,user_id INTEGER,status TEXT,amount INTEGER,updated TEXT);
        CREATE TABLE IF NOT EXISTS partner_events(id TEXT PRIMARY KEY,campaign_id INTEGER,user_id INTEGER,affiliate_id INTEGER,source TEXT,status INTEGER,commission_micro INTEGER,turnover_micro INTEGER,order_id TEXT,updated TEXT);
        CREATE TABLE IF NOT EXISTS sync_state(name TEXT PRIMARY KEY,at TEXT,error TEXT DEFAULT '');
        CREATE TABLE IF NOT EXISTS video_link_checks(video_id TEXT PRIMARY KEY,expected_link TEXT,valid INTEGER,checked TEXT);
        CREATE TABLE IF NOT EXISTS review_claims(user_id INTEGER PRIMARY KEY,manager_id INTEGER,expires TEXT);
        CREATE UNIQUE INDEX IF NOT EXISTS unique_affiliate ON users(affiliate_id) WHERE affiliate_id IS NOT NULL;
        ''')
        if 'username' not in {r['name'] for r in self.db.execute('PRAGMA table_info(users)')}:
            self.db.execute("ALTER TABLE users ADD COLUMN username TEXT DEFAULT ''")
        if 'accepted_at' not in {r['name'] for r in self.db.execute('PRAGMA table_info(users)')}:
            self.db.execute('ALTER TABLE users ADD COLUMN accepted_at TEXT')
        self.db.execute('UPDATE users SET accepted_at=joined WHERE accepted=1 AND accepted_at IS NULL')
        for owner in self.db.execute("SELECT id FROM users WHERE role='owner'").fetchall():
            if owner['id'] not in settings.owner_ids:
                self.db.execute("UPDATE users SET role='author' WHERE id=?",(owner['id'],))
        for uid in settings.owner_ids:
            self.db.execute("UPDATE users SET role='owner' WHERE id=?",(uid,))
        self.db.commit()

    def rows(self, sql, args=()):
        return self.db.execute(sql, args).fetchall()

    def one(self, sql, args=()):
        return self.db.execute(sql, args).fetchone()

    def run(self, sql, args=()):
        with self.db:
            return self.db.execute(sql, args)

    def audit(self, actor, action, detail):
        self.run('INSERT INTO audit(actor,action,detail,at) VALUES(?,?,?,?)', (actor, action, str(detail), iso()))

    def user(self, uid, name='', username=None):
        self.run('INSERT OR IGNORE INTO users(id,name,joined,code) VALUES(?,?,?,?)', (uid, name, iso(), 'CINCIN-' + secrets.token_hex(5)))
        if name:
            self.run('UPDATE users SET name=? WHERE id=?', (name, uid))
        if username is not None:
            self.run('UPDATE users SET username=? WHERE id=?', (username, uid))
        if uid in settings.owner_ids:
            self.run("UPDATE users SET role='owner' WHERE id=?", (uid,))
        return self.one('SELECT * FROM users WHERE id=?', (uid,))

    def balance(self, uid):
        earned = self.one('SELECT COALESCE(SUM(amount),0) n FROM ledger WHERE user_id=?', (uid,))['n']
        paid = self.one("SELECT COALESCE(SUM(amount),0) n FROM payouts WHERE user_id=? AND status='paid'", (uid,))['n']
        return earned - paid

    def accept_terms(self, uid, at=None):
        at=iso(at)
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            user=self.one('SELECT accepted FROM users WHERE id=?',(uid,))
            if not user:raise ValueError('Сначала запустите бота.')
            if user['accepted']:return False
            self.db.execute('UPDATE users SET accepted=1,accepted_at=?,joined=? WHERE id=?',(at,at,uid))
            self.db.execute('INSERT INTO audit(actor,action,detail,at) VALUES(?,?,?,?)',(uid,'accept_rules','v1',at))
        return True

    def partner_links(self, uid):
        links = [dict(r) for r in self.rows('SELECT * FROM affiliate_links WHERE user_id=? ORDER BY campaign_id', (uid,))]
        profile = self.one('SELECT tracking_link FROM users WHERE id=?', (uid,))
        if profile and profile['tracking_link'] and not any(r['link']==profile['tracking_link'] for r in links):
            links.append({'user_id':uid,'campaign_id':settings.tracknow_campaign_id,'link':profile['tracking_link']})
        return links

    def save_partner_link(self, uid, campaign_id, link):
        with self.db:
            previous=self.one('SELECT tracking_link FROM users WHERE id=?',(uid,))
            if previous and previous['tracking_link'] and not self.one('SELECT 1 FROM affiliate_links WHERE user_id=? AND link=?',(uid,previous['tracking_link'])):
                parsed=parse_qs(urlparse(previous['tracking_link']).query).get('campaign_id',[])
                legacy_cid=int(parsed[0]) if parsed and parsed[0].isdigit() else settings.tracknow_campaign_id
                if legacy_cid!=campaign_id:
                    self.db.execute('INSERT OR IGNORE INTO affiliate_links VALUES(?,?,?)',(uid,legacy_cid,previous['tracking_link']))
            self.db.execute('INSERT INTO affiliate_links VALUES(?,?,?) ON CONFLICT(user_id,campaign_id) DO UPDATE SET link=excluded.link', (uid,campaign_id,link))
            self.db.execute('UPDATE users SET tracking_link=? WHERE id=?', (link,uid))

    def income(self, uid):
        return income_breakdown(self, uid)

    def claim_review(self, uid, actor, at=None):
        at = at or now()
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            existing = self.one('SELECT * FROM review_claims WHERE user_id=?', (uid,))
            if existing and existing['manager_id'] != actor and existing['expires'] > iso(at):
                manager = self.one('SELECT name FROM users WHERE id=?', (existing['manager_id'],))
                raise ValueError('Автор уже на проверке у менеджера ' + (manager['name'] if manager else str(existing['manager_id'])) + '. Выберите другого автора.')
            self.db.execute('INSERT INTO review_claims VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET manager_id=excluded.manager_id,expires=excluded.expires', (uid,actor,iso(at+timedelta(minutes=30))))

    def release_review(self, uid, actor):
        self.run('DELETE FROM review_claims WHERE user_id=? AND manager_id=?', (uid,actor))

    def payable_entries(self, uid, at=None, approved_only=True):
        at = at or now()
        entries = self.rows("SELECT l.* FROM ledger l WHERE user_id=? AND (created<? OR (amount<0 AND source IN ('cpa','turnover'))) AND NOT EXISTS(SELECT 1 FROM payout_items i WHERE i.ledger_id=l.id)", (uid,iso(cutoff(at))))
        return [e for e in entries if e['source'] != 'reversal'
                and not self.one("SELECT 1 FROM ledger WHERE source='reversal' AND external_id=?", (str(e['id']),))
                and (e['source'] != 'views' or self.one('SELECT 1 FROM videos WHERE id=? AND status '+("='approved'" if approved_only else "!='rejected'"), (e['external_id'].split(':')[0],)))]

    def payout_overview(self, uid, at=None):
        entries = self.payable_entries(uid,at,False)
        pending = {e['external_id'].split(':')[0] for e in entries if e['source']=='views'
                   and not self.one("SELECT 1 FROM videos WHERE id=? AND status='approved'",(e['external_id'].split(':')[0],))}
        return {'gross':sum(e['amount'] for e in entries),
                'approved':sum(e['amount'] for e in self.payable_entries(uid,at,True)),
                'pending_videos':pending,
                'prepared':self.one("SELECT * FROM payouts WHERE user_id=? AND status='review'",(uid,))}

    def cancel_payout(self, pid, actor):
        with self.db:
            p = self.one("SELECT * FROM payouts WHERE id=? AND status='review'",(pid,))
            if not p: raise ValueError('Подготовленная выплата не найдена.')
            self.db.execute('DELETE FROM payout_items WHERE payout_id=?',(pid,))
            self.db.execute("UPDATE payouts SET status='cancelled' WHERE id=?",(pid,))
        self.audit(actor,'cancel_payout',pid)

    def record_link_check(self, vid, expected, valid, at=None):
        at = at or now()
        old = self.one('SELECT * FROM video_link_checks WHERE video_id=?', (vid,))
        changed = old is None or old['valid'] != int(valid)
        with self.db:
            self.db.execute("INSERT INTO video_link_checks VALUES(?,?,?,?) ON CONFLICT(video_id) DO UPDATE SET expected_link=COALESCE(NULLIF(video_link_checks.expected_link,''),excluded.expected_link),valid=excluded.valid,checked=excluded.checked", (vid, expected, int(valid), iso(at)))
            if not valid:
                self.db.execute("UPDATE videos SET status='pending',reason=? WHERE id=? AND status!='rejected'", ('Партнёрская ссылка отсутствует в описании: требуется проверка менеджера', vid))
                for p in self.rows("SELECT DISTINCT p.id FROM payouts p JOIN payout_items i ON i.payout_id=p.id JOIN ledger l ON l.id=i.ledger_id WHERE p.status='review' AND l.source='views' AND l.external_id LIKE ?", (vid + ':%',)):
                    self.db.execute('DELETE FROM payout_items WHERE payout_id=?', (p['id'],))
                    self.db.execute("UPDATE payouts SET status='cancelled' WHERE id=?", (p['id'],))
                    self.db.execute('INSERT INTO audit(actor,action,detail,at) VALUES(0,?,?,?)', ('missing_link_cancel_payout', str(p['id']), iso(at)))
            elif old and not old['valid']:
                self.db.execute("UPDATE videos SET reason=? WHERE id=? AND status='pending'", ('Ссылка восстановлена: ожидается проверка менеджера', vid))
        return changed

    def apply_partner_events(self, events, campaign_id, rules, at=None):
        """Reconcile confirmed API commissions atomically; repeat snapshots are no-ops."""
        at = at or now()
        prepared = []
        for event in events:
            if int(event.get('campaignId', -1)) != campaign_id:
                raise ValueError('Tracknow вернул событие другой кампании. Импорт остановлен.')
            uid = self.one('SELECT id FROM users WHERE affiliate_id=?', (event.get('affiliateId'),))
            if not uid:
                continue
            source = rules.get(event.get('campaignPayoutId'))
            if source == 'ignore' and Decimal(str(event.get('affiliateCommission', 0))) == 0:
                continue
            if source not in ('cpa', 'turnover'):
                raise ValueError('Неизвестное правило вознаграждения Tracknow. Импорт остановлен.')
            external = event.get('ogId')
            if not external:
                raise ValueError('Tracknow не вернул постоянный ID операции.')
            currency = event.get('convertedCurrency') or event.get('currency')
            if currency != 'USD':
                raise ValueError('Для импорта нужна комиссия Tracknow в USD.')
            status = event.get('status')
            if status not in range(8):
                raise ValueError('Неизвестный статус Tracknow.')
            def micro(value):
                amount = Decimal(str(value))
                if not amount.is_finite() or amount < 0:
                    raise ValueError('Некорректная сумма операции Tracknow.')
                return int((amount * 1000000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            key = f'{campaign_id}:{external}'
            old = self.one('SELECT * FROM partner_events WHERE id=?', (key,))
            if old and (old['user_id'] != uid['id'] or old['source'] != source):
                raise ValueError('Изменилась привязка операции. Требуется проверка владельцем.')
            prepared.append((key, campaign_id, uid['id'], event['affiliateId'], source, status,
                             micro(event['affiliateCommission']), micro(event.get('amount', 0)),
                             str(event.get('orderId', '')), iso(at)))
        changes = []
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for row in prepared:
                self.db.execute('INSERT INTO partner_events VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,commission_micro=excluded.commission_micro,turnover_micro=excluded.turnover_micro,updated=excluded.updated', row)
            for uid, source in sorted({(r[2], r[4]) for r in prepared}):
                micros = self.one('SELECT COALESCE(SUM(commission_micro),0) n FROM partner_events WHERE user_id=? AND source=? AND status IN (0,7)', (uid, source))['n']
                target = int((Decimal(micros) / 10000).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                current = self.one('SELECT COALESCE(SUM(amount),0) n FROM ledger WHERE user_id=? AND source=?', (uid, source))['n']
                delta = target - current
                if not delta:
                    continue
                if delta < 0:
                    # A reversal invalidates a reserved amount before it can be paid.
                    for p in self.rows("SELECT id FROM payouts WHERE user_id=? AND status='review'", (uid,)):
                        self.db.execute('DELETE FROM payout_items WHERE payout_id=?', (p['id'],))
                        self.db.execute("UPDATE payouts SET status='cancelled' WHERE id=?", (p['id'],))
                        self.db.execute('INSERT INTO audit(actor,action,detail,at) VALUES(0,?,?,?)', ('partner_reversal_cancel_payout', str(p['id']), iso(at)))
                self.db.execute('INSERT INTO ledger(user_id,source,external_id,amount,created) VALUES(?,?,?,?,?)', (uid, source, secrets.token_hex(16), delta, iso(at)))
                changes.append((uid, source, delta))
            self.db.execute("INSERT INTO sync_state(name,at,error) VALUES('tracknow',?,'') ON CONFLICT(name) DO UPDATE SET at=excluded.at,error=''", (iso(at),))
        return changes

    def update_views(self, vid, views, at=None):
        at = at or now()
        v = self.one('SELECT * FROM videos WHERE id=?', (vid,))
        if not v or v['final'] or v['status'] == 'rejected':
            return 0
        deadline = datetime.fromisoformat(v['deadline'])
        # Never include views obtained after the 60-day window. Freeze the last pre-deadline sample.
        final = at >= deadline
        counted = v['views'] if final else views
        amount = reward(counted, final)
        old_blocks = v['earned'] // 500
        with self.db:
            self.db.execute('UPDATE videos SET views=?,checked=?,earned=?,final=? WHERE id=?', (counted, iso(at), amount, int(final), vid))
            self.db.execute('INSERT INTO snapshots VALUES(?,?,?)', (vid, iso(at), counted))
            for block in range(old_blocks + 1, amount // 500 + 1):
                self.db.execute("INSERT OR IGNORE INTO ledger(user_id,source,external_id,amount,created) VALUES(?,'views',?,?,?)", (v['user_id'], f'{vid}:{block}', 500, iso(at)))
        return max(0, amount - v['earned'])

    def reject(self, vid, reason, actor):
        v = self.one('SELECT * FROM videos WHERE id=?', (vid,))
        if not v:
            raise ValueError('Видео не найдено.')
        if self.one("SELECT 1 FROM payout_items i JOIN ledger l ON l.id=i.ledger_id JOIN payouts p ON p.id=i.payout_id WHERE l.external_id LIKE ? AND p.status='review'", (vid + ':%',)):
            raise ValueError('Сначала отмените подготовленную выплату автору.')
        with self.db:
            self.db.execute("UPDATE videos SET status='rejected',reason=? WHERE id=?", (reason, vid))
            unpaid = self.db.execute("SELECT l.id,l.amount FROM ledger l WHERE l.source='views' AND l.external_id LIKE ? AND l.amount>0 AND NOT EXISTS(SELECT 1 FROM payout_items i JOIN payouts p ON p.id=i.payout_id WHERE i.ledger_id=l.id AND p.status='paid')", (vid + ':%',)).fetchall()
            for entry in unpaid:
                self.db.execute("INSERT OR IGNORE INTO ledger(user_id,source,external_id,amount,created) VALUES(?,'reversal',?,?,?)", (v['user_id'], str(entry['id']), -entry['amount'], iso()))
        self.audit(actor, 'reject', f'{vid}: {reason}')

    def prepare_payout(self, uid, actor, at=None):
        at = at or now()
        if not 5 <= at.astimezone(MSK).day <= 10:
            raise ValueError('Подготовка выплат доступна с 5 по 10 число по Москве.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            u = self.one('SELECT * FROM users WHERE id=?', (uid,))
            if not u or u['blocked'] or not u['wallet']:
                raise ValueError('Автор заблокирован или не указал кошелёк.')
            if self.one("SELECT 1 FROM payouts WHERE user_id=? AND status='review'", (uid,)):
                raise ValueError('Выплата уже подготовлена.')
            entries = self.payable_entries(uid, at)
            amount = sum(e['amount'] for e in entries)
            if amount < 2000:
                raise ValueError('Одобренная сумма за прошлые месяцы меньше $20.')
            pid = self.db.execute('INSERT INTO payouts(user_id,amount,wallet,created,manager_id) VALUES(?,?,?,?,?)', (uid, amount, u['wallet'], iso(at), actor)).lastrowid
            self.db.executemany('INSERT INTO payout_items VALUES(?,?)', [(pid, e['id']) for e in entries])
        self.audit(actor, 'prepare_payout', pid)
        return pid

    def pay(self, pid, tx, actor):
        if not re.fullmatch('[a-fA-F0-9]{64}', tx):
            raise ValueError('Хэш TRON должен содержать 64 шестнадцатеричных символа.')
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            p = self.one("SELECT * FROM payouts WHERE id=? AND status='review'", (pid,))
            if not p:
                raise ValueError('Выплата уже завершена или не найдена.')
            self.db.execute("UPDATE payouts SET status='paid',tx=?,manager_id=? WHERE id=?", (tx.lower(), actor, pid))
        self.audit(actor, 'paid', pid)
        return p

def income_breakdown(store, uid):
    """All-time accruals, payouts and outstanding balance for each income source."""
    result = {source: {'earned': 0, 'paid': 0, 'balance': 0, 'pending_micro': 0, 'test_micro': 0} for source in ('views', 'cpa', 'turnover')}
    sql = """SELECT CASE WHEN l.source='reversal' THEN 'views' ELSE l.source END source,
             COALESCE(SUM(l.amount),0) earned FROM ledger l WHERE user_id=? GROUP BY 1"""
    for r in store.rows(sql, (uid,)):
        if r['source'] in result: result[r['source']]['earned'] = r['earned']
    for r in store.rows("SELECT l.source,SUM(l.amount) paid FROM ledger l JOIN payout_items i ON i.ledger_id=l.id JOIN payouts p ON p.id=i.payout_id WHERE l.user_id=? AND p.status='paid' GROUP BY l.source", (uid,)):
        if r['source'] in result: result[r['source']]['paid'] = r['paid']
    for r in store.rows('SELECT source,status,SUM(commission_micro) amount FROM partner_events WHERE user_id=? GROUP BY source,status', (uid,)):
        if r['source'] in result and r['status'] in (1,2,4,6): result[r['source']]['pending_micro'] += r['amount']
        if r['source'] in result and r['status'] == 5: result[r['source']]['test_micro'] += r['amount']
    for r in result.values(): r['balance'] = r['earned'] - r['paid']
    return result
