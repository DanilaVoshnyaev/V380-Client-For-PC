"""
Диагностика IP-камеры: опознаёт модель, находит потоки и подсказывает,
как включить ONVIF, если он спрятан.

    python probe.py 192.168.1.100
    python probe.py 192.168.1.100 --user admin --password ПАРОЛЬ
    python probe.py 192.168.1.100 --write-config     # записать config.json
    python probe.py 192.168.1.100 --report           # заготовка записи для базы

Работает только с устройствами в вашей собственной сети.
"""
import argparse
import datetime
import json
import os
import sys

import camdb
import fingerprint

import paths

BASE_DIR = paths.app_dir()
TOOL_VERSION = "0.3.0"


def head(text):
    print("\n" + text)
    print("-" * max(len(text), 30))


def show_fingerprint(data):
    head("ОТПЕЧАТОК УСТРОЙСТВА")
    ports = ", ".join(str(p) for p in data["open_ports"]) or "нет открытых"
    print("  Адрес           : %s" % data["host"])
    print("  MAC             : %s" % (data["mac"] or "не определён"))
    print("  Открытые порты  : %s" % ports)
    if data["http_server"]:
        print("  HTTP Server     : %s" % data["http_server"])
    if data["rtsp_banner"]:
        print("  Баннер RTSP     : %s" % data["rtsp_banner"])

    onvif = data.get("onvif")
    if onvif:
        print("  ONVIF           : порт %s" % onvif["port"])
        print("    производитель : %s" % onvif["manufacturer"])
        print("    модель        : %s" % onvif["model"])
        print("    прошивка      : %s" % onvif["firmware"])
        print("    PTZ           : %s" % ("есть" if onvif["ptz"] else "нет"))
        for profile in onvif["profiles"]:
            print("    профиль %-12s %sx%s" % (
                profile["token"], profile["width"], profile["height"]))
    else:
        print("  ONVIF           : не отвечает")


def show_match(results):
    head("ОПОЗНАВАНИЕ ПО БАЗЕ")
    if not results:
        print("  Модель в базе не найдена.")
        print("  Помогите её пополнить: запустите с ключом --report,")
        print("  и приложите получившийся файл к pull request.")
        return None

    record, points, reasons = results[0]
    print("  %s" % record["display_name"])
    print("  Совпадение: %d очков (%s)" % (points, ", ".join(reasons)))
    if record.get("aliases"):
        print("  Также продаётся как: %s" % ", ".join(record["aliases"]))
    if record.get("cloud_app"):
        print("  Родное приложение  : %s" % record["cloud_app"])

    for other, other_points, _ in results[1:3]:
        print("  Похоже также на    : %s (%d)" % (other["display_name"], other_points))
    return record


def show_unlock(record, already_open=False):
    unlock = record.get("unlock", {})
    status = unlock.get("status")
    head("ДОСТУП К ПОТОКУ")

    if already_open:
        print("  Статус: всё уже включено, камера отдаёт поток.")
        if status == "hidden-onvif":
            print("  (у этой модели ONVIF выключен с завода — у вас он включён)")
        return

    print("  Статус: %s" % camdb.STATUS_TEXT.get(status, status))

    if status == "hidden-onvif":
        if unlock.get("app_path"):
            print("  Где включать: %s" % unlock["app_path"])
        print()
        for number, step in enumerate(unlock.get("steps", []), 1):
            print("  %d. %s" % (number, step))
        if unlock.get("requires_reboot"):
            print("\n  Обязательно перезагрузите камеру после сохранения.")
    elif status == "closed":
        print("  Стандартными средствами поток получить нельзя.")
        print("  Остаётся приложение вендора либо смена прошивки.")
    elif status == "firmware-mod":
        print("  Требуется смена прошивки. Риск вывести камеру из строя.")


