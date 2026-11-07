from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from vk_profile_cleaner.storage.database import Database
from vk_profile_cleaner.storage.legacy import migrate_database, migrate_token
from vk_profile_cleaner.identity import APP_ID, LEGACY_APP_ID, TOKEN_ACCOUNT
from vk_profile_cleaner.core.models import CleanerError, DateRange, Dialog
from vk_profile_cleaner.core.demo import DemoApi
from vk_profile_cleaner.core.services import scan, delete


class FakeKeyring:
    def __init__(self):
        self.values = {(LEGACY_APP_ID, TOKEN_ACCOUNT): 'test-secret'}
        self.fail_write = False

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, token):
        if self.fail_write:
            raise RuntimeError('test-secret')
        self.values[service, account] = token

    def delete_password(self, service, account):
        del self.values[service, account]


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = Path(self.temp.name) / 'old' / 'cleaner.sqlite3'
        self.new = Path(self.temp.name) / 'new' / 'cleaner.sqlite3'
        self.store = Database(self.old)
        self.api = DemoApi()
        sid = scan(self.api, self.store, 1, [Dialog(101, 'Тест')], DateRange(date(2020, 1, 1), date(2030, 1, 1)))
        self.job, self.digest = self.store.freeze(sid)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_jobs_survive_and_resume(self):
        migrate_database(self.old, self.new)
        copied = Database(self.new)
        try:
            copied.verify(self.job)
            self.assertEqual(delete(self.api, copied, self.job, 1, self.digest), {'confirmed': 6})
            self.assertEqual(self.store.summary(self.job), {'pending': 6})
            self.assertEqual(self.new.stat().st_mode & 0o777, 0o600)
        finally:
            copied.close()

    def test_destination_not_overwritten(self):
        migrate_database(self.old, self.new)
        content = self.new.read_bytes()
        with self.assertRaises(CleanerError):
            migrate_database(self.old, self.new)
        self.assertEqual(content, self.new.read_bytes())

    def test_busy_source_rejected(self):
        with self.store.lock(), self.assertRaises(CleanerError):
            migrate_database(self.old, self.new)
        self.assertFalse(self.new.exists())

    def test_newer_schema_rejected(self):
        self.store.db.execute('pragma user_version=99')
        with self.assertRaises(CleanerError):
            migrate_database(self.old, self.new)
        self.assertFalse(self.new.exists())

    def test_missing_source(self):
        with self.assertRaises(CleanerError):
            migrate_database(self.old.parent / 'missing', self.new)

    def test_token_transfer_verified(self):
        ring = FakeKeyring()
        migrate_token(ring, remove_old=True)
        self.assertEqual(ring.get_password(APP_ID, TOKEN_ACCOUNT), 'test-secret')
        self.assertIsNone(ring.get_password(LEGACY_APP_ID, TOKEN_ACCOUNT))

    def test_token_write_failure_preserves_old(self):
        ring = FakeKeyring()
        ring.fail_write = True
        with self.assertRaises(CleanerError) as ctx:
            migrate_token(ring, remove_old=True)
        self.assertNotIn('test-secret', str(ctx.exception))
        self.assertEqual(ring.get_password(LEGACY_APP_ID, TOKEN_ACCOUNT), 'test-secret')

    def test_existing_token_not_overwritten(self):
        ring = FakeKeyring()
        ring.values[APP_ID, TOKEN_ACCOUNT] = 'another-secret'
        with self.assertRaises(CleanerError):
            migrate_token(ring, remove_old=True)
        self.assertEqual(ring.get_password(APP_ID, TOKEN_ACCOUNT), 'another-secret')

    def test_new_default_not_created_over_legacy(self):
        with patch('vk_profile_cleaner.storage.database.database_path', return_value=self.new), \
             patch('vk_profile_cleaner.storage.database.legacy_database_path', return_value=self.old):
            with self.assertRaises(CleanerError):
                Database(self.new)
        self.assertFalse(self.new.exists())

    def test_filesystem_error_is_safe(self):
        with patch('vk_profile_cleaner.storage.legacy.migration_locks', side_effect=PermissionError('sensitive-path')):
            with self.assertRaises(CleanerError) as ctx:
                migrate_database(self.old, self.new)
        self.assertNotIn('sensitive-path', str(ctx.exception))
        self.assertFalse(self.new.exists())

    def test_token_verification_failure_keeps_legacy(self):
        ring = FakeKeyring()
        ring.set_password = lambda *args: None
        with self.assertRaises(CleanerError):
            migrate_token(ring, remove_old=True)
        self.assertEqual(ring.get_password(LEGACY_APP_ID, TOKEN_ACCOUNT), 'test-secret')

    def test_token_delete_failure_keeps_new_verified_copy(self):
        ring = FakeKeyring()
        def fail(*args):
            raise RuntimeError('test-secret')
        ring.delete_password = fail
        with self.assertRaises(CleanerError):
            migrate_token(ring, remove_old=True)
        self.assertEqual(ring.get_password(APP_ID, TOKEN_ACCOUNT), 'test-secret')
        self.assertEqual(ring.get_password(LEGACY_APP_ID, TOKEN_ACCOUNT), 'test-secret')
