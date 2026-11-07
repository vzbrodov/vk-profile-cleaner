"""Ограниченная диагностика чтения VK API. Только стандартная библиотека."""
import argparse
import getpass
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

ALLOWED = frozenset({'users.get', 'messages.getConversations', 'messages.getHistory'})


class ProbeError(Exception):
    """Сообщение без содержимого ответа сервера и секретов."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call_api(token, method, params, version='5.199', opener=None):
    if method not in ALLOWED:
        raise ProbeError('Метод не разрешён диагностикой.')
    if set(params) & {'access_token', 'v'}:
        raise ProbeError('Служебные параметры нельзя переопределять.')
    body = urllib.parse.urlencode({**params, 'access_token': token, 'v': version}).encode()
    request = urllib.request.Request('https://api.vk.com/method/' + method, data=body)
    opener = opener or urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            payload = json.loads(response.read(4_000_001))
    except (OSError, ValueError, urllib.error.URLError):
        raise ProbeError('Сбой сети, HTTP или формата ответа; секретные детали скрыты.') from None
    if not isinstance(payload, dict):
        raise ProbeError('Неожиданный формат ответа VK.')
    if 'error' in payload:
        error = payload['error']
        code = error.get('error_code') if isinstance(error, dict) else None
        if type(code) is not int:
            raise ProbeError('Некорректная ошибка VK.')
        hint = {5: 'Ошибка авторизации', 6: 'Слишком частые запросы',
                7: 'Недостаточно прав', 9: 'Flood control', 14: 'Требуется CAPTCHA',
                15: 'Доступ запрещён', 29: 'Достигнут лимит'}.get(code, 'Ошибка API')
        raise ProbeError(f'{hint}; код {code}.')
    if 'response' not in payload:
        raise ProbeError('В ответе VK отсутствует response.')
    return payload['response']


def run_probe(call, peer_id=None, sleep=time.sleep):
    users = call('users.get', {})
    if not isinstance(users, list) or not users or not isinstance(users[0], dict):
        raise ProbeError('users.get: неверная структура.')
    user_id = users[0].get('id')
    if type(user_id) is not int or user_id <= 0:
        raise ProbeError('Не получен ID пользователя.')
    report = {'auth': 'ok', 'history': 'not_checked'}
    sleep(0.4)
    dialogs = call('messages.getConversations', {'count': 1, 'offset': 0})
    if not isinstance(dialogs, dict) or not isinstance(dialogs.get('items'), list):
        raise ProbeError('getConversations: неверная структура.')
    report['dialogs'] = 'ok'
    report['sample_dialogs'] = len(dialogs['items'])
    if peer_id is not None:
        sleep(0.4)
        history = call('messages.getHistory', {'peer_id': peer_id, 'count': 20, 'offset': 0, 'rev': 0})
        if not isinstance(history, dict) or not isinstance(history.get('items'), list):
            raise ProbeError('getHistory: неверная структура.')
        items = history['items']
        if any(not isinstance(m, dict) or type(m.get('date')) is not int
               or type(m.get('from_id')) is not int for m in items):
            raise ProbeError('getHistory: неверная структура сообщения.')
        report.update(history='ok', sample_messages=len(items),
                      sample_own_messages=sum(m['from_id'] == user_id for m in items),
                      sample_with_cmids=sum(type(m.get('conversation_message_id')) is int for m in items))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--peer-id', type=int, help='Проверить до 20 сообщений указанного диалога')
    parser.add_argument('--api-version', default='5.199')
    args = parser.parse_args()
    if args.peer_id == 0:
        parser.error('peer-id не может быть нулём')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            token = getpass.getpass('Токен VK (скрытый ввод): ').strip()
        if not token:
            raise ProbeError('Токен не введён.')
        result = run_probe(lambda method, params: call_api(token, method, params, args.api_version), args.peer_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ProbeError, getpass.GetPassWarning) as exc:
        print(str(exc) if isinstance(exc, ProbeError) else 'Нужен терминал со скрытым вводом.')
        return 1
    except (EOFError, KeyboardInterrupt):
        print('Проверка отменена.')
        return 130
    finally:
        print('Проверка не удаляет сообщения. После работы отзовите ненужный токен.')


if __name__ == '__main__':
    raise SystemExit(main())
