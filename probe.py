"""
Диагностика IP-камеры: опознаёт модель, находит потоки и подсказывает,
как включить ONVIF, если он спрятан.

    python probe.py 192.168.1.100
    python probe.py 192.168.1.100 --user admin --password ПАРОЛЬ
    python probe.py 192.168.1.100 --write-config     # записать config.json
    python probe.py 192.168.1.100 --report           # заготовка записи для базы

Язык вывода определяется по системе, принудительно — ключом --lang en|ru.

Работает только с устройствами в вашей собственной сети.
"""
import argparse
import datetime
import json
import os
import sys

import camdb
import fingerprint
import i18n
import report
from i18n import t

import paths

BASE_DIR = paths.app_dir()
TOOL_VERSION = "0.3.0"


def head(text):
    print("\n" + text)
    print("-" * max(len(text), 30))


def line(text, indent=2):
    print(" " * indent + text)


def field(label, value, indent=2):
    """Поле отпечатка. Двоеточия выстраиваются в столбик на любом языке."""
    print("%s%-*s: %s" % (" " * indent, i18n.COLUMN - indent, label, value))


def show_fingerprint(data):
    head(t("hdr_fingerprint"))
    ports = ", ".join(str(p) for p in data["open_ports"]) or t("val_no_open_ports")
    field(t("lbl_address"), data["host"])
    field(t("lbl_mac"), data["mac"] or t("val_mac_unknown"))
    field(t("lbl_open_ports"), ports)
    if data["http_server"]:
        field(t("lbl_http_server"), data["http_server"])
    if data["rtsp_banner"]:
        field(t("lbl_rtsp_banner"), data["rtsp_banner"])

    onvif = data.get("onvif")
    if onvif:
        field(t("lbl_onvif"), t("val_onvif_port", onvif["port"]))
        field(t("lbl_manufacturer"), onvif["manufacturer"], indent=4)
        field(t("lbl_model"), onvif["model"], indent=4)
        field(t("lbl_firmware"), onvif["firmware"], indent=4)
        field(t("lbl_ptz"), t("val_yes") if onvif["ptz"] else t("val_no"), indent=4)
        for profile in onvif["profiles"]:
            line(t("val_profile", profile["token"],
                   profile["width"], profile["height"]), indent=4)
    else:
        field(t("lbl_onvif"), t("val_onvif_silent"))


def show_match(results):
    head(t("hdr_match"))
    if not results:
        line(t("match_none_1"))
        line(t("match_none_2"))
        line(t("match_none_3"))
        return None

    record, points, reasons = results[0]
    line(record["display_name"])
    line(t("match_score", points, ", ".join(reasons)))
    if record.get("aliases"):
        line(t("match_aliases", ", ".join(record["aliases"])))
    if record.get("cloud_app"):
        line(t("match_cloud_app", record["cloud_app"]))

    for other, other_points, _ in results[1:3]:
        line(t("match_also_like", other["display_name"], other_points))
    return record


def show_unlock(record, already_open=False):
    unlock = record.get("unlock", {})
    status = unlock.get("status")
    head(t("hdr_access"))

    if already_open:
        line(t("unlock_all_open"))
        if status == "hidden-onvif":
            line(t("unlock_hidden_note"))
        return

    line(t("unlock_status", camdb.status_text(status)))

    if status == "hidden-onvif":
        if unlock.get("app_path"):
            line(t("unlock_where", unlock["app_path"]))
        print()
        for number, step in enumerate(unlock.get("steps", []), 1):
            line("%d. %s" % (number, step))
        if unlock.get("requires_reboot"):
            print()
            line(t("unlock_reboot"))
    elif status == "closed":
        line(t("unlock_closed_1"))
        line(t("unlock_closed_2"))
    elif status == "firmware-mod":
        line(t("unlock_firmware"))