def show_streams(data, record, host, verify):
    head("ПОТОКИ")
    streams = data.get("rtsp_streams") or []

    if not streams and record:
        print("  Сейчас недоступны. По базе у этой модели должны быть:")
        for item in camdb.stream_urls(record, host):
            print("    %s  %s" % (item["url"], item["resolution"]))
        return []

    if not streams:
        print("  Не найдено. Вероятно, RTSP выключен.")
        return []

    if len(streams) == 1 and streams[0].get("path") == "(любой путь)":
        print("  Сервер отдаёт поток на ЛЮБОЙ путь, включая несуществующий,")
        print("  поэтому перебором точный адрес не выяснить.")
        if record:
            print("  По базе у этой модели пути такие:")
            for item in camdb.stream_urls(record, host):
                print("    %s  %s" % (item["url"], item["resolution"]))
        else:
            print("  Точные пути даст ONVIF — включите его и повторите проверку.")
        return []

    working = []
    for stream in streams:
        mark = "нужен пароль" if stream["auth_required"] else "без пароля"
        codecs = ", ".join(stream["codecs"]) or "?"
        print("  %s" % stream["url"])
        print("      %s | кодеки: %s" % (mark, codecs))

        if verify and not stream["auth_required"]:
            total, error = fingerprint.verify_stream(host, 554, stream["path"])
            if error:
                print("      проверка: ошибка - %s" % error)
            elif total > 20000:
                print("      проверка: поток идёт, ~%d КБ/с" % (total / 5 / 1024))
                working.append(stream)
            else:
                print("      проверка: данные не поступают")
        else:
            working.append(stream)
    return working


def show_security(data, record):
    warnings = []
    streams = data.get("rtsp_streams") or []
    if any(s["status"] == 200 and not s["auth_required"] for s in streams):
        warnings.append(
            "RTSP отдаёт поток без авторизации - его может смотреть любой в вашей сети.")
    if record:
        security = record.get("security") or {}
        if security.get("notes"):
            warnings.append(security["notes"])
    if not warnings:
        return
    head("БЕЗОПАСНОСТЬ")
    for text in warnings:
        print("  * %s" % text)
    print("  * Не пробрасывайте порты камеры наружу. Для доступа из другой сети")
    print("    используйте VPN, например WireGuard на роутере.")


def write_config(record, data, host, user, password):
    if record:
        config = camdb.as_config(record, host, user, password)
    else:
        onvif = data.get("onvif") or {}
        config = {
            "host": host,
            "onvif_port": onvif.get("port", 8899),
            "rtsp_port": 554,
            "user": user,
            "password": password,
            "ptz_speed": 0.5,
            "save_dir": os.path.join(os.path.expanduser("~"), "Pictures", "Camera"),
            "network_caching_ms": 300,
            "use_tcp": True,
        }
    path = paths.config_path()
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(config, fh, indent=2, ensure_ascii=False)
    head("КОНФИГУРАЦИЯ")
    print("  Записан %s" % path)
    print("  Запускайте клиент: python client.py")


def make_report(data):
    """Заготовка записи для базы. Личные данные в неё не попадают."""
    onvif = data.get("onvif") or {}
    manufacturer = (onvif.get("manufacturer") or "unknown").lower()
    model = (onvif.get("model") or "device").lower()
    firmware = onvif.get("firmware") or "unknown"

    raw_id = "-".join((manufacturer, model, firmware))
    raw_id = raw_id.replace(" ", "-").replace("_", "-").lower()
    record_id = "".join(c for c in raw_id if c.isalnum() or c in "-.")
    while "--" in record_id:
        record_id = record_id.replace("--", "-")
    record_id = record_id.strip("-") or "unknown-device"

    streams = []
    for stream in data.get("rtsp_streams", []):
        if stream["status"] != 200:
            continue
        entry = {
            "path": stream["path"],
            "codec": stream["codecs"][0] if stream["codecs"] else "",
            "label": "",
        }
        for profile in onvif.get("profiles", []):
            if profile["uri"].endswith(stream["path"]):
                entry["resolution"] = "%dx%d" % (profile["width"], profile["height"])
        streams.append(entry)

    auth_required = any(s["auth_required"] for s in data.get("rtsp_streams", []))
    open_without_auth = any(
        s["status"] == 200 and not s["auth_required"]
        for s in data.get("rtsp_streams", [])
    )

    match = {
        "onvif_manufacturer": onvif.get("manufacturer"),
        "onvif_model": onvif.get("model"),
        "firmware": [firmware],
        "rtsp_banner": data.get("rtsp_banner"),
        "http_server": data.get("http_server"),
        "mac_prefixes": [data["mac"][:8]] if data.get("mac") else [],
        "ports": data.get("open_ports", []),
    }

    report = {
        "id": record_id,
        "display_name": "%s %s" % (onvif.get("manufacturer") or "?",
                                   onvif.get("model") or "?"),
        "aliases": ["ЗАПОЛНИТЕ: под каким брендом продаётся"],
        "cloud_app": "ЗАПОЛНИТЕ: название мобильного приложения вендора",
        "match": {key: value for key, value in match.items() if value},
        "unlock": {
            "status": "hidden-onvif",
            "app_path": "ЗАПОЛНИТЕ: где в приложении включается ONVIF",
            "requires_reboot": True,
            "steps": ["ЗАПОЛНИТЕ: по шагу на строку"],
        },
        "onvif": {
            "port": onvif.get("port", 8899),
            "service_path": "/onvif/device_service",
            "auth_required": True,
            "default_user": "admin",
        },
        "rtsp": {"port": 554, "auth_required": auth_required},
        "streams": streams,
        "features": {
            "ptz": onvif.get("ptz"),
            "zoom": None,
            "presets": None,
            "audio": None,
        },
        "security": {"rtsp_without_auth": open_without_auth},
        "verified": {
            "date": datetime.date.today().isoformat(),
            "by": "ЗАПОЛНИТЕ: ваш GitHub-ник",
            "tool_version": TOOL_VERSION,
        },
    }

    directory = paths.reports_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "%s.json" % record_id)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    head("ЗАГОТОВКА ДЛЯ БАЗЫ")
    print("  Файл: %s" % path)
    print("  В нём нет ни вашего IP, ни паролей - только признаки модели.")
    print()
    print("  Что дальше:")
    print("   1. Заполните поля, помеченные ЗАПОЛНИТЕ.")
    print("   2. Перенесите файл в db/devices/")
    print("   3. Проверьте: python tools/validate_db.py")
    print("   4. Отправьте pull request. Подробности в CONTRIBUTING.md")


