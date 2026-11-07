"""Явный импорт данных прежнего приложения без перезаписи новой базы."""
import contextlib
import fcntl
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from ..core.models import CleanerError
from ..identity import APP_ID, LEGACY_APP_ID, TOKEN_ACCOUNT


@contextlib.contextmanager
def migration_locks(source, destination):
    with contextlib.ExitStack() as stack:
        for path in sorted([source, destination]):
            handle = stack.enter_context(open(str(path) + '.lock', 'a'))
            os.chmod(handle.name, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CleanerError('База занята. Завершите операцию в другом приложении.') from None
        yield


def migrate_database(source, destination):
    try:
        return _migrate_database(source, destination)
    except (sqlite3.Error, OSError):
        raise CleanerError('Не удалось перенести базу. Проверьте доступ к каталогам. Исходная база сохранена.') from None


def _migrate_database(source, destination):
    source, destination = Path(source), Path(destination)
    if source == destination or source.is_symlink() or not source.is_file():
        raise CleanerError('Прежняя база не найдена или путь недопустим.')
    if destination.exists() or destination.is_symlink():
        raise CleanerError('Новая база уже существует. Автоматическое объединение запрещено.')
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with migration_locks(source, destination):
        if destination.exists():
            raise CleanerError('Новая база уже существует.')
        fd, temporary = tempfile.mkstemp(prefix='.migration-', dir=destination.parent)
        os.close(fd)
        try:
            with contextlib.closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as old:
                if old.execute('pragma user_version').fetchone()[0] != 1:
                    raise CleanerError('Версия прежней базы не поддерживается.')
                required = {'scans', 'candidates', 'jobs', 'items'}
                tables = {r[0] for r in old.execute("select name from sqlite_master where type='table'")}
                if not required <= tables:
                    raise CleanerError('В прежней базе отсутствуют таблицы заданий.')
                with contextlib.closing(sqlite3.connect(temporary)) as new:
                    deadline = time.monotonic() + 15
                    def progress(status, remaining, total):
                        if time.monotonic() > deadline:
                            raise CleanerError('Время импорта истекло. Закройте прежнее приложение и повторите.')
                    old.backup(new, pages=128, progress=progress, sleep=0.05)
                    if new.execute('pragma integrity_check').fetchone()[0] != 'ok':
                        raise CleanerError('Проверка скопированной базы не пройдена.')
            # link создаёт имя только если оно ещё свободно. Оригинал не удаляется.
            os.link(temporary, destination)
        except (sqlite3.Error, OSError):
            raise CleanerError('Не удалось перенести базу. Исходная база сохранена.') from None
        finally:
            os.unlink(temporary)
    return destination


def migrate_token(keyring, remove_old=False):
    try:
        if keyring.get_password(APP_ID, TOKEN_ACCOUNT):
            raise CleanerError('В новом хранилище уже есть токен. Перезапись запрещена.')
        token = keyring.get_password(LEGACY_APP_ID, TOKEN_ACCOUNT)
        if not token:
            raise CleanerError('Прежний токен не найден.')
        keyring.set_password(APP_ID, TOKEN_ACCOUNT, token)
        if keyring.get_password(APP_ID, TOKEN_ACCOUNT) != token:
            raise CleanerError('Не удалось проверить запись токена. Старый токен сохранён.')
        if remove_old:
            keyring.delete_password(LEGACY_APP_ID, TOKEN_ACCOUNT)
    except CleanerError:
        raise
    except Exception:
        raise CleanerError('Системное хранилище недоступно. Проверьте сохранённые записи.') from None