def show_streams(data, record, host, verify):
    head(t("hdr_streams"))
    streams = data.get("rtsp_streams") or []

    if not streams and record:
        line(t("streams_db_expected"))
        for item in camdb.stream_urls(record, host):
            line("%s  %s" % (item["url"], item["resolution"]), indent=4)
        return []

    if not streams:
        line(t("streams_none"))
        return []

    # Прошивка отдаёт поток на любой путь — перебором адрес не выяснить
    if len(streams) == 1 and streams[0].get("catch_all_only"):
        line(t("streams_catch_all_1"))
        line(t("streams_catch_all_2"))
        if record:
            line(t("streams_db_paths"))
            for item in camdb.stream_urls(record, host):
                line("%s  %s" % (item["url"], item["resolution"]), indent=4)
        else:
            line(t("streams_onvif_hint"))
        return []

    working = []
    for stream in streams:
        mark = t("stream_auth_required") if stream["auth_required"] else t("stream_no_auth")
        codecs = ", ".join(stream["codecs"]) or "?"
        line(stream["url"])
        line(t("stream_detail", mark, codecs), indent=6)

        if verify and not stream["auth_required"]:
            total, error = fingerprint.verify_stream(host, 554, stream["path"])
            if error:
                line(t("verify_error", error), indent=6)
            elif total > 20000:
                line(t("verify_ok", total / fingerprint.VERIFY_SECONDS / 1024), indent=6)
                working.append(stream)
            else:
                line(t("verify_nodata"), indent=6)
        else:
            working.append(stream)
    return working


def show_security(data, record):
    warnings = []
    streams = data.get("rtsp_streams") or []
    if any(s["status"] == 200 and not s["auth_required"] for s in streams):
        warnings.append(t("sec_no_auth"))
    if record:
        security = record.get("security") or {}
        if security.get("notes"):
            warnings.append(security["notes"])
    if not warnings:
        return
    head(t("hdr_security"))
    for text in warnings:
        line("* %s" % text)
    line("* " + t("sec_no_forward_1"))
    line(t("sec_no_forward_2"), indent=4)


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
    head(t("hdr_config"))
    line(t("cfg_written", path))
    line(t("cfg_run_client"))


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

    fill = t("fill_in")
    report = {
        "id": record_id,
        "display_name": "%s %s" % (onvif.get("manufacturer") or "?",
                                   onvif.get("model") or "?"),
        "aliases": [t("ph_aliases", fill)],
        "cloud_app": t("ph_cloud_app", fill),
        "match": {key: value for key, value in match.items() if value},
        "unlock": {
            "status": "hidden-onvif",
            "app_path": t("ph_app_path", fill),
            "requires_reboot": True,
            "steps": [t("ph_steps", fill)],
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
            "by": t("ph_by", fill),
            "tool_version": TOOL_VERSION,
        },
    }

    directory = paths.reports_dir()
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "%s.json" % record_id)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    head(t("hdr_report"))
    line(t("rpt_file", path))
    line(t("rpt_no_personal"))
    print()
    line(t("rpt_next"))
    line(t("rpt_step1", fill))
    line(t("rpt_step2"))
    line(t("rpt_step3"))
    line(t("rpt_step4"))


