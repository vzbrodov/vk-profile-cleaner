"""Идентификаторы приложения и расположение пользовательских данных."""
from platformdirs import user_data_path

APP_NAME = 'VK Profile Cleaner'
APP_ID = 'vk-profile-cleaner'
LEGACY_APP_ID = 'vk-cleaner'
TOKEN_ACCOUNT = 'token'


def database_path():
    return user_data_path(APP_ID) / 'cleaner.sqlite3'


def legacy_database_path():
    return user_data_path(LEGACY_APP_ID) / 'cleaner.sqlite3'
