import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from settings import Settings
from core import Store, contains_partner_link
from partner_program import PartnerProgram
from services import Tracknow

class DirectionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store=Store(':memory:')
        self.store.user(1,'Creator')
        self.api=SimpleNamespace(register=Mock(return_value={'data':{'id':100}}),
                                 assign=Mock(),link=Mock(side_effect=lambda aid,cid:{'link':f'https://tracking.example/click?ref_id={aid}&campaign_id={cid}'}))
        self.program=PartnerProgram(self.store,self.api,Settings(tracknow_enabled=True))

    async def test_both_directions_reuse_one_account_and_keep_both_links(self):
        a,first=await self.program.issue(1,'exchange','creator@example.invalid')
        b,second=await self.program.issue(1,'cards')
        self.assertEqual((a['campaign_id'],b['campaign_id']),(338,339))
        self.assertEqual(self.api.register.call_count,1)
        self.assertEqual(self.api.assign.call_args_list[0].args,(100,338))
        self.assertEqual(self.api.assign.call_args_list[1].args,(100,339))
        self.assertEqual({r['link'] for r in self.store.partner_links(1)},{first,second})
        self.assertTrue(any(contains_partner_link('Card: '+first,r['link']) for r in self.store.partner_links(1)))

    async def test_repeat_choice_does_not_register_again(self):
        await self.program.issue(1,'exchange','creator@example.invalid')
        await self.program.issue(1,'exchange')
        self.assertEqual(self.api.register.call_count,1)
        self.assertEqual(len(self.store.partner_links(1)),1)

    async def test_link_failure_preserves_created_account_for_retry(self):
        self.api.link.side_effect=None
        self.api.link.return_value={'link':''}
        with self.assertRaises(ValueError):await self.program.issue(1,'cards','creator@example.invalid')
        self.assertEqual(self.store.user(1)['affiliate_id'],100)
        self.api.link.return_value={'link':'https://tracking.example/cards'}
        await self.program.issue(1,'cards')
        self.assertEqual(self.api.register.call_count,1)

    async def test_old_link_is_preserved_when_new_direction_is_selected(self):
        old='https://tracking.example/click?ref_id=100&campaign_id=337'
        self.store.run('UPDATE users SET affiliate_id=100,tracking_link=? WHERE id=1',(old,))
        await self.program.issue(1,'cards')
        self.assertIn(old,{r['link'] for r in self.store.partner_links(1)})
        self.assertEqual(self.api.register.call_count,0)

    async def test_invalid_choice_and_blocked_user_cannot_register(self):
        with self.assertRaises(ValueError):await self.program.issue(1,'unknown','creator@example.invalid')
        self.store.run('UPDATE users SET blocked=1 WHERE id=1')
        with self.assertRaises(ValueError):await self.program.issue(1,'cards','creator@example.invalid')
        self.api.register.assert_not_called()

class CampaignIncomeTests(unittest.TestCase):
    def test_campaigns_with_same_event_uuid_do_not_overwrite_each_other(self):
        s=Store(':memory:');s.user(1,'Creator')
        s.run('UPDATE users SET affiliate_id=100 WHERE id=1')
        def event(cid,status=0):
            return {'campaignId':cid,'ogId':'same-id','affiliateId':100,'campaignPayoutId':1,
                    'status':status,'convertedCurrency':'USD','affiliateCommission':10,'amount':0}
        s.apply_partner_events([event(338)],338,{1:'cpa'})
        s.apply_partner_events([event(339)],339,{1:'cpa'})
        self.assertEqual(s.balance(1),2000)
        s.apply_partner_events([event(338)],338,{1:'cpa'})
        self.assertEqual(s.balance(1),2000)
        s.apply_partner_events([event(338,3)],338,{1:'cpa'})
        self.assertEqual(s.balance(1),1000)

    def test_live_campaign_rule_names_and_zero_blocking_are_supported(self):
        api=Tracknow()
        rules=[
            {'identity':1,'goalValue':'card_open','paymentType':'CPA','affiliateCommission':10},
            *[{'identity':i+2,'goalValue':goal,'paymentType':'CPS','affiliateCommission':0.1} for i,goal in enumerate(['card_top_up','card_withdrawal','chargeback','exchange'])],
            {'identity':9,'goalValue':'card_blocking','paymentType':'CPA','affiliateCommission':0},
        ]
        api.get=Mock(return_value={'content':rules});api.conversions=Mock(return_value=[])
        events,mapping=api.fetch_income(338)
        self.assertEqual(mapping[1],'cpa')
        self.assertTrue(all(mapping[i]=='turnover' for i in range(2,6)))
        self.assertEqual(mapping[9],'ignore')
        s=Store(':memory:');s.user(1,'Creator');s.run('UPDATE users SET affiliate_id=100 WHERE id=1')
        event={'campaignId':338,'ogId':'blocking','affiliateId':100,'campaignPayoutId':9,'affiliateCommission':0}
        s.apply_partner_events([event],338,mapping)
        self.assertEqual(s.balance(1),0)
        event['affiliateCommission']=1
        with self.assertRaises(ValueError):s.apply_partner_events([event],338,mapping)

if __name__=='__main__':unittest.main()
