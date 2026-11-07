import math
import random
import threading
import time
import requests
from .models import CleanerError, Cancelled


class ApiClient:
    version = '5.199'

    def __init__(self, token, cancel=None, rate=3, session=None):
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError('Частота должна быть положительной.')
        self._token = token
        self.cancel = cancel or threading.Event()
        self.rate = rate
        self.session = session or requests.Session()
        self.last = 0

    def wait(self, seconds):
        if self.cancel.wait(max(0, seconds)):
            raise Cancelled()

    def call(self, method, params):
        if method not in {'users.get', 'messages.getConversations', 'messages.getHistory', 'messages.delete'}:
            raise CleanerError('Метод не поддерживается.')
        writing = method == 'messages.delete'
        if {'v', 'access_token'} & params.keys():
            raise CleanerError('Недопустимые параметры.')
        for attempt in range(4):
            self.wait(1 / self.rate - (time.monotonic() - self.last))
            self.last = time.monotonic()
            try:
                response = self.session.post('https://api.vk.com/method/' + method,
                    data={**params, 'v': self.version, 'access_token': self._token},
                    timeout=(10, 30), allow_redirects=False)
                if response.status_code != 200:
                    raise CleanerError('Ошибка HTTP.', unknown=writing)
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError()
            except (requests.RequestException, ValueError):
                if writing:
                    raise CleanerError('Результат отправленного запроса неизвестен.', unknown=True) from None
                if attempt == 3:
                    raise CleanerError('Не удалось прочитать ответ VK.') from None
                self.wait(2 ** attempt + random.random())
                continue
            except CleanerError:
                if writing or attempt == 3:
                    raise
                self.wait(2 ** attempt + random.random())
                continue
            if 'error' in payload:
                error = payload['error']
                code = error.get('error_code') if isinstance(error, dict) else None
                if type(code) is not int:
                    raise CleanerError('Неизвестный формат ошибки.', unknown=writing)
                if code in (6, 9, 10) and attempt < 3 and (not writing or code in (6, 9)):
                    self.wait((5 if code == 9 else 1) * 2 ** attempt + random.random())
                    continue
                hints = {5: 'Нужно войти заново.', 7: 'Недостаточно прав.',
                         15: 'Доступ запрещён.', 29: 'Лимит VK: продолжите позже.',
                         14: 'VK требует CAPTCHA.', 17: 'VK требует подтверждение.'}
                raise CleanerError(hints.get(code, f'VK отклонил запрос: код {code}.'), code,
                                   unknown=writing and code == 10)
            if 'response' not in payload:
                raise CleanerError('Неизвестный формат ответа.', unknown=writing)
            return payload['response']
