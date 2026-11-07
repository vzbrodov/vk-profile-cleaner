from datetime import date
from pathlib import Path
import tempfile
import unittest
from vk_cleaner.core.models import DateRange, CleanerError
from vk_cleaner.core.demo import DemoApi
from vk_cleaner.core.services import scan, delete, parse_delete, export_csv
from vk_cleaner.storage.database import Database
from vk_cleaner.core.models import Dialog


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Database(Path(self.temp.name) / 'test.sqlite3')
        self.api = DemoApi()
        self.sid = scan(self.api, self.store, 1, [Dialog(101, 'Тест')], DateRange(date(2000, 1, 1), date(2099, 1, 1)))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_author_and_resume(self):
        self.assertEqual(self.store.db.execute('select count(*) from candidates').fetchone()[0], 6)
        job, digest = self.store.freeze(self.sid)
        self.assertEqual(delete(self.api, self.store, job, 1, digest), {'confirmed': 6})
        self.assertEqual(delete(self.api, self.store, job, 1, digest), {'confirmed': 6})

    def test_exclusion_and_account(self):
        with self.store.db:
            self.store.db.execute('update candidates set selected=0 where id=1')
        job, digest = self.store.freeze(self.sid)
        with self.assertRaises(CleanerError):
            delete(self.api, self.store, job, 2, digest)
        self.assertEqual(delete(self.api, self.store, job, 1, digest), {'confirmed': 5})

    def test_unknown_blocks_resume(self):
        job, digest = self.store.freeze(self.sid)
        original = self.api.call
        def failure(method, params):
            if method == 'users.get':
                return original(method, params)
            raise CleanerError('Таймаут', unknown=True)
        self.api.call = failure
        with self.assertRaises(CleanerError):
            delete(self.api, self.store, job, 1, digest)
        self.assertEqual(self.store.summary(job), {'unknown': 6})
        with self.assertRaises(CleanerError):
            delete(self.api, self.store, job, 1, digest)

    def test_crash_recovery(self):
        job, digest = self.store.freeze(self.sid)
        with self.store.db:
            self.store.db.execute("update items set state='in_flight'")
        with self.assertRaises(CleanerError):
            delete(self.api, self.store, job, 1, digest)
        self.assertEqual(self.store.summary(job), {'unknown': 6})

    def test_partial(self):
        rows = [{'id': 1, 'peer': 9, 'cmid': 1}, {'id': 2, 'peer': 9, 'cmid': 2}]
        result = parse_delete([{'peer_id': 9, 'conversation_message_id': 1, 'response': 1}], rows)
        self.assertEqual(result, {1: 'confirmed', 2: 'unknown'})

    def test_dates_dst(self):
        bounds = DateRange(date(2024, 3, 31), date(2024, 3, 31), 'Europe/Berlin').bounds()
        self.assertEqual(bounds[1] - bounds[0], 23 * 3600)
        self.assertFalse(DateRange(date(2024, 3, 31), date(2024, 3, 31), 'Europe/Berlin').contains(bounds[1]))

    def test_csv_formula(self):
        with self.store.db:
            self.store.db.execute("update candidates set snippet='=1+1'")
        path = Path(self.temp.name) / 'out.csv'
        export_csv(self.store, self.sid, path)
        self.assertIn("'=1+1", path.read_text(encoding='utf-8-sig'))

    def test_lock(self):
        other = Database(self.store.path)
        try:
            with self.store.lock(), self.assertRaises(CleanerError):
                with other.lock():
                    pass
        finally:
            other.close()

    def test_snapshot_tampering_blocks_delete(self):
        job, digest = self.store.freeze(self.sid)
        with self.store.db:
            self.store.db.execute('update candidates set cmid=999 where id=1')
        with self.assertRaises(CleanerError):
            delete(self.api, self.store, job, 1, digest)
        self.assertEqual(self.store.summary(job), {'pending': 6})

    def test_real_token_identity_checked(self):
        job, digest = self.store.freeze(self.sid)
        original = self.api.call
        def other_account(method, params):
            return [{'id': 2}] if method == 'users.get' else original(method, params)
        self.api.call = other_account
        with self.assertRaises(CleanerError):
            delete(self.api, self.store, job, 1, digest)
        self.assertEqual(self.store.summary(job), {'pending': 6})
