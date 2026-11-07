import threading
import unittest
from unittest.mock import Mock, patch
import requests
from vk_profile_cleaner.core.api import ApiClient
from vk_profile_cleaner.core.models import CleanerError, Cancelled


class ApiTests(unittest.TestCase):
    def api(self, payload=None, error=None):
        session = Mock()
        session.post.return_value.status_code = 200
        session.post.return_value.json.return_value = payload
        if error:
            session.post.side_effect = error
        api = ApiClient('SECRET', session=session)
        api.wait = Mock()
        return api, session

    def test_write_timeout_no_retry(self):
        api, session = self.api(error=requests.Timeout('SECRET'))
        with self.assertRaises(CleanerError) as ctx:
            api.call('messages.delete', {})
        self.assertTrue(ctx.exception.unknown)
        self.assertNotIn('SECRET', str(ctx.exception))
        self.assertEqual(session.post.call_count, 1)

    def test_auth_no_retry(self):
        api, session = self.api({'error': {'error_code': 5, 'error_msg': 'SECRET'}})
        with self.assertRaises(CleanerError):
            api.call('users.get', {})
        self.assertEqual(session.post.call_count, 1)

    def test_transient_bounded(self):
        api, session = self.api({'error': {'error_code': 6}})
        with self.assertRaises(CleanerError):
            api.call('users.get', {})
        self.assertEqual(session.post.call_count, 4)

    def test_cancel_before_send(self):
        event = threading.Event()
        event.set()
        session = Mock()
        with self.assertRaises(Cancelled):
            ApiClient('SECRET', cancel=event, session=session).call('users.get', {})
        session.post.assert_not_called()
