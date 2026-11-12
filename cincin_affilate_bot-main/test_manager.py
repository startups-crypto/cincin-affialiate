import asyncio
from datetime import timedelta
from types import SimpleNamespace as NS
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch
from core import Store, iso
from manager_ui import ManagerPanel
import test_core as fixtures

AT=fixtures.AT

class ClaimsTests(TestCase):
    def test_claims_exclude_other_managers_and_expire(self):
        s=Store(':memory:');s.user(1,'Author');s.user(10,'First');s.user(11,'Second')
        s.claim_review(1,10,AT)
        with self.assertRaises(ValueError):s.claim_review(1,11,AT)
        s.claim_review(1,11,AT+timedelta(minutes=31))
        self.assertEqual(s.one('SELECT manager_id FROM review_claims')['manager_id'],11)

    def test_claim_release_only_by_holder(self):
        s=Store(':memory:');s.claim_review(1,10,AT)
        s.release_review(1,11);self.assertIsNotNone(s.one('SELECT * FROM review_claims'))
        s.release_review(1,10);self.assertIsNone(s.one('SELECT * FROM review_claims'))

class PanelTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock=patch('core.now',return_value=AT);self.clock.start();self.addCleanup(self.clock.stop)
        self.s=Store(':memory:')
        self.s.user(1,'Test Author','test_author')
        self.s.run("UPDATE users SET accepted=1,wallet='T-test-wallet' WHERE id=1")
        self.s.user(10,'Manager');self.s.run("UPDATE users SET role='manager' WHERE id=10")
        self.s.user(11,'Second Manager');self.s.run("UPDATE users SET role='manager' WHERE id=11")
        self.s.run("INSERT INTO ledger(user_id,source,external_id,amount,created) VALUES(1,'cpa','fixture',2500,?)",(iso(AT-timedelta(days=15)),))
        self.r=NS(store=self.s,money=lambda n:f'${n/100:.2f}',staff=lambda uid:self.s.user(uid)['role'] in ('owner','manager'),
                  sync_partner=AsyncMock(),refresh=AsyncMock(),refresh_lock=asyncio.Lock(),
                  check_video_link=AsyncMock(return_value=True),youtube=NS(videos=Mock()),log=NS(warning=Mock()))
        self.panel=ManagerPanel(self.r)
        self.context=NS(user_data={},bot=NS(send_message=AsyncMock()))

    def update(self,action=None,body='',actor=10):
        message=NS(text=body,reply_text=AsyncMock())
        callback=NS(data=action,edit_message_text=AsyncMock()) if action else None
        return NS(effective_user=NS(id=actor),effective_message=message,callback_query=callback)

    async def test_author_list_has_id_username_and_balance(self):
        update=self.update()
        await self.panel.listing(update,self.context)
        keyboard=update.effective_message.reply_text.call_args.kwargs['reply_markup']
        text=keyboard.inline_keyboard[0][0].text
        self.assertIn('1 · @test_author',text);self.assertIn('$25.00',text)

    async def test_prepared_author_stays_in_queue(self):
        self.s.prepare_payout(1,10,AT)
        self.assertEqual([u['id'] for u,_ in self.panel.queue()],[1])

    async def test_full_button_payment_and_queue_removal(self):
        await self.panel.handle_callback(self.update('mgr:prepare:1'),self.context)
        p=self.s.one("SELECT * FROM payouts WHERE status='review'")
        await self.panel.handle_callback(self.update(f'mgr:hash:{p["id"]}'),self.context)
        self.assertEqual(self.context.user_data['state'],('manager_hash',p['id']))
        await self.panel.input(self.update(body='a'*64),self.context)
        self.assertEqual(self.s.balance(1),2500) # Entering the hash alone does not pay.
        await self.panel.handle_callback(self.update(f'mgr:finish:{p["id"]}'),self.context)
        self.assertEqual(self.s.balance(1),0);self.assertEqual(self.panel.queue(),[])
        self.context.bot.send_message.assert_awaited()
        with self.assertRaises(ValueError):await self.panel.handle_callback(self.update(f'mgr:finish:{p["id"]}'),self.context)
        self.assertEqual(self.s.balance(1),0)

    async def test_second_manager_cannot_process_claimed_payment(self):
        self.s.claim_review(1,10)
        with self.assertRaises(ValueError):
            await self.panel.handle_callback(self.update('mgr:prepare:1',actor=11),self.context)
        self.assertIsNone(self.s.one('SELECT * FROM payouts'))

    async def test_hash_validation_does_not_change_balance(self):
        p=self.s.prepare_payout(1,10,AT);self.context.user_data['state']=('manager_hash',p)
        with self.assertRaises(ValueError):await self.panel.input(self.update(body='bad hash'),self.context)
        self.assertEqual(self.s.balance(1),2500)
        self.assertEqual(self.context.user_data['state'],('manager_hash',p))

    async def test_cancel_preparation_returns_author_to_queue(self):
        p=self.s.prepare_payout(1,10,AT)
        await self.panel.handle_callback(self.update(f'mgr:cancelpay:{p}'),self.context)
        self.assertEqual(self.s.balance(1),2500)
        self.assertEqual([u['id'] for u,_ in self.panel.queue()],[1])

    async def test_nonstaff_cannot_use_callback(self):
        with self.assertRaises(ValueError):await self.panel.handle_callback(self.update('mgr:prepare:1',actor=1),self.context)

    async def test_pending_video_prevents_preparation(self):
        self.s.run("INSERT INTO videos(id,user_id,status,published) VALUES('abcdefghijk',1,'pending',?)",(iso(AT-timedelta(days=15)),))
        self.s.run("INSERT INTO ledger(user_id,source,external_id,amount,created) VALUES(1,'views','abcdefghijk:1',500,?)",(iso(AT-timedelta(days=15)),))
        await self.panel.handle_callback(self.update('mgr:prepare:1'),self.context)
        self.assertIsNone(self.s.one('SELECT * FROM payouts'))

    async def test_below_minimum_and_current_month_not_in_queue(self):
        self.s.run('DELETE FROM ledger')
        self.s.run("INSERT INTO ledger(user_id,source,external_id,amount,created) VALUES(1,'cpa','new',5000,?)",(iso(AT),))
        self.assertEqual(self.panel.queue(),[])

    async def test_message_sent_by_button(self):
        await self.panel.handle_callback(self.update('mgr:reply:1'),self.context)
        await self.panel.input(self.update(body='Please check your banner.'),self.context)
        self.context.bot.send_message.assert_awaited_with(1,'Менеджер ChinChin Partners:\nPlease check your banner.')
        self.assertIsNone(self.context.user_data.get('state'))

    async def test_reject_button_accepts_reason_and_notifies_author(self):
        self.s.run("INSERT INTO videos(id,user_id,status,published,title) VALUES('abcdefghijk',1,'pending',?,'Video')",(iso(AT-timedelta(days=15)),))
        self.s.run("INSERT INTO ledger(user_id,source,external_id,amount,created) VALUES(1,'views','abcdefghijk:1',500,?)",(iso(AT-timedelta(days=15)),))
        await self.panel.handle_callback(self.update('mgr:reject:abcdefghijk'),self.context)
        await self.panel.input(self.update(body='Баннер отсутствует в середине ролика.'),self.context)
        self.assertEqual(self.s.one('SELECT status FROM videos')['status'],'rejected')
        self.assertEqual(self.s.balance(1),2500)
        self.assertIn('Баннер отсутствует',self.context.bot.send_message.call_args.args[1])

    async def test_approve_button_checks_link_before_approval(self):
        self.s.run("INSERT INTO videos(id,user_id,status,published,title) VALUES('abcdefghijk',1,'pending',?,'Video')",(iso(AT-timedelta(days=15)),))
        self.r.youtube.videos.return_value=[{'status':{'privacyStatus':'public'}}]
        await self.panel.handle_callback(self.update('mgr:approve:abcdefghijk'),self.context)
        self.r.check_video_link.assert_awaited()
        self.assertEqual(self.s.one('SELECT status FROM videos')['status'],'approved')

    async def test_navigation_cancels_hash_input(self):
        self.context.user_data['state']=('manager_hash',5)
        await self.panel.handle_callback(self.update('mgr:author:1'),self.context)
        self.assertNotIn('state',self.context.user_data)
