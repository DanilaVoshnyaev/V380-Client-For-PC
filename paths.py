"""
Пути к файлам — одинаково для запуска из исходников и из собранного .exe.

PyInstaller распаковывает вложенные данные во временный каталог (_MEIPASS),
который пересоздаётся при каждом запуске. Поэтому база и wsdl читаются оттуда,
а настройки и записи пользователя должны лежать рядом с exe и переживать
перезапуск.
"""
import os
import sys

FROZEN = getattr(sys, "frozen", False)


def app_dir():
    """Каталог, где живут пользовательские файлы: config.json, reports/."""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir():
    """Каталог с данными, вшитыми в сборку: db/, wsdl/."""
    if FROZEN:
        return getattr(sys, "_MEIPASS", app_dir())
    return os.path.dirname(os.path.abspath(__file__))


def config_path():
    return os.path.join(app_dir(), "config.json")


def db_dir():
    return os.path.join(data_dir(), "db", "devices")


def schema_path():
    return os.path.join(data_dir(), "db", "schema.json")


def reports_dir():
    return os.path.join(app_dir(), "reports")
