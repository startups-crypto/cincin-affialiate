import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from core import Store
from terms_flow import TermsFlow
from terms import RULE_PAGES, RULES

class TermsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store=Store(':memory:');self.store.user(1)
        self.flow=TermsFlow(self.store)
        self.context=SimpleNamespace(user_data={})
        self.message=SimpleNamespace(reply_text=AsyncMock())
    def tearDown(self): self.store.db.close()
    async def test_five_pages_accept_only_last(self):
        self.assertEqual(len(RULE_PAGES),5)
        await self.flow.begin(self.message,self.context,1)
        for i in range(5):
            if i:
                q=SimpleNamespace(data=f'terms:{i}',message=self.message,edit_message_reply_markup=AsyncMock())
                await self.flow.next(SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=1)),self.context)
            button=self.message.reply_text.call_args.kwargs['reply_markup'].inline_keyboard[0][0]
            self.assertEqual(button.callback_data,'accept' if i==4 else f'terms:{i+1}')
        self.flow.require_complete(self.context,1)
    async def test_cannot_skip(self):
        with self.assertRaises(ValueError): self.flow.require_complete(self.context,1)
        q=SimpleNamespace(data='terms:4',message=self.message,edit_message_reply_markup=AsyncMock())
        await self.flow.next(SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=1)),self.context)
        self.assertNotIn('terms_page',self.context.user_data)
    async def test_existing_consent_needs_no_new_acceptance(self):
        self.store.accept_terms(1)
        await self.flow.send(self.message,self.context,1,4)
        self.assertIsNone(self.message.reply_text.call_args.kwargs['reply_markup'])
        self.flow.require_complete(SimpleNamespace(user_data={}),1)
    def test_sheet_text_has_no_html(self):
        self.assertNotIn('<b>',RULES)
