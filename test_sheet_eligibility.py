import threading
import unittest
from unittest.mock import Mock
from core import Store, linked_authors, has_partner_link
from services import Sheets, RemoteError

class SheetEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:')
        self.s.user(1,'Author')
        self.s.run('UPDATE users SET accepted=1,affiliate_id=100 WHERE id=1')
        self.s.user(2,'Owner')
        self.s.run("UPDATE users SET role='owner' WHERE id=2")

    def test_email_registration_and_owner_role_do_not_create_sections(self):
        self.assertEqual(linked_authors(self.s),[])
        self.assertFalse(has_partner_link(self.s,1))
        self.assertFalse(has_partner_link(self.s,2))

    def test_successful_link_qualifies_and_second_link_does_not_duplicate_user(self):
        self.s.save_partner_link(1,338,'https://tracking.example/exchange')
        self.s.save_partner_link(1,339,'https://tracking.example/cards')
        self.assertTrue(has_partner_link(self.s,1))
        self.assertEqual([u['id'] for u in linked_authors(self.s)],[1])

    def test_legacy_link_supported_but_empty_link_is_not(self):
        self.s.run("UPDATE users SET tracking_link='   ' WHERE id=1")
        self.assertFalse(has_partner_link(self.s,1))
        self.s.run("UPDATE users SET tracking_link='https://tracking.example/old' WHERE id=1")
        self.assertTrue(has_partner_link(self.s,1))

    def test_author_button_cannot_bypass_requirement(self):
        sheets=Sheets.__new__(Sheets);sheets.session=Mock()
        with self.assertRaises(RemoteError):sheets.author_url(1,self.s)
        sheets.session.get.assert_not_called()

    def test_report_creates_only_linked_author_tab_and_overview_row(self):
        self.s.save_partner_link(1,338,'https://tracking.example/exchange')
        response=Mock(ok=True)
        response.json.return_value={'sheets':[{'properties':{'title':t}} for t in ('Обзор','Выплаты','Условия')]}
        sheets=Sheets.__new__(Sheets)
        sheets.url='https://sheets.example/test';sheets.lock=threading.RLock()
        sheets.session=Mock()
        sheets.session.get.return_value=response;sheets.session.post.return_value=response
        sheets.sync(self.s)
        calls=sheets.session.post.call_args_list
        creation=next(c.kwargs['json'] for c in calls if c.args[0].endswith(':batchUpdate') and 'requests' in c.kwargs['json'])
        self.assertEqual([r['addSheet']['properties']['title'] for r in creation['requests']],['Автор 1'])
        payload=next(c.kwargs['json'] for c in calls if '/values:batchUpdate' in c.args[0])
        overview=next(d for d in payload['data'] if d['range']=="'Обзор'!A1")['values']
        self.assertEqual([row[0] for row in overview[1:]],['1'])

if __name__=='__main__':unittest.main()
