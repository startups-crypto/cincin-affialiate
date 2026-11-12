from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from core import Store
from settings import Settings, load_settings

class SettingsTests(unittest.TestCase):
    def test_template_parses_without_any_private_files(self):
        result=load_settings(Path(__file__).parent/'config.example.toml')
        self.assertEqual(result.owner_ids,())
        self.assertFalse(result.tracknow_enabled)
        self.assertEqual(result.views_interval,900)

    def test_one_file_loads_all_integration_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'config.toml'
            p.write_text('''[telegram]
token="sample-telegram"
owner_ids=[123,456]
[youtube]
api_key="sample-youtube"
[google_sheets]
spreadsheet_id="sample-sheet"
[google_sheets.service_account]
project_id="sample-project"
client_email="bot@example.invalid"
private_key="sample-key"
[tracknow]
enabled=true
api_key="sample-partner"
campaign_id=10
campaign_title="test"
''')
            s=load_settings(p)
            self.assertEqual(s.owner_ids,(123,456))
            self.assertEqual(s.telegram_token,'sample-telegram')
            self.assertEqual(s.youtube_api_key,'sample-youtube')
            self.assertEqual(s.google_service_account['private_key'],'sample-key')
            self.assertEqual(s.google_spreadsheet_id,'sample-sheet')
            self.assertEqual(s.tracknow_campaign_id,10)
            self.assertNotIn('sample-partner',repr(s))
            self.assertNotIn('sample-key',repr(s))
            self.assertNotIn('sample-telegram',repr(s))

    def test_changing_owner_ids_revokes_previous_owner(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'test.sqlite3'
            with patch('core.settings',Settings(owner_ids=(10,))):
                first=Store(path);first.user(10,'Old owner');first.user(11,'New owner');first.db.close()
            with patch('core.settings',Settings(owner_ids=(11,))):
                second=Store(path)
                self.assertEqual(second.user(10)['role'],'author')
                self.assertEqual(second.user(11)['role'],'owner')
                second.db.close()

if __name__=='__main__':unittest.main()
