from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox
from vk_cleaner.gui.app import Window, apply_theme


def test_demo_flow(qtbot, monkeypatch):
    window = Window(demo=True)
    qtbot.addWidget(window)
    window.show()
    qtbot.mouseClick(window.login, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.busy)
    assert len(window.model.rows) == 3
    window.search.setText('Книжный')
    qtbot.waitUntil(lambda: window.proxy.rowCount() == 1)
    window.clear_selection()
    window.select_visible()
    assert window.model.checked == {103}
    window.search.clear()
    monkeypatch.setattr(QMessageBox, 'question', lambda *args: QMessageBox.Yes)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: QMessageBox.Yes)
    qtbot.mouseClick(window.next, Qt.LeftButton)
    qtbot.waitUntil(lambda: not window.busy)
    assert window.step == 'preview'
    assert len(window.model.rows) == 6
    qtbot.mouseClick(window.next, Qt.LeftButton)
    qtbot.waitUntil(lambda: window.step == 'dialogs' and not window.busy)
    assert 'confirmed' in window.log.toPlainText()


def test_token_is_immediately_masked_with_stars(qtbot, qapp):
    apply_theme(qapp)
    window = Window(demo=True)
    qtbot.addWidget(window)
    window.show()
    window.token.setFocus()
    qtbot.keyClicks(window.token, 'private-token-123')
    assert window.token.displayText() == '*' * len('private-token-123')
    assert window.token.text() == 'private-token-123'