def run_scan(cidr, user, password, verify):
    """Обход подсети: находит все камеры и сводит их в таблицу."""
    head("СКАНИРОВАНИЕ ПОДСЕТИ %s" % cidr)

    state = {"last": -1}

    def progress(done, total):
        percent = done * 100 // total
        if percent != state["last"] and percent % 10 == 0:
            state["last"] = percent
            print("  проверено %d%% (%d из %d)" % (percent, done, total))

    candidates = fingerprint.scan_subnet(cidr, progress=progress)
    print("\n  Устройств с открытыми портами камер: %d" % len(candidates))
    if not candidates:
        print("  Ничего не найдено. Проверьте, та ли это подсеть.")
        return 1

    # WS-Discovery заранее говорит, у кого есть ONVIF и на каком порту.
    # Без этого пришлось бы вслепую долбиться в каждый открытый HTTP-порт,
    # а неудачный ONVIF-запрос стоит несколько секунд.
    print("  Спрашиваю, кто отзывается по ONVIF …")
    onvif_by_host = {}
    for address in fingerprint.discover_onvif(timeout=4) or []:
        try:
            hostport = address.split("://", 1)[1].split("/", 1)[0]
            host, _, port = hostport.partition(":")
            onvif_by_host[host] = int(port) if port else 80
        except (IndexError, ValueError):
            continue
    if onvif_by_host:
        print("  Ответили: %s" % ", ".join(sorted(onvif_by_host)))

    records = camdb.load_db()

    def inspect(item):
        host = item["host"]
        if host in onvif_by_host:
            onvif_ports = [onvif_by_host[host]]
        elif 8899 in item["open_ports"]:
            onvif_ports = [8899]
        else:
            onvif_ports = []  # молчит по discovery и нет типового порта — не тратим время
        data = fingerprint.build(
            host, user, password, deep=False,
            known_ports=item["open_ports"], onvif_ports=onvif_ports)
        data["protocol"] = item.get("protocol")
        return host, data

    print("  Опрашиваю найденные устройства …")
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        inspected = list(pool.map(inspect, candidates))

    rows = []
    for host, data in inspected:
        matches = camdb.identify(data, records)

        record = matches[0][0] if matches else None
        streams = [s for s in data.get("rtsp_streams", []) if s["status"] == 200]

        onvif = data.get("onvif") or {}
        # Камерой считаем только то, что говорит по RTSP или отвечает по ONVIF.
        # Устройство с одним лишь HTTP — обычно роутер, принтер или NAS.
        is_camera = bool(streams or onvif or data.get("protocol") == "rtsp")

        if streams:
            status = "поток доступен"
        elif record and record.get("unlock", {}).get("status") == "hidden-onvif":
            status = "нужно включить ONVIF"
        elif onvif:
            status = "ONVIF есть, потока нет"
        elif is_camera:
            status = "закрыта"
        else:
            status = "не похоже на камеру"
        rows.append({
            "host": host,
            "mac": data.get("mac") or "?",
            "model": (record["display_name"] if record
                      else (onvif.get("model") or "неизвестно")),
            "firmware": onvif.get("firmware") or "?",
            "status": status,
            "stream": streams[0]["url"] if streams else "",
            "known": bool(record),
            "is_camera": is_camera,
        })

    head("НАЙДЕННЫЕ КАМЕРЫ")
    print("  %-15s %-18s %-34s %-22s" % ("АДРЕС", "MAC", "МОДЕЛЬ", "СОСТОЯНИЕ"))
    for row in rows:
        model = row["model"]
        if len(model) > 33:
            model = model[:30] + "..."
        print("  %-15s %-18s %-34s %-22s" % (row["host"], row["mac"], model, row["status"]))

    ready = [r for r in rows if r["stream"]]
    if ready:
        head("ГОТОВЫЕ АДРЕСА ПОТОКОВ")
        for row in ready:
            print("  %s" % row["stream"])

    unknown = [r for r in rows if not r["known"] and r["is_camera"]]
    if unknown:
        head("НЕТ В БАЗЕ")
        for row in unknown:
            print("  %s (%s, прошивка %s)" % (row["host"], row["model"], row["firmware"]))
        print("\n  Помогите пополнить базу:")
        print("  python probe.py %s --report" % unknown[0]["host"])

    cameras = [r for r in rows if r["is_camera"]]
    head("ИТОГ")
    print("  Камер: %d, из них с доступным потоком: %d" % (len(cameras), len(ready)))
    others = len(rows) - len(cameras)
    if others:
        print("  Прочих устройств (не камеры): %d" % others)
    return 0 if ready else 1


