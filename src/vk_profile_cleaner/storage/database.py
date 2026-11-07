import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from ..core.models import CleanerError
from ..identity import database_path, legacy_database_path


class Database:
    def __init__(self, path):
        self.path = Path(path)
        if self.path == database_path() and not self.path.exists() and legacy_database_path().is_file():
            raise CleanerError('Найдена прежняя база. Сначала импортируйте её через меню Файл.')
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists() and self.path.is_symlink():
            raise CleanerError('База не может быть символической ссылкой.')
        self.db = sqlite3.connect(self.path)
        os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        if self.db.execute('pragma user_version').fetchone()[0] > 1:
            raise CleanerError('Версия базы новее приложения.')
        self.db.executescript('''
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS scans(id TEXT PRIMARY KEY, account INTEGER, state TEXT, config TEXT);
        CREATE TABLE IF NOT EXISTS candidates(
          id INTEGER PRIMARY KEY, scan TEXT REFERENCES scans(id) ON DELETE CASCADE,
          peer INTEGER, title TEXT, mid INTEGER, cmid INTEGER, timestamp INTEGER,
          snippet TEXT, selected INTEGER DEFAULT 1, UNIQUE(scan,peer,cmid));
        CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, scan TEXT REFERENCES scans(id),
          account INTEGER, digest TEXT, state TEXT);
        CREATE TABLE IF NOT EXISTS items(job TEXT REFERENCES jobs(id) ON DELETE CASCADE,
          candidate INTEGER REFERENCES candidates(id), state TEXT, PRIMARY KEY(job,candidate));
        CREATE INDEX IF NOT EXISTS candidate_scan ON candidates(scan,id);
        CREATE INDEX IF NOT EXISTS item_state ON items(job,state);
        PRAGMA user_version=1;
        ''')

    def close(self):
        self.db.close()

    @contextlib.contextmanager
    def lock(self):
        with open(str(self.path) + '.lock', 'a') as handle:
            os.chmod(handle.name, 0o600)
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CleanerError('Другая операция уже использует базу.') from None
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def create_scan(self, account, config):
        ident = uuid.uuid4().hex
        with self.db:
            self.db.execute('insert into scans values(?,?,?,?)', (ident, account, 'scanning', json.dumps(config)))
        return ident

    def freeze(self, scan):
        row = self.db.execute('select * from scans where id=?', (scan,)).fetchone()
        if not row or row['state'] != 'preview':
            raise CleanerError('Анализ не завершён.')
        rows = self.db.execute('select id,peer,mid,cmid,timestamp from candidates where scan=? and selected=1 order by id', (scan,)).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            raise CleanerError('Нет выбранных сообщений.')
        digest = self.digest(row['account'], scan, row['config'], rows)
        job = uuid.uuid4().hex
        with self.db:
            self.db.execute('insert into jobs values(?,?,?,?,?)', (job, scan, row['account'], digest, 'ready'))
            self.db.executemany('insert into items values(?,?,?)', [(job, i, 'pending') for i in ids])
            self.db.execute("update scans set state='frozen' where id=?", (scan,))
        return job, digest

    def summary(self, job):
        return dict(self.db.execute('select state,count(*) from items where job=? group by state', (job,)))

    @staticmethod
    def digest(account, scan, config, rows):
        return hashlib.sha256(json.dumps([account, scan, config, [list(r) for r in rows]]).encode()).hexdigest()

    def verify(self, job):
        row = self.db.execute('select j.*,s.config from jobs j join scans s on j.scan=s.id where j.id=?', (job,)).fetchone()
        rows = self.db.execute('select c.id,c.peer,c.mid,c.cmid,c.timestamp from candidates c join items i on c.id=i.candidate where i.job=? order by c.id', (job,)).fetchall()
        if not row or self.digest(row['account'], row['scan'], row['config'], rows) != row['digest']:
            raise CleanerError('Состав задания изменился. Удаление заблокировано.')
