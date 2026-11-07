PYTHON ?= python3
VENV ?= .venv
PY = $(VENV)/bin/python

.PHONY: help setup dev run demo test build
help:
	@echo 'make setup  — установить программу'
	@echo 'make dev    — установить зависимости тестирования и сборки'
	@echo 'make run    — запустить GUI'
	@echo 'make demo   — запустить демонстрацию без токена'
	@echo 'make test   — выполнить тесты'
	@echo 'make build  — собрать автономную папку для Linux в dist/vk-profile-cleaner'

$(PY):
	$(PYTHON) -m venv $(VENV)

setup: $(PY)
	$(PY) -m pip install -e '.[keyring]'

dev: $(PY)
	$(PY) -m pip install -e '.[dev,keyring,bundle]'

run: $(PY)
	$(PY) -m vk_profile_cleaner.gui.app

demo: $(PY)
	$(PY) -m vk_profile_cleaner.gui.app --demo

test: $(PY)
	QT_QPA_PLATFORM=offscreen $(PY) -m pytest -q

build: $(PY)
	XDG_CACHE_HOME=$(CURDIR)/build/cache $(PY) -m PyInstaller --noconfirm --name vk-profile-cleaner --onedir --windowed --paths src --specpath build --workpath build/pyinstaller --distpath dist --copy-metadata keyring --collect-submodules keyring.backends tools/gui_entry.py
	cp LICENSE dist/vk-profile-cleaner/LICENSE
