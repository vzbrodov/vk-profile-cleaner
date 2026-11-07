import argparse
from datetime import date
import getpass
from pathlib import Path
from platformdirs import user_data_path
from .core.api import ApiClient
from .core.demo import DemoApi
from .core.models import DateRange, CleanerError
from .core import services
from .storage.database import Database
from .identity import database_path, legacy_database_path
from .storage.legacy import migrate_database, migrate_token


def main():
    parser = argparse.ArgumentParser(description='Локальная очистка сообщений VK')
    parser.add_argument('--database', type=Path, default=database_path())
    parser.add_argument('--demo', action='store_true')
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('migrate-data', help='Импорт прежней базы без перезаписи новой')
    sub.add_parser('migrate-token', help='Перенос прежнего токена через keyring')
    scan = sub.add_parser('scan')
    scan.add_argument('--date-from', type=date.fromisoformat, required=True)
    scan.add_argument('--date-to', type=date.fromisoformat, required=True)
    scan.add_argument('--timezone', default='Europe/Moscow')
    targets = scan.add_mutually_exclusive_group(required=True)
    targets.add_argument('--all', action='store_true')
    targets.add_argument('--peer-id', type=int, action='append')
    scan.add_argument('--dry-run', action='store_true')
    for name in ('run', 'resume', 'report', 'export'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--job-id', required=True)
        if name in ('run', 'resume'):
            cmd.add_argument('--confirm', required=True)
        if name == 'export':
            cmd.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.demo and args.database == database_path():
        parser.error('Для demo укажите отдельную --database.')
    if args.command in ('migrate-data', 'migrate-token'):
        try:
            if args.demo:
                raise CleanerError('Импорт недоступен в demo.')
            if args.command == 'migrate-data':
                migrate_database(legacy_database_path(), args.database)
                print('Задания скопированы. Старую программу больше не запускайте.')
            else:
                import keyring
                migrate_token(keyring, remove_old=True)
                print('Токен перенесён через системное хранилище.')
            return 0
        except (CleanerError, ImportError) as exc:
            print(str(exc) if isinstance(exc, CleanerError) else 'Системное хранилище недоступно.')
            return 1
    if args.database == database_path() and legacy_database_path().is_file() and not args.database.exists():
        print('Найдена прежняя база. Сначала выполните migrate-data либо выберите другую --database.')
        return 1
    store = Database(args.database)
    try:
        if args.command in ('report', 'export'):
            if args.command == 'report':
                print(store.summary(args.job_id))
            else:
                row = store.db.execute('select scan from jobs where id=?', (args.job_id,)).fetchone()
                if not row:
                    raise CleanerError('Задание не найдено.')
                services.export_csv(store, row[0], args.output)
            return 0
        api = DemoApi() if args.demo else ApiClient(getpass.getpass('Токен VK: '))
        account, _ = services.identity(api)
        if args.command == 'scan':
            from .core.models import Dialog
            peers = services.dialogs(api) if args.all else [Dialog(p, f'Диалог {p}') for p in args.peer_id]
            with store.lock():
                sid = services.scan(api, store, account, peers, DateRange(args.date_from, args.date_to, args.timezone), print)
                job, digest = store.freeze(sid)
            print(f'Задание: {job}\nПодтверждение: {digest}\nУдаление не выполнялось.')
            return 0
        result = services.delete(api, store, args.job_id, account, args.confirm, print)
        print(result)
        return 3 if result.get('failed') or result.get('unknown') else 0
    except CleanerError as exc:
        print(str(exc))
        return 3 if exc.unknown else 1
    except (ValueError, KeyError):
        print('Некорректные параметры или ответ API.')
        return 1
    except KeyboardInterrupt:
        return 130
    finally:
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
