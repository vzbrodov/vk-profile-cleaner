import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
import threading
import logging
from logging.handlers import RotatingFileHandler
from zoneinfo import ZoneInfo
from platformdirs import user_data_path
from PySide6.QtCore import Qt, QObject, Signal, QRunnable, QThreadPool, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, QTimer
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QTableView, QDateEdit, QMessageBox, QFileDialog,
    QPlainTextEdit, QHeaderView, QCheckBox, QProgressBar)
from ..core.api import ApiClient
from ..core.demo import DemoApi
from ..core.models import DateRange, CleanerError
from ..core import services
from ..storage.database import Database


class Signals(QObject):
    done = Signal(object)
    failed = Signal(str)
    event = Signal(object)


class Worker(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function, self.signals = function, Signals()

    def run(self):
        try:
            self.signals.done.emit(self.function(self.signals.event.emit))
        except CleanerError as exc:
            self.signals.failed.emit(str(exc))
        except Exception:
            self.signals.failed.emit('Операция завершилась с ошибкой. Повторите или проверьте настройки.')


class Table(QAbstractTableModel):
    changed = Signal()

    def __init__(self, headers, rows=()):
        super().__init__()
        self.headers, self.rows = headers, list(rows)
        self.all_rows = self.rows
        self.checked = {r[0] for r in self.rows}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.headers)

    def headerData(self, section, orientation, role):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.headers[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        if role == Qt.CheckStateRole and index.column() == 0:
            return Qt.Checked if self.rows[index.row()][0] in self.checked else Qt.Unchecked
        if role == Qt.DisplayRole:
            return str(self.rows[index.row()][index.column() + 1])

    def flags(self, index):
        return super().flags(index) | (Qt.ItemIsUserCheckable if index.column() == 0 else Qt.NoItemFlags)

    def setData(self, index, value, role):
        if role != Qt.CheckStateRole:
            return False
        key = self.rows[index.row()][0]
        if value == Qt.Checked.value:
            self.checked.add(key)
        else:
            self.checked.discard(key)
        self.dataChanged.emit(index, index, [role])
        self.changed.emit()
        return True


class Window(QMainWindow):
    def __init__(self, demo=False):
        super().__init__()
        self.setWindowTitle('VK Cleaner — мастер очистки' + (' [ДЕМОНСТРАЦИЯ]' if demo else ''))
        self.resize(1040, 740)
        self.demo = demo
        self.temp = tempfile.TemporaryDirectory() if demo else None
        self.path = Path(self.temp.name if demo else user_data_path('vk-cleaner')) / 'cleaner.sqlite3'
        self.pool = QThreadPool(self)
        self.busy = False
        self.api = None
        self.job = self.sid = None
        self.step = 'dialogs'
        panel = QWidget()
        self.setCentralWidget(panel)
        layout = QVBoxLayout(panel)
        mast = QLabel('VK CLEANER   /   Личная переписка')
        mast.setObjectName('mast')
        layout.addWidget(mast)
        self.description = QLabel('1. Вход  →  2. Диалоги и период  →  3. Проверка  →  4. Результат')
        layout.addWidget(self.description)
        auth = QHBoxLayout()
        self.token = QLineEdit()
        self.token.setPlaceholderText('Пользовательский токен VK')
        self.token.setEchoMode(QLineEdit.Password)
        auth.addWidget(self.token)
        self.login = QPushButton('Войти' if not demo else 'Открыть демонстрацию')
        self.login.clicked.connect(self.authenticate)
        auth.addWidget(self.login)
        layout.addLayout(auth)
        security = QHBoxLayout()
        self.remember = QCheckBox('Запомнить токен в системном хранилище')
        security.addWidget(self.remember)
        logout = QPushButton('Выйти')
        logout.clicked.connect(self.logout)
        security.addWidget(logout)
        forget = QPushButton('Удалить сохранённый токен')
        forget.clicked.connect(self.forget_token)
        security.addWidget(forget)
        restore = QPushButton('Войти сохранённым токеном')
        restore.clicked.connect(self.restore_token)
        security.addWidget(restore)
        self.restore_button = restore
        self.logout_button, self.forget_button = logout, forget
        layout.addLayout(security)
        self.account_label = QLabel('Токен используется только в текущем сеансе.')
        layout.addWidget(self.account_label)
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Поиск в списке…')
        tools.addWidget(self.search)
        self.start = QDateEdit(date.today() - timedelta(days=30))
        self.end = QDateEdit(date.today())
        for label, widget in [('С', self.start), ('По', self.end)]:
            widget.setCalendarPopup(True)
            widget.setDisplayFormat('dd.MM.yyyy')
            tools.addWidget(QLabel(label))
            tools.addWidget(widget)
        tools.addWidget(QLabel('Москва / UTC+3'))
        layout.addLayout(tools)
        self.table = QTableView()
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table, 1)
        self.filter_generation = 0
        self.filter_workers = {}
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(180)
        self.filter_timer.timeout.connect(lambda: self.filter(self.search.text()))
        self.search.textChanged.connect(lambda _: self.filter_timer.start())
        buttons = QHBoxLayout()
        self.all_button = QPushButton('Выбрать видимые')
        self.none_button = QPushButton('Снять всё')
        self.all_button.clicked.connect(self.select_visible)
        self.none_button.clicked.connect(self.clear_selection)
        buttons.addWidget(self.all_button)
        buttons.addWidget(self.none_button)
        self.count = QLabel('Нет данных')
        buttons.addWidget(self.count, 1)
        self.export = QPushButton('Экспорт CSV')
        self.export.clicked.connect(self.export_csv)
        buttons.addWidget(self.export)
        self.next = QPushButton('Анализировать')
        self.next.clicked.connect(self.advance)
        buttons.addWidget(self.next)
        self.resume = QPushButton('Продолжить задание')
        self.resume.clicked.connect(self.resume_job)
        buttons.addWidget(self.resume)
        self.back = QPushButton('К диалогам')
        self.back.clicked.connect(self.back_to_dialogs)
        buttons.addWidget(self.back)
        layout.addLayout(buttons)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        self.log.setMaximumHeight(125)
        layout.addWidget(self.log)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)
        self.cancel = QPushButton('Отменить операцию')
        self.cancel.clicked.connect(self.stop)
        layout.addWidget(self.cancel)
        self.set_table(['Диалог', 'peer_id'], [])
        self.controls()
        self.logger = logging.getLogger('vk-cleaner.' + str(id(self)))
        if not demo:
            logpath = self.path.parent / 'logs'
            logpath.mkdir(parents=True, exist_ok=True, mode=0o700)
            handler = RotatingFileHandler(logpath / 'cleaner.log', maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)

    def controls(self):
        for widget in (self.login, self.next, self.resume, self.all_button, self.none_button):
            widget.setEnabled(not self.busy)
        self.next.setEnabled(not self.busy and self.api is not None and bool(self.model.checked))
        self.export.setEnabled(not self.busy and self.sid is not None)
        self.cancel.setEnabled(self.busy)
        self.logout_button.setEnabled(not self.busy)
        self.forget_button.setEnabled(not self.busy)
        self.restore_button.setEnabled(not self.busy)
        self.back.setEnabled(not self.busy and self.step == 'preview')
        self.table.setEnabled(not self.busy)
        self.start.setEnabled(not self.busy and self.step == 'dialogs')
        self.end.setEnabled(not self.busy and self.step == 'dialogs')

    def set_table(self, headers, rows):
        self.filter_generation = getattr(self, 'filter_generation', 0) + 1
        self.model = Table(headers, rows)
        self.model.changed.connect(self.update_count)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.table.setModel(self.proxy)
        self.update_count()

    def filter(self, text):
        self.filter_generation += 1
        generation = self.filter_generation
        model = self.model
        source = model.all_rows
        needle = text.casefold()
        worker = Worker(lambda _: [r for r in source if needle in ' '.join(str(v) for v in r[1:]).casefold()])
        self.filter_workers[generation] = worker
        def done(rows):
            self.filter_workers.pop(generation, None)
            if generation != self.filter_generation or model is not self.model:
                return
            model.beginResetModel()
            model.rows = rows
            model.endResetModel()
        worker.signals.done.connect(done)
        self.pool.start(worker)

    def update_count(self):
        self.count.setText(f'Выбрано {len(self.model.checked)} из {len(self.model.all_rows)}')
        self.controls()

    def select_visible(self):
        self.model.beginResetModel()
        self.model.checked.update(self.model.rows[self.proxy.mapToSource(self.proxy.index(i, 0)).row()][0] for i in range(self.proxy.rowCount()))
        self.model.endResetModel()
        self.update_count()

    def clear_selection(self):
        self.model.beginResetModel()
        self.model.checked.clear()
        self.model.endResetModel()
        self.update_count()

    def work(self, function, done, reset_cancel=True):
        if self.busy:
            return
        self.busy = True
        if self.api and reset_cancel:
            self.api.cancel.clear()
        self.controls()
        self.progress.setRange(0, 0)
        self.worker = Worker(function)
        self.worker.signals.event.connect(self.show_event)
        def success(result):
            self.busy = False
            self.progress.setRange(0, 1)
            self.progress.setValue(1)
            done(result)
            self.controls()
        def failed(message):
            self.busy = False
            self.progress.setRange(0, 1)
            self.progress.setValue(0)
            self.log.appendPlainText(message)
            self.logger.warning(message)
            QMessageBox.warning(self, 'Операция остановлена', message)
            self.controls()
        self.worker.signals.done.connect(success)
        self.worker.signals.failed.connect(failed)
        self.pool.start(self.worker)

    def authenticate(self):
        token = self.token.text().strip()
        remember = self.remember.isChecked()
        api = DemoApi() if self.demo else ApiClient(token)
        self.token.clear()
        self.api = None
        self.sid = self.job = None
        self.step = 'dialogs'
        self.set_table(['Диалог', 'peer_id'], [])
        def task(emit):
            account = services.identity(api)
            peers = services.dialogs(api)
            if remember and not self.demo:
                try:
                    import keyring
                    keyring.set_password('vk-cleaner', 'token', token)
                except Exception:
                    emit('Токен не сохранён: системное хранилище недоступно.')
            return account, peers
        def done(result):
            self.api = api
            (self.account, name), self.peers = result
            self.account_label.setText(f'{name} · ID {self.account}' + (' · Демонстрация, без запросов к VK' if self.demo else ''))
            self.step, self.sid, self.job = 'dialogs', None, None
            self.next.setText('Анализировать')
            self.set_table(['Диалог', 'peer_id'], [(p.peer_id, p.title, p.peer_id) for p in self.peers])
        self.work(task, done)

    def advance(self):
        if self.step == 'dialogs':
            if QMessageBox.question(self, 'Локальный предпросмотр', 'Сохранить фрагменты своих сообщений на этом компьютере для предпросмотра?') != QMessageBox.Yes:
                return
            peers = [p for p in self.peers if p.peer_id in self.model.checked]
            period = DateRange(self.start.date().toPython(), self.end.date().toPython())
            def task(emit):
                store = Database(self.path)
                try:
                    sid = services.scan(self.api, store, self.account, peers, period, emit)
                    rows = [tuple(r) for r in store.db.execute('select id,title,timestamp,snippet from candidates where scan=?', (sid,))]
                    return sid, [(i, title, datetime.fromtimestamp(ts, ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M'), text) for i, title, ts, text in rows]
                finally:
                    store.close()
            def done(result):
                self.sid, rows = result
                self.step = 'preview'
                self.next.setText('Удалить выбранное')
                self.search.clear()
                self.set_table(['Диалог', 'Дата', 'Сообщение'], rows)
            self.work(task, done)
        else:
            selected = set(self.model.checked)
            if QMessageBox.warning(self, 'Подтверждение удаления', f'Аккаунт {self.account}. Период {self.start.text()} — {self.end.text()} (Москва).\nУдалить для всех {len(selected)} сообщений? Действие необратимо.' + ('\nДЕМО: изменятся только тестовые данные.' if self.demo else ''), QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
                return
            def task(emit):
                store = Database(self.path)
                try:
                    with store.db:
                        store.db.execute('update candidates set selected=0 where scan=?', (self.sid,))
                        store.db.executemany('update candidates set selected=1 where id=? and scan=?', [(i, self.sid) for i in selected])
                    job, digest = store.freeze(self.sid)
                    emit('Задание сохранено: ' + job)
                    return job, digest
                finally:
                    store.close()
            self.work(task, self.start_delete)

    def start_delete(self, result):
        self.job, digest = result
        def task(emit):
            store = Database(self.path)
            try:
                return services.delete(self.api, store, self.job, self.account, digest, emit)
            finally:
                store.close()
        def done(summary):
            self.log.appendPlainText('Результат: ' + str(summary))
            self.step = 'dialogs'
            self.next.setText('Анализировать')
            self.sid = None
            self.set_table(['Диалог', 'peer_id'], [(p.peer_id, p.title, p.peer_id) for p in self.peers])
        self.work(task, done, reset_cancel=False)

    def resume_job(self):
        if not self.api or not hasattr(self, 'account'):
            return
        def task(emit):
            store = Database(self.path)
            try:
                row = store.db.execute("select id,digest,scan from jobs where account=? and state in ('ready','paused','needs_review') order by rowid desc limit 1", (self.account,)).fetchone()
                if not row:
                    raise CleanerError('Нет незавершённых заданий.')
                import json
                config = json.loads(store.db.execute('select config from scans where id=?', (row['scan'],)).fetchone()[0])
                return row['id'], row['digest'], config, store.summary(row['id'])
            finally:
                store.close()
        def done(result):
            job, digest, config, summary = result
            prompt = f'Аккаунт {self.account}\n{config["start"]} — {config["end"]} ({config["timezone"]})\n{summary}\nПродолжить удаление для всех? Неизвестные результаты блокируют запуск.'
            if QMessageBox.question(self, 'Возобновление', prompt) == QMessageBox.Yes:
                self.api.cancel.clear()
                self.start_delete((job, digest))
        self.work(task, done)

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, 'Экспорт выбранных сообщений', 'messages.csv', 'CSV (*.csv)')
        if not path:
            return
        selected = set(self.model.checked)
        def task(emit):
            store = Database(self.path)
            try:
                with store.db:
                    store.db.execute('update candidates set selected=0 where scan=?', (self.sid,))
                    store.db.executemany('update candidates set selected=1 where scan=? and id=?', [(self.sid, i) for i in selected])
                services.export_csv(store, self.sid, path)
            finally:
                store.close()
        self.work(task, lambda _: self.log.appendPlainText('CSV сохранён.'))

    def show_event(self, value):
        self.log.appendPlainText(str(value))
        if isinstance(value, dict):
            self.logger.info('Результаты пакета: %s', value)
        elif str(value).startswith('Задание сохранено:'):
            self.logger.info('%s', value)

    def logout(self):
        if self.busy:
            return
        self.api = None
        self.sid = self.job = None
        self.step = 'dialogs'
        self.token.clear()
        self.account_label.setText('Вы вышли. Отзовите ненужный токен в VK.')
        self.set_table(['Диалог', 'peer_id'], [])

    def back_to_dialogs(self):
        self.step, self.sid = 'dialogs', None
        self.next.setText('Анализировать')
        self.search.clear()
        self.set_table(['Диалог', 'peer_id'], [(p.peer_id, p.title, p.peer_id) for p in self.peers])

    def restore_token(self):
        def task(emit):
            try:
                import keyring
                token = keyring.get_password('vk-cleaner', 'token')
                if not token:
                    raise ValueError()
                return token
            except Exception:
                raise CleanerError('Сохранённый токен недоступен.') from None
        def done(token):
            self.token.setText(token)
            self.authenticate()
        self.work(task, done)

    def forget_token(self):
        def task(emit):
            try:
                import keyring
                keyring.delete_password('vk-cleaner', 'token')
            except Exception:
                raise CleanerError('Не удалось удалить токен или он не был сохранён.') from None
        self.work(task, lambda _: self.log.appendPlainText('Сохранённый токен удалён.'))

    def stop(self):
        if self.api:
            self.api.cancel.set()
        self.log.appendPlainText('Останавливаем…')

    def closeEvent(self, event):
        if self.busy:
            self.stop()
            event.ignore()
            self.log.appendPlainText('Дождитесь завершения текущего запроса и закройте окно.')
        else:
            event.accept()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--demo', action='store_true')
    args = parser.parse_args()
    app = QApplication([])
    apply_theme(app)
    window = Window(args.demo)
    window.show()
    return app.exec()


def apply_theme(app):
    app.setStyle('Fusion')
    app.setStyleSheet('''
    QWidget { font-family: "DejaVu Sans"; font-size: 12px; color: #202b38; }
    QMainWindow { background: #d7d5cc; }
    QLabel#mast { background: #355774; color: white; padding: 16px; font-size: 20px; font-weight: bold; }
    QPushButton { background: #e7e5dd; border: 1px solid #7f8586; border-top: 2px solid white; border-left: 2px solid white; padding: 7px 12px; }
    QPushButton:pressed { border: 2px inset #7f8586; background: #cccac1; }
    QPushButton:disabled { color: #888; }
    QLineEdit, QDateEdit, QPlainTextEdit { background: #fffef8; border: 1px solid #858d91; padding: 5px; }
    QTableView { background: #fffef8; alternate-background-color: #edf1f2; gridline-color: #ccd1d4; selection-background-color: #355774; selection-color: white; }
    QHeaderView::section { background: #d7dfe3; border: 1px solid #a5afb4; padding: 7px; }
    ''')


if __name__ == '__main__':
    raise SystemExit(main())
