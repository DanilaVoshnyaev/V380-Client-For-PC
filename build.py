"""
Сборка .exe для Windows — чтобы инструментом могли пользоваться люди без Python.

    python build.py

Получается папка dist/ с двумя программами:
    CameraClient.exe  — окно просмотра камеры
    CameraProbe.exe   — диагностика и опознавание моделей

VLC внутрь НЕ упаковывается: он остаётся внешней зависимостью. Так честнее
по лицензии (VLC под LGPL) и сборка не весит лишние сотни мегабайт.
Пользователю нужен установленный VLC той же разрядности, что и сборка.
"""
import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def wsdl_source():
    """Каталог wsdl библиотеки onvif-zeep — его нужно вшить в сборку."""
    import sysconfig

    import onvif

    pkg = os.path.dirname(onvif.__file__)
    for path in (
        os.path.join(pkg, "wsdl"),
        os.path.join(os.path.dirname(pkg), "wsdl"),
        os.path.join(sysconfig.get_paths()["purelib"], "wsdl"),
    ):
        if os.path.isfile(os.path.join(path, "devicemgmt.wsdl")):
            return path
    raise SystemExit("Не найден каталог wsdl. Установите: pip install onvif-zeep")


def build(script, name, windowed, extra_data):
    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--name", name,
        "--distpath", os.path.join(BASE_DIR, "dist"),
        "--workpath", os.path.join(BASE_DIR, "build"),
        "--specpath", os.path.join(BASE_DIR, "build"),
        # zeep тянет схемы и плагины через динамический импорт,
        # PyInstaller их сам не находит
        "--collect-data", "zeep",
        "--hidden-import", "zeep.transports",
        "--hidden-import", "wsdiscovery",
    ]
    if windowed:
        command.append("--windowed")
    for source, target in extra_data:
        command += ["--add-data", "%s%s%s" % (source, separator, target)]
    command.append(os.path.join(BASE_DIR, script))

    print("\n=== Собираю %s ===" % name)
    result = subprocess.run(command)
    if result.returncode != 0:
        raise SystemExit("Сборка %s не удалась" % name)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    wsdl = wsdl_source()
    db = os.path.join(BASE_DIR, "db")
    print("wsdl : %s" % wsdl)
    print("база : %s" % db)

    # Клиенту база не нужна — он работает по готовому config.json
    build("client.py", "CameraClient", windowed=True,
          extra_data=[(wsdl, "wsdl")])

    # Диагностике нужна база отпечатков
    build("probe.py", "CameraProbe", windowed=False,
          extra_data=[(wsdl, "wsdl"), (db, "db")])

    # Пример конфигурации кладём рядом, чтобы было с чего начать
    example = os.path.join(BASE_DIR, "config.example.json")
    if os.path.isfile(example):
        shutil.copy(example, os.path.join(BASE_DIR, "dist", "config.example.json"))

    print("\n=== Готово ===")
    dist = os.path.join(BASE_DIR, "dist")
    for name in sorted(os.listdir(dist)):
        path = os.path.join(dist, name)
        size = os.path.getsize(path) / 1024 / 1024
        print("  %-28s %6.1f МБ" % (name, size))
    print("\nПапка: %s" % dist)
    print("Напоминание: на компьютере пользователя должен быть установлен VLC.")


if __name__ == "__main__":
    main()
