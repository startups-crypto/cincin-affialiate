import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from datetime import timedelta
from core import Store
from settings import Settings
from test_core import AT

class ConsentTests(unittest.TestCase):
    def test_acceptance_survives_reopen_and_profile_update(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bot.sqlite3'
            first=Store(path);first.user(1,'Author')
            self.assertTrue(first.accept_terms(1,AT))
            joined=first.user(1)['joined'];first.db.close()
            second=Store(path)
            user=second.user(1,'Updated name','new_nickname')
            self.assertEqual(user['accepted'],1)
            self.assertEqual(user['accepted_at'],joined)
            self.assertFalse(second.accept_terms(1,AT+timedelta(days=1)))
            self.assertEqual(second.user(1)['joined'],joined)
            self.assertEqual(len(second.rows("SELECT * FROM audit WHERE action='accept_rules'")),1)
            second.db.close()

    def test_existing_acceptance_is_kept_during_migration(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'bot.sqlite3'
            s=Store(path);s.user(1,'Author')
            s.run('UPDATE users SET accepted=1 WHERE id=1');s.db.close()
            reopened=Store(path)
            self.assertEqual(reopened.user(1)['accepted'],1)
            self.assertTrue(reopened.user(1)['accepted_at'])
            reopened.db.close()

    def test_railway_volume_used_without_changing_config(self):
        with tempfile.TemporaryDirectory() as folder:
            config=Settings()
            with patch.dict(os.environ,{'RAILWAY_VOLUME_MOUNT_PATH':folder}),patch('core.settings',config):
                first=Store();first.user(1,'Author');first.accept_terms(1,AT);first.db.close()
                self.assertTrue((Path(folder)/'bot.sqlite3').is_file())
                second=Store();self.assertEqual(second.user(1)['accepted'],1);second.db.close()

if __name__=='__main__':unittest.main()
