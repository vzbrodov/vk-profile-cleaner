from PySide6.QtCore import QTimer
from vk_profile_cleaner.gui.app import Window


def test_large_search_runs_in_background(qtbot):
    window = Window(True)
    qtbot.addWidget(window)
    window.set_table(['Сообщение'], [(i, f'Сообщение {i}') for i in range(100000)])
    ticks = []
    timer = QTimer(window)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    window.search.setText('99999')
    qtbot.waitUntil(lambda: window.model.rowCount() == 1, timeout=5000)
    assert len(ticks) > 3
    assert len(window.model.checked) == 100000