def run_scan(cidr, user, password, report_path=None):
    """Обход подсети: находит все камеры и сводит их в таблицу."""
    head(t("hdr_scan", cidr))

    state = {"last": -1}

    def progress(done, total):
        percent = done * 100 // total
        if percent != state["last"] and percent % 10 == 0:
            state["last"] = percent
            line(t("scan_progress", percent, done, total))

    candidates = fingerprint.scan_subnet(cidr, progress=progress)
    print()
    line(t("scan_devices_found", len(candidates)))
    if not candidates:
        line(t("scan_nothing"))
        return 1

    # WS-Discovery заранее говорит, у кого есть ONVIF и на каком порту.
    # Без этого пришлось бы вслепую долбиться в каждый открытый HTTP-порт,
    # а неудачный ONVIF-запрос стоит несколько секунд.
    line(t("scan_asking_onvif"))
    onvif_by_host = {}
    for address in fingerprint.discover_onvif(timeout=4) or []:
        try:
            hostport = address.split("://", 1)[1].split("/", 1)[0]
            host, _, port = hostport.partition(":")
            onvif_by_host[host] = int(port) if port else 80
        except (IndexError, ValueError):
            continue
    if onvif_by_host:
        line(t("scan_answered", ", ".join(sorted(onvif_by_host))))

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

    line(t("scan_querying"))
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

        # status — текст для человека, action — машинный ключ для отчёта.
        # Разделено, чтобы вёрстка отчёта не зависела от языка вывода.
        if streams:
            status, action = t("st_stream_available"), "ready"
        elif record and record.get("unlock", {}).get("status") == "hidden-onvif":
            status, action = t("st_enable_onvif"), "enable"
        elif onvif:
            status, action = t("st_onvif_no_stream"), "setup"
        elif is_camera:
            status, action = t("st_closed"), "locked"
        else:
            status, action = t("st_not_camera"), None
        rows.append({
            "action": action,
            "no_auth": any(not s["auth_required"] for s in streams),
            "host": host,
            "mac": data.get("mac") or "?",
            "model": (record["display_name"] if record
                      else (onvif.get("model") or t("val_unknown_model"))),
            "firmware": onvif.get("firmware") or "?",
            "status": status,
            "stream": streams[0]["url"] if streams else "",
            "known": bool(record),
            "is_camera": is_camera,
        })

    head(t("hdr_found"))
    print("  %-15s %-18s %-34s %-22s" % (t("col_address"), t("col_mac"),
                                         t("col_model"), t("col_state")))
    for row in rows:
        model = row["model"]
        if len(model) > 33:
            model = model[:30] + "..."
        print("  %-15s %-18s %-34s %-22s" % (row["host"], row["mac"], model, row["status"]))

    ready = [r for r in rows if r["stream"]]
    if ready:
        head(t("hdr_ready"))
        for row in ready:
            line(row["stream"])

    unknown = [r for r in rows if not r["known"] and r["is_camera"]]
    if unknown:
        head(t("hdr_unknown"))
        for row in unknown:
            line(t("unknown_row", row["host"], row["model"], row["firmware"]))
        print()
        line(t("unknown_help"))
        line("python probe.py %s --report" % unknown[0]["host"])

    cameras = [r for r in rows if r["is_camera"]]
    head(t("hdr_summary"))
    line(t("sum_cameras", len(cameras), len(ready)))
    others = len(rows) - len(cameras)
    if others:
        line(t("sum_others", others))

    if report_path:
        report.save(report_path, rows, cidr, "CameraProbe", TOOL_VERSION)
        line(t("rh_saved", report_path))
    return 0 if ready else 1


def pick_language(argv):
    """
    Язык нужен до сборки парсера: иначе --help выйдет не на том языке.
    Поэтому --lang вычитываем из argv вручную, до argparse.
    """
    value = None
    for index, item in enumerate(argv):
        if item == "--lang" and index + 1 < len(argv):
            value = argv[index + 1]
        elif item.startswith("--lang="):
            value = item.split("=", 1)[1]
    return i18n.set_language(value)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    pick_language(sys.argv[1:])

    parser = argparse.ArgumentParser(description=t("arg_description"))
    parser.add_argument("host", nargs="?", help=t("arg_host"))
    parser.add_argument("--scan", metavar="CIDR", help=t("arg_scan"))
    parser.add_argument("--user", default="admin", help=t("arg_user"))
    parser.add_argument("--password", default="", help=t("arg_password"))
    parser.add_argument("--write-config", action="store_true",
                        help=t("arg_write_config"))
    parser.add_argument("--report", action="store_true", help=t("arg_report"))
    parser.add_argument("--no-verify", action="store_true", help=t("arg_no_verify"))
    parser.add_argument("--discover", action="store_true", help=t("arg_discover"))
    parser.add_argument("--report-html", metavar="FILE", help=t("arg_report_html"))
    parser.add_argument("--lang", choices=("en", "ru", "auto"), default="auto",
                        help=t("arg_lang"))
    args = parser.parse_args()

    if not args.host and not args.scan:
        parser.error(t("err_need_target"))

    if args.scan:
        return run_scan(args.scan, args.user, args.password, args.report_html)

    if args.discover:
        head(t("hdr_discover"))
        addresses = fingerprint.discover_onvif()
        for address in addresses or []:
            line(address)
        if not addresses:
            line(t("discover_none"))

    print("\n" + t("scanning", args.host))
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

    head(t("hdr_summary"))
    if working:
        line(t("final_usable"))
        line(t("final_url", working[0]["url"]))
        if not args.write_config:
            line(t("final_write_cfg", args.host))
        return 0
    if record and record.get("unlock", {}).get("status") == "hidden-onvif":
        line(t("final_hidden_1"))
        line(t("final_hidden_2"))
        return 1
    line(t("final_failed"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
