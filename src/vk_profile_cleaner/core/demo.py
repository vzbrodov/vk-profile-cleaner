import threading
from datetime import datetime, timezone


class DemoApi:
    version = 'demo'

    def __init__(self):
        self.cancel = threading.Event()

    def call(self, method, params):
        if method == 'users.get':
            return [{'id': 1, 'first_name': 'Демо', 'last_name': 'Пользователь'}]
        if method == 'messages.getConversations':
            if params.get('offset') or params.get('filter') == 'archive':
                return {'items': []}
            return {'items': [{'conversation': {'peer': {'id': i}, 'chat_settings': {'title': title}}} for i, title in [(101, 'Анна · планы на выходные'), (102, 'Рабочая беседа'), (103, 'Книжный клуб')]]}
        if method == 'messages.getHistory':
            return {'count': 8, 'items': [] if params.get('offset') else [
                {'id': i, 'conversation_message_id': i, 'from_id': 1 if i % 3 else 2,
                 'date': int(datetime.now(timezone.utc).timestamp()) - i * 3600,
                 'text': ['Встречаемся в субботу?', 'Сохранил список, спасибо.', 'Давай обсудим завтра.', 'Пришлю вечером.'][i % 4]} for i in range(1, 9)]}
        if method == 'messages.delete':
            return [{'peer_id': params['peer_id'], 'conversation_message_id': int(i), 'response': 1} for i in params['cmids'].split(',')]
        raise ValueError('Неизвестный демо-метод')