def main():
    parser = argparse.ArgumentParser(
        description="Диагностика IP-камеры: опознавание модели и поиск потоков")
    parser.add_argument("host", nargs="?",
                        help="IP камеры в вашей локальной сети")
    parser.add_argument("--scan", metavar="CIDR",
                        help="обойти подсеть целиком, например 192.168.1.0/24")
    parser.add_argument("--user", default="admin", help="логин ONVIF")
    parser.add_argument("--password", default="", help="пароль ONVIF")
    parser.add_argument("--write-config", action="store_true",
                        help="записать config.json для клиента")
    parser.add_argument("--report", action="store_true",
                        help="создать заготовку записи для базы устройств")
    parser.add_argument("--no-verify", action="store_true",
                        help="не проверять потоки приёмом данных")
    parser.add_argument("--discover", action="store_true",
                        help="найти ONVIF-камеры в сети перед сканированием")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if not args.host and not args.scan:
        parser.error("укажите IP камеры либо подсеть через --scan")

    if args.scan:
        return run_scan(args.scan, args.user, args.password, not args.no_verify)

    if args.discover:
        head("ПОИСК ONVIF-УСТРОЙСТВ В СЕТИ")
        addresses = fingerprint.discover_onvif()
        for address in addresses or []:
            print("  %s" % address)
        if not addresses:
            print("  никто не ответил")

    print("\nСканирую %s ..." % args.host)
    data = fingerprint.build(args.host, args.user, args.password)

    show_fingerprint(data)
    record = show_match(camdb.identify(data))

    already_open = any(
        stream["status"] == 200 for stream in data.get("rtsp_streams", []))
    if record:
        show_unlock(record, already_open)
    working = show_streams(data, record, args.host, not args.no_verify)
    show_security(data, record)

    if args.write_config:
        write_config(record, data, args.host, args.user, args.password)
    if args.report:
        make_report(data)

    head("ИТОГ")
    if working:
        print("  Камера пригодна для сторонних клиентов.")
        print("  Рабочий адрес: %s" % working[0]["url"])
        if not args.write_config:
            print("  Записать настройки: python probe.py %s --write-config" % args.host)
        return 0
    if record and record.get("unlock", {}).get("status") == "hidden-onvif":
        print("  Поток закрыт, но у этой модели ONVIF включается в приложении.")
        print("  Выполните шаги выше, перезагрузите камеру и запустите проверку снова.")
        return 1
    print("  Стандартный поток получить не удалось.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
