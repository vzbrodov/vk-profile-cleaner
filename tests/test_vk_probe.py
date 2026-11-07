import io
import json
import unittest
from tools.vk_probe import call_api, run_probe, ProbeError, NoRedirect


class FakeOpener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return io.BytesIO(json.dumps(self.payload).encode())


class ProbeTests(unittest.TestCase):
    def test_post_and_no_token_in_url(self):
        opener = FakeOpener({'response': []})
        self.assertEqual(call_api('SECRET', 'users.get', {}, opener=opener), [])
        self.assertEqual(opener.request.get_method(), 'POST')
        self.assertNotIn('SECRET', opener.request.full_url)
        self.assertIn(b'access_token=SECRET', opener.request.data)

    def test_mutations_blocked(self):
        for method in ('messages.delete', 'execute', 'messages.markAsRead'):
            with self.assertRaises(ProbeError):
                call_api('SECRET', method, {})

    def test_error_does_not_echo_server_secrets(self):
        for code in (5, 6, 9, 15):
            with self.assertRaises(ProbeError) as ctx:
                call_api('SECRET', 'users.get', {}, opener=FakeOpener(
                    {'error': {'error_code': code, 'error_msg': 'SECRET', 'request_params': 'SECRET'}}))
            self.assertNotIn('SECRET', str(ctx.exception))
            self.assertIn(str(code), str(ctx.exception))

    def test_bad_payload(self):
        for payload in ([], {}, {'error': 'SECRET'}):
            with self.assertRaises(ProbeError):
                call_api('SECRET', 'users.get', {}, opener=FakeOpener(payload))

    def test_no_redirect(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))

    def test_read_flow_and_own_count(self):
        calls = []
        responses = [[{'id': 12}], {'items': []}, {'items': [
            {'from_id': 12, 'date': 100, 'conversation_message_id': 1},
            {'from_id': 13, 'date': 101}]}]
        def call(method, params):
            calls.append(method)
            return responses.pop(0)
        result = run_probe(call, 99, sleep=lambda _: None)
        self.assertEqual(calls, ['users.get', 'messages.getConversations', 'messages.getHistory'])
        self.assertEqual(result['sample_own_messages'], 1)
        self.assertEqual(result['sample_with_cmids'], 1)
        self.assertNotIn('12', json.dumps(result))

    def test_empty_account_does_not_select_peer(self):
        responses = iter([[{'id': 12}], {'items': []}])
        result = run_probe(lambda *args: next(responses), sleep=lambda _: None)
        self.assertEqual(result['history'], 'not_checked')

    def test_invalid_identity_stops(self):
        with self.assertRaises(ProbeError):
            run_probe(lambda *args: [{'id': 'SECRET'}], sleep=lambda _: None)


if __name__ == '__main__':
    unittest.main()
