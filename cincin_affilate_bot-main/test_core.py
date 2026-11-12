import unittest
from datetime import datetime, timedelta, timezone
from core import Store, reward, cutoff, iso

AT = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)

class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
        self.s.user(1, 'Author')
        self.s.run("UPDATE users SET wallet='test-wallet' WHERE id=1")

    def video(self, vid='abcdefghijk', views=400000):
        self.s.run("INSERT INTO videos(id,user_id,title,published,deadline,status) VALUES(?,1,'Test',?,?,'approved')", (vid, iso(AT-timedelta(days=20)), iso(AT+timedelta(days=40))))
        self.s.update_views(vid, views, AT-timedelta(days=10))

    def test_blocks_and_final_rounding(self):
        self.assertEqual(reward(50000),0)
        self.assertEqual(reward(290000),1000)
        self.assertEqual(reward(290000,True),1500)
        self.assertEqual(reward(90000,True),500)
        self.assertEqual(reward(89999,True),0)

    def test_repeat_refresh_not_double_counted(self):
        self.video()
        self.s.update_views('abcdefghijk',400000,AT)
        self.assertEqual(self.s.balance(1),2000)

    def test_payment_retains_new_earnings_and_cannot_repeat(self):
        self.video()
        pid=self.s.prepare_payout(1,99,AT)
        self.s.update_views('abcdefghijk',500000,AT)
        self.s.pay(pid,'a'*64,99)
        self.assertEqual(self.s.balance(1),500)
        with self.assertRaises(ValueError): self.s.pay(pid,'b'*64,99)

    def test_rejection_cancels_unpaid_only(self):
        self.video()
        pid=self.s.prepare_payout(1,99,AT)
        self.s.pay(pid,'a'*64,99)
        self.s.update_views('abcdefghijk',600000,AT)
        self.s.reject('abcdefghijk','Invalid banner',99)
        self.s.reject('abcdefghijk','Invalid banner again',99)
        self.assertEqual(self.s.balance(1),0)

    def test_rejected_entries_do_not_reduce_other_video_payment(self):
        self.video()
        self.s.reject('abcdefghijk','Invalid banner',99)
        self.video('lmnopqrstuv')
        pid=self.s.prepare_payout(1,99,AT)
        self.assertEqual(self.s.one('SELECT amount FROM payouts WHERE id=?',(pid,))['amount'],2000)

    def test_no_post_deadline_views(self):
        self.video(views=290000)
        deadline=datetime.fromisoformat(self.s.one('SELECT deadline FROM videos')['deadline'])
        self.s.update_views('abcdefghijk',10000000,deadline+timedelta(minutes=1))
        self.assertEqual(self.s.balance(1),1500)
        self.assertEqual(self.s.one('SELECT views FROM videos')['views'],290000)

    def test_month_boundary_moscow(self):
        self.assertEqual(cutoff(AT),datetime(2026,10,31,21,tzinfo=timezone.utc))

    def test_payments_outside_window_rejected(self):
        self.video()
        with self.assertRaises(ValueError): self.s.prepare_payout(1,99,AT.replace(day=11))

if __name__=='__main__': unittest.main()
