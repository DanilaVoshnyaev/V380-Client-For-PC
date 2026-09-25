"""
Сборка .exe для Windows — чтобы инструментом могли пользоваться люди без Python.

    python build.py

Получается папка dist/ с двумя программами:
    CameraClient.exe  — окно просмотра камеры
    CameraProbe.exe   — диагностика и опознавание моделей

VLC внутрь НЕ упаковывается: он остаётся внешней зависимостью. Так честнее
по лицензии (VLC под LGPL) и сборка не весит лишние сотни мегабайт.
Пользователю нужен установленный VLC той же разрядности, что и сборка.

    python build.py --onedir

То же самое, но папкой, упакованной в .zip. Скачивать дольше, зато Windows
Defender и SmartScreen ругаются на такую сборку заметно реже: одиночный exe
распаковывает себя во временный каталог, а это поведение похоже на упаковщик
вредоноса. В релиз имеет смысл класть оба варианта.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile

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


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build(script, name, windowed, extra_data, onefile=True):
    separator = ";" if sys.platform == "win32" else ":"
    command = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile" if onefile else "--onedir",
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


def pack_folder(dist, name):
    """Сборку папкой отдавать пользователю россыпью нельзя — пакуем в .zip."""
    folder = os.path.join(dist, name)
    if not os.path.isdir(folder):
        return None
    archive = os.path.join(dist, "%s.zip" % name)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(folder):
            for item in files:
                full = os.path.join(root, item)
                zf.write(full, os.path.relpath(full, dist))
    shutil.rmtree(folder)
    return archive


def is_personal(name):
    return (name == "config.json"
            or name == "reports"
            or name.endswith(".conf")
            or name.endswith("-qr.png")
            or name.endswith(".html"))


def remove_personal(folder):
    """Удаляет личные файлы, лежащие прямо в folder (без обхода вглубь)."""
    for name in sorted(os.listdir(folder)):
        if not is_personal(name):
            continue
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        print("Удалён личный файл из сборки: %s" % os.path.relpath(path, BASE_DIR))


def main():
    parser = argparse.ArgumentParser(description="Сборка .exe для Windows")
    parser.add_argument("--onedir", action="store_true",
                        help="собрать папкой и упаковать в .zip вместо одиночного exe")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    wsdl = wsdl_source()
    db = os.path.join(BASE_DIR, "db")
    dist = os.path.join(BASE_DIR, "dist")
    print("wsdl : %s" % wsdl)
    print("база : %s" % db)

    onefile = not args.onedir

    # Клиенту база не нужна — он работает по готовому config.json
    build("client.py", "CameraClient", windowed=True,
          extra_data=[(wsdl, "wsdl")], onefile=onefile)

    # Диагностике нужна база отпечатков
    build("probe.py", "CameraProbe", windowed=False,
          extra_data=[(wsdl, "wsdl"), (db, "db")], onefile=onefile)

    # Личные файлы в dist/ попадают случайно — например, если запускали
    # собранный exe прямо оттуда. В релиз они уходить не должны: config.json
    # содержит пароль от камеры, конфиги WireGuard — приватные ключи,
    # HTML-отчёт и reports/ — адреса и MAC реальных устройств.
    # Чистим до упаковки в .zip, иначе личное уедет внутри архива.
    remove_personal(dist)
    for name in ("CameraClient", "CameraProbe"):
        folder = os.path.join(dist, name)
        if os.path.isdir(folder):
            remove_personal(folder)

    if args.onedir:
        for name in ("CameraClient", "CameraProbe"):
            archive = pack_folder(dist, name)
            if archive:
                print("Упаковано: %s" % archive)

    # Пример конфигурации кладём рядом, чтобы было с чего начать
    example = os.path.join(BASE_DIR, "config.example.json")
    if os.path.isfile(example):
        shutil.copy(example, os.path.join(dist, "config.example.json"))

    # Двойной клик по .bat — обследование камер без единой команды в терминале
    launcher = os.path.join(BASE_DIR, "scan-cameras.bat")
    if os.path.isfile(launcher):
        shutil.copy(launcher, os.path.join(dist, "scan-cameras.bat"))

    print("\n=== Готово ===")
    sums = []
    for name in sorted(os.listdir(dist)):
        path = os.path.join(dist, name)
        if not os.path.isfile(path) or name == "SHA256SUMS.txt":
            continue
        size = os.path.getsize(path) / 1024 / 1024
        digest = sha256(path)
        sums.append("%s  %s" % (digest, name))
        print("  %-28s %6.1f МБ  %s" % (name, size, digest))

    # Контрольные суммы идут в описание релиза: сборка не подписана
    # сертификатом, и у скачавшего должен быть способ проверить файл.
    sums_path = os.path.join(dist, "SHA256SUMS.txt")
    with open(sums_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(sums) + "\n")

    print("\nПапка: %s" % dist)
    print("Контрольные суммы: %s" % sums_path)
    print("Напоминание: на компьютере пользователя должен быть установлен VLC.")
    print("Сборка не подписана — Windows покажет предупреждение SmartScreen.")
    print("Приложите SHA256 и ссылку на VirusTotal к описанию релиза.")


if __name__ == "__main__":
    main()
