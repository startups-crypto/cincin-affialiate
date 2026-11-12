"""Configure the test campaign selected in config.toml (explicit manual command)."""
import json
import requests
from core import ROOT
from services import Tracknow, checked
from terms import CAMPAIGN_TITLE, CAMPAIGN_TERMS, CARD_REWARD
from settings import settings

def main():
    settings.validate()
    api = Tracknow()
    api.require_access()
    cid = settings.tracknow_campaign_id
    (ROOT / 'secrets').mkdir(exist_ok=True)
    url = settings.tracknow_base_url
    headers = {'Authorization': 'Bearer ' + api.token}
    result = checked(requests.put(url + f'/campaigns/{cid}', data={
        'title': CAMPAIGN_TITLE, 'description': CAMPAIGN_TERMS,
        'campaignTerms': CAMPAIGN_TERMS,
    }, headers=headers, timeout=30))
    print('Campaign title:', result.get('title'), flush=True)
    existing = api.get(f'{cid}/payout', size=100)
    items = existing.get('content', []) if isinstance(existing, dict) else existing
    cpa = next((r for r in items if r['title'] == 'TEST card issuance CPA'), None)
    if not cpa:
        raise RuntimeError('Expected CPA rule missing; review campaign before changing it')
    checked(requests.put(url + f'/{cid}/payout/' + str(cpa['identity']), json={
        'paymentType': 'CPA', 'affiliateCommission': 10, 'goalValue': 'card_issued',
        'description': '$10 за подтверждённое оформление карты. TEST.',
    }, headers=headers, timeout=30))
    name = 'TEST turnover 0.1%'
    match = next((r for r in items if r['title'] == name), None)
    payload = {'title': name, 'paymentType': 'CPS', 'goalValue': 'card_turnover',
               'payoutType': 'General', 'affiliateCommission': 0.1, 'merchantCommission': 0,
               'description': CARD_REWARD + ' TEST.'}
    endpoint = f'/{cid}/payout' + ('/' + str(match['identity']) if match else '')
    method = requests.put if match else requests.post
    result = checked(method(url + endpoint, json=payload, headers=headers, timeout=30))
    print('Turnover rule ID:', result.get('identity'), flush=True)
    verified = api.get(f'{cid}/payout', size=100)
    (ROOT / 'secrets/test-campaign-payouts.json').write_text(json.dumps(verified))
    (ROOT / 'secrets/test-campaign.json').write_text(json.dumps({'campaignId': cid, 'title': CAMPAIGN_TITLE}))
    rows = verified.get('content', []) if isinstance(verified, dict) else verified
    for rule in rows:
        print('Verified:', rule.get('identity'), rule.get('title'), rule.get('paymentType'), rule.get('revshareType'), rule.get('affiliateCommission'), flush=True)

if __name__ == '__main__':
    main()
