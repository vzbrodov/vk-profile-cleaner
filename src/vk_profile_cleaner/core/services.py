import csv
from datetime import datetime
from zoneinfo import ZoneInfo
from .models import CleanerError, Cancelled, Dialog


def identity(api):
    result = api.call('users.get', {})
    if not isinstance(result, list) or not result or type(result[0].get('id')) is not int:
        raise CleanerError('Не получен пользователь.')
    return result[0]['id'], ' '.join(result[0].get(k, '') for k in ('first_name', 'last_name'))


def dialogs(api, emit=lambda _: None):
    found = {}
    for kind in ('all', 'archive'):
        offset = 0
        while True:
            result = api.call('messages.getConversations', {'count': 200, 'offset': offset, 'filter': kind, 'extended': 1})
            items = result['items']
            profiles = {p['id']: p.get('first_name', '') + ' ' + p.get('last_name', '') for p in result.get('profiles', [])}
            groups = {-g['id']: g['name'] for g in result.get('groups', [])}
            added = []
            for item in items:
                conv = item['conversation']
                peer = conv['peer']['id']
                if peer not in found:
                    title = conv.get('chat_settings', {}).get('title') or profiles.get(peer) or groups.get(peer) or f'Диалог {peer}'
                    found[peer] = Dialog(peer, title)
                    added.append(found[peer])
            emit(added)
            if not items or len(items) < 200:
                break
            if not added and kind == 'all':
                raise CleanerError('Список диалогов не продвигается.')
            offset += len(items)
    return list(found.values())


def scan(api, store, account, peers, period, emit=lambda _: None):
    low, high = period.bounds()
    sid = store.create_scan(account, {'start': str(period.start), 'end': str(period.end), 'timezone': period.timezone})
    try:
        for peer in peers:
            offset, seen, initial_count = 0, set(), None
            while True:
                result = api.call('messages.getHistory', {'peer_id': peer.peer_id, 'count': 200, 'offset': offset, 'rev': 0})
                if initial_count is None:
                    initial_count = result['count']
                elif result['count'] != initial_count:
                    raise CleanerError('История изменилась во время анализа. Повторите анализ.')
                messages = result['items']
                if not messages:
                    break
                fresh = 0
                with store.db:
                    for m in messages:
                        cmid = m.get('conversation_message_id')
                        if type(cmid) is not int or cmid <= 0:
                            raise CleanerError('Сообщение без надёжного ключа.')
                        if cmid in seen:
                            continue
                        seen.add(cmid)
                        fresh += 1
                        if m['from_id'] == account and low <= m['date'] < high and not m.get('deleted'):
                            store.db.execute('insert or ignore into candidates(scan,peer,title,mid,cmid,timestamp,snippet) values(?,?,?,?,?,?,?)',
                                (sid, peer.peer_id, peer.title, m.get('id'), cmid, m['date'], m.get('text', '')[:200]))
                emit(f'Анализ: {peer.title}, просмотрено {len(seen)}')
                if len(messages) < 200:
                    break
                if not fresh:
                    raise CleanerError('История не продвигается.')
                offset += 180
        with store.db:
            store.db.execute("update scans set state='preview' where id=?", (sid,))
        return sid
    except BaseException:
        with store.db:
            store.db.execute("update scans set state='incomplete' where id=?", (sid,))
        raise


def parse_delete(response, rows):
    # Формат messages_delete_full_response: peer_id, conversation_message_id, response.
    if not isinstance(response, list):
        raise CleanerError('Неизвестный ответ удаления.', unknown=True)
    results = {}
    for value in response:
        if not isinstance(value, dict):
            raise CleanerError('Неизвестный ответ удаления.', unknown=True)
        key = (value.get('peer_id'), value.get('conversation_message_id'))
        status = value.get('response')
        if value.get('error') and status is None:
            status = 0
        if key in results or type(status) is not int or status not in (0, 1):
            raise CleanerError('Неоднозначный ответ удаления.', unknown=True)
        results[key] = 'confirmed' if status == 1 else 'failed'
    return {r['id']: results.get((r['peer'], r['cmid']), 'unknown') for r in rows}


def delete(api, store, job, account, digest, emit=lambda _: None):
    actual_account, _ = identity(api)
    if actual_account != account:
        raise CleanerError('Токен принадлежит другому аккаунту.')
    with store.lock():
        meta = store.db.execute('select * from jobs where id=?', (job,)).fetchone()
        if not meta or meta['account'] != account or meta['digest'] != digest:
            raise CleanerError('Аккаунт или подтверждение не совпадает.')
        store.verify(job)
        with store.db:
            store.db.execute("update items set state='unknown' where job=? and state='in_flight'", (job,))
        if store.summary(job).get('unknown'):
            raise CleanerError('Есть неизвестные результаты: требуется ручная проверка.')
        state = 'needs_review'
        try:
            while True:
                if api.cancel.is_set():
                    raise Cancelled()
                pending = store.db.execute("select c.* from candidates c join items i on c.id=i.candidate where i.job=? and i.state='pending' order by c.peer,c.id limit 100", (job,)).fetchall()
                if not pending:
                    break
                rows = [r for r in pending if r['peer'] == pending[0]['peer']]
                with store.db:
                    store.db.executemany("update items set state='in_flight' where job=? and candidate=?", [(job, r['id']) for r in rows])
                try:
                    response = api.call('messages.delete', {'peer_id': rows[0]['peer'], 'cmids': ','.join(str(r['cmid']) for r in rows), 'delete_for_all': 1})
                    states = parse_delete(response, rows)
                except CleanerError as exc:
                    state = 'unknown' if exc.unknown else 'pending'
                    with store.db:
                        store.db.executemany('update items set state=? where job=? and candidate=?', [(state, job, r['id']) for r in rows])
                    raise
                with store.db:
                    store.db.executemany('update items set state=? where job=? and candidate=?', [(s, job, i) for i, s in states.items()])
                emit(store.summary(job))
                if 'unknown' in states.values():
                    raise CleanerError('Часть результатов неизвестна.', unknown=True)
            state = 'completed_with_errors' if store.summary(job).get('failed') else 'completed'
            with store.db:
                store.db.execute("update candidates set snippet='' where scan=?", (meta['scan'],))
        except CleanerError:
            state = 'needs_review' if store.summary(job).get('unknown') else 'paused'
            raise
        finally:
            with store.db:
                store.db.execute('update jobs set state=? where id=?', (state, job))
        return store.summary(job)


def export_csv(store, scan_id, destination):
    def safe(value):
        value = str(value or '')
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value
    import json
    config = json.loads(store.db.execute('select config from scans where id=?', (scan_id,)).fetchone()[0])
    with open(destination, 'w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['peer_id', 'peer_name', 'message_id', 'date', 'text_snippet', 'cmid'])
        for row in store.db.execute('select * from candidates where scan=? and selected=1', (scan_id,)):
            writer.writerow([row['peer'], safe(row['title']), row['mid'], datetime.fromtimestamp(row['timestamp'], ZoneInfo(config['timezone'])).isoformat(), safe(row['snippet']), row['cmid']])
