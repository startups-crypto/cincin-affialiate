import unittest
from datetime import timedelta
from core import Store, iso
from test_core import AT

RULES = {40476:'cpa',40477:'turnover'}

def event(eid='one', source='cpa', status=0, commission=10, affiliate=100, amount=0):
    return {'ogId':eid,'orderId':'order-'+eid,'campaignId':337,'affiliateId':affiliate,
            'campaignPayoutId':40476 if source=='cpa' else 40477,'status':status,
            'affiliateCommission':commission,'amount':amount,'convertedCurrency':'USD'}

class PartnerTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:')
        self.s.user(1,'Author')
        self.s.run("UPDATE users SET affiliate_id=100,wallet='test-wallet' WHERE id=1")

    def apply(self, events, at=None):
        return self.s.apply_partner_events(events,337,RULES,at or AT-timedelta(days=10))

    def test_repeated_import_and_paid_status_dont_duplicate(self):
        self.apply([event()]);self.apply([event()]);self.apply([event(status=7)])
        self.assertEqual(self.s.balance(1),1000)
        self.assertEqual(len(self.s.rows('SELECT * FROM ledger')),1)

    def test_pending_test_and_denied_excluded_from_balance(self):
        self.apply([event('pending',status=2),event('test',status=5),event('denied',status=3)])
        self.assertEqual(self.s.balance(1),0)
        info=self.s.income(1)['cpa']
        self.assertEqual(info['pending_micro'],10000000)
        self.assertEqual(info['test_micro'],10000000)

    def test_approval_and_reversal_and_reapproval(self):
        self.apply([event(status=2)]);self.assertEqual(self.s.balance(1),0)
        self.apply([event()]);self.assertEqual(self.s.balance(1),1000)
        self.apply([event(status=3)]);self.assertEqual(self.s.balance(1),0)
        self.apply([event()]);self.assertEqual(self.s.balance(1),1000)

    def test_fractional_cents_accumulate_before_rounding(self):
        records=[event(str(i),source='turnover',commission='0.0001',amount='0.1') for i in range(50)]
        self.apply(records)
        self.assertEqual(self.s.balance(1),1)
        self.apply(records)
        self.assertEqual(self.s.balance(1),1)

    def test_three_sources_sum_and_payments_match(self):
        self.s.run("INSERT INTO ledger(user_id,source,external_id,amount,created) VALUES(1,'views','v:1',500,?)",(iso(AT-timedelta(days=10)),))
        self.s.run("INSERT INTO videos(id,user_id,status) VALUES('v',1,'approved')")
        self.apply([event('c1'),event('c2'),event('t1',source='turnover',commission=5,amount=5000)])
        self.assertEqual(self.s.balance(1),3000)
        data=self.s.income(1)
        self.assertEqual([data[s]['balance'] for s in ['views','cpa','turnover']],[500,2000,500])
        pid=self.s.prepare_payout(1,99,AT);self.s.pay(pid,'f'*64,99)
        self.assertEqual(sum(r['balance'] for r in self.s.income(1).values()),0)
        self.assertEqual([self.s.income(1)[s]['paid'] for s in ['views','cpa','turnover']],[500,2000,500])

    def test_reversal_cancels_reserved_payout(self):
        self.apply([event('a'),event('b')]);pid=self.s.prepare_payout(1,99,AT)
        self.apply([event('a',status=3)],AT)
        self.assertEqual(self.s.one('SELECT status FROM payouts WHERE id=?',(pid,))['status'],'cancelled')
        self.assertEqual(self.s.balance(1),1000)
        with self.assertRaises(ValueError):self.s.pay(pid,'f'*64,99)

    def test_reversal_after_payment_creates_visible_debt(self):
        self.apply([event('a'),event('b')]);pid=self.s.prepare_payout(1,99,AT);self.s.pay(pid,'f'*64,99)
        self.apply([event('a',status=3)],AT)
        self.assertEqual(self.s.balance(1),-1000)
        self.assertEqual(self.s.income(1)['cpa']['balance'],-1000)

    def test_unmapped_affiliate_ignored(self):
        self.apply([event(affiliate=999)])
        self.assertEqual(self.s.balance(1),0)
        self.assertEqual(len(self.s.rows('SELECT * FROM partner_events')),0)

    def test_wrong_campaign_rejected_atomically(self):
        invalid=event('bad');invalid['campaignId']=64
        with self.assertRaises(ValueError):self.apply([event(),invalid])
        self.assertEqual(self.s.balance(1),0)

    def test_other_payout_and_nonusd_rejected(self):
        for field,value in [('campaignPayoutId',999),('convertedCurrency','EUR')]:
            invalid=event();invalid[field]=value
            with self.assertRaises(ValueError):self.apply([invalid])
        self.assertEqual(self.s.balance(1),0)

    def test_duplicate_affiliate_binding_rejected(self):
        import sqlite3
        self.s.user(2,'Second')
        with self.assertRaises(sqlite3.IntegrityError):self.s.run('UPDATE users SET affiliate_id=100 WHERE id=2')

if __name__=='__main__':unittest.main()
