import unittest
from core import contains_partner_link
import test_core as fixtures
AT = fixtures.AT

class LinkMatchTests(unittest.TestCase):
    expected = 'https://tracking.example/click?affiliate=12&campaign=337'

    def test_full_url_in_description_and_punctuation(self):
        self.assertTrue(contains_partner_link('Оформить карту: '+self.expected+'\nСпасибо',self.expected))
        self.assertTrue(contains_partner_link('('+self.expected+').',self.expected))

    def test_query_order_equivalent(self):
        self.assertTrue(contains_partner_link('https://tracking.example/click?campaign=337&affiliate=12',self.expected))

    def test_other_affiliate_and_domain_rejected(self):
        for url in [self.expected+'9',self.expected.replace('=12','=123'),self.expected.replace('tracking.example','tracking.example.evil.test'),self.expected.replace('tracking.example','tracking.example@evil.test'),'https://evil.test/?next='+self.expected]:
            self.assertFalse(contains_partner_link(url,self.expected),url)

    def test_invalid_url_does_not_hide_later_valid_url(self):
        self.assertTrue(contains_partner_link('https://bad.test:invalid\n'+self.expected,self.expected))

    def test_missing_expected_link_never_matches(self):
        self.assertFalse(contains_partner_link(self.expected,''))
        self.assertFalse(contains_partner_link('',self.expected))

class LinkAccountingTests(unittest.TestCase):
    def setUp(self):
        fixture=fixtures.AccountingTests();fixture.setUp();fixture.video()
        self.s=fixture.s
        self.vid='abcdefghijk'

    def test_missing_link_cancels_prepared_payment_and_requires_review(self):
        pid=self.s.prepare_payout(1,99,AT)
        self.assertTrue(self.s.record_link_check(self.vid,'https://example.test/author',False,AT))
        self.assertEqual(self.s.one('SELECT status FROM payouts WHERE id=?',(pid,))['status'],'cancelled')
        self.assertEqual(self.s.one('SELECT status FROM videos')['status'],'pending')
        self.assertEqual(self.s.balance(1),2000)
        with self.assertRaises(ValueError):self.s.prepare_payout(1,99,AT)

    def test_no_repeat_notification_and_no_auto_approval_on_restore(self):
        self.s.record_link_check(self.vid,'https://example.test/author',False,AT)
        self.assertFalse(self.s.record_link_check(self.vid,'https://example.test/author',False,AT))
        self.assertTrue(self.s.record_link_check(self.vid,'https://example.test/author',True,AT))
        self.assertEqual(self.s.one('SELECT status FROM videos')['status'],'pending')
        self.assertIn('восстановлена',self.s.one('SELECT reason FROM videos')['reason'])

if __name__=='__main__':unittest.main()
