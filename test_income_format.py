import unittest
from income_format import format_source,format_partner_report

class IncomeFormatTests(unittest.TestCase):
    def setUp(self):
        self.data=dict(earned=1234,paid=200,balance=1034,pending_micro=125,test_micro=0)
    def test_source_has_bullets_and_bold_header(self):
        text=format_source(self.data,'cpa')
        self.assertTrue(text.startswith('<b>💳'))
        self.assertIn('▫️ Остаток: $10.34',text)
        self.assertIn('▫️ Ожидает подтверждения: $0.000125',text)
        self.assertNotIn('Tracknow',text)
    def test_report_has_one_update_and_no_event_history(self):
        text=format_partner_report(dict(cpa=self.data,turnover=self.data),'Информация обновлена: 20.11.2026, 12:00 (GMT+3)')
        self.assertEqual(text.count('Информация обновлена:'),1)
        self.assertEqual(text.count('Доли цента'),1)
        self.assertNotIn('Последние события',text)
    def test_view_details_do_not_include_partner_only_fields(self):
        text=format_source(self.data,'views')
        self.assertNotIn('Тестовые',text)
        self.assertIn('до проверки контента менеджером',text)
