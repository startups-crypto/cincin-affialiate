import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from core import Store
from account_flow import AccountFlow

class AccountFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store=Store(':memory:');self.store.user(1);self.store.user(2)
        self.flow=AccountFlow(self.store)
        self.message=SimpleNamespace(reply_text=AsyncMock())
        self.context=SimpleNamespace(user_data={})

    def tearDown(self): self.store.db.close()

    def channel(self, cid, uid=1):
        self.store.run('INSERT INTO channels VALUES(?,?,?)',(cid,uid,cid))

    async def test_no_accounts_does_not_offer_submission(self):
        await self.flow.choose_platform(self.message,1)
        self.assertNotIn('reply_markup',self.message.reply_text.call_args.kwargs)

    async def test_only_verified_platform_is_offered(self):
        self.channel('UC1')
        await self.flow.choose_platform(self.message,1)
        rows=self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual([b.callback_data for row in rows for b in row],['accounts:platform:youtube'])

    async def test_single_account_skips_picker(self):
        self.channel('UC1')
        await self.flow.choose_account(self.message,self.context,1)
        self.assertEqual(self.context.user_data['state'],('video','UC1'))

    async def test_multiple_accounts_offer_only_own(self):
        self.channel('UC1');self.channel('UC2');self.channel('OTHER',2)
        await self.flow.choose_account(self.message,self.context,1)
        rows=self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard
        self.assertEqual({b.callback_data for row in rows for b in row},{'accounts:video:UC1','accounts:video:UC2'})
        self.assertNotIn('state',self.context.user_data)

    async def test_other_users_account_rejected(self):
        self.channel('OTHER',2)
        with self.assertRaises(ValueError):
            await self.flow.select_account(self.message,self.context,1,'OTHER')
        self.assertNotIn('state',self.context.user_data)

    async def test_unavailable_provider_does_not_claim_connection(self):
        self.context.user_data['state']=('video','UC1')
        update=SimpleNamespace(effective_user=SimpleNamespace(id=1),callback_query=SimpleNamespace(data='accounts:connect:tiktok',message=self.message))
        await self.flow.callback(update,self.context)
        self.assertNotIn('state',self.context.user_data)
        self.assertEqual(self.flow.accounts(1),[])
