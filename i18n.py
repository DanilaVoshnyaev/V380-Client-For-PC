"""
Язык вывода инструментов: английский и русский.

Переводится только то, что читает пользователь. Комментарии и докстринги
в коде остаются русскими — это язык разработки проекта, и смешивать его
с языком интерфейса не нужно.

Язык выбирается сам по системным настройкам; принудительно — ключом --lang.
"""
import os
import sys

DEFAULT = "en"
AVAILABLE = ("en", "ru")

_language = DEFAULT


def detect():
    """Язык системы. Вернёт 'ru' только если пользователь и правда русский."""
    if sys.platform == "win32":
        try:
            import ctypes

            # Младший байт LANGID — основной язык. 0x19 это LANG_RUSSIAN.
            lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
            return "ru" if (lang_id & 0xFF) == 0x19 else "en"
        except Exception:
            pass

    for name in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(name, "")
        if value:
            return "ru" if value.lower().startswith("ru") else "en"
    return DEFAULT


def set_language(name=None):
    """Ставит язык. None или 'auto' — определить по системе."""
    global _language
    if not name or name == "auto":
        _language = detect()
    elif name in AVAILABLE:
        _language = name
    else:
        _language = DEFAULT
    return _language


def language():
    return _language


def t(key, *args):
    """Строка по ключу. Недостающий перевод берётся из английского."""
    text = MESSAGES.get(_language, {}).get(key)
    if text is None:
        text = MESSAGES[DEFAULT].get(key, key)
    return text % args if args else text


# Ширина колонки с названием поля — чтобы двоеточия выстроились в столбик
# независимо от языка и от уровня вложенности.
COLUMN = 18


MESSAGES = {
    "en": {
        # --- заголовки разделов ---
        "hdr_fingerprint": "DEVICE FINGERPRINT",
        "hdr_match": "DATABASE MATCH",
        "hdr_access": "STREAM ACCESS",
        "hdr_streams": "STREAMS",
        "hdr_security": "SECURITY",
        "hdr_config": "CONFIGURATION",
        "hdr_report": "DATABASE ENTRY DRAFT",
        "hdr_scan": "SCANNING SUBNET %s",
        "hdr_found": "CAMERAS FOUND",
        "hdr_ready": "WORKING STREAM URLS",
        "hdr_unknown": "NOT IN THE DATABASE",
        "hdr_summary": "SUMMARY",
        "hdr_discover": "LOOKING FOR ONVIF DEVICES",

        # --- отпечаток ---
        "lbl_address": "Address",
        "lbl_mac": "MAC",
        "lbl_open_ports": "Open ports",
        "lbl_http_server": "HTTP Server",
        "lbl_rtsp_banner": "RTSP banner",
        "lbl_onvif": "ONVIF",
        "lbl_manufacturer": "manufacturer",
        "lbl_model": "model",
        "lbl_firmware": "firmware",
        "lbl_ptz": "PTZ",
        "val_no_open_ports": "none open",
        "val_mac_unknown": "not detected",
        "val_yes": "yes",
        "val_no": "no",
        "val_onvif_port": "port %s",
        "val_onvif_silent": "not responding",
        "val_profile": "profile %-12s %sx%s",

        # --- опознавание ---
        "match_none_1": "Model not found in the database.",
        "match_none_2": "Help fill it in: run with --report,",
        "match_none_3": "then attach the resulting file to a pull request.",
        "match_score": "Match: %d points (%s)",
        "match_aliases": "Also sold as     : %s",
        "match_cloud_app": "Vendor app       : %s",
        "match_also_like": "Also resembles   : %s (%d)",

        # --- доступ к потоку ---
        "unlock_all_open": "Status: everything is already on, the camera is serving video.",
        "unlock_hidden_note": "(on this model ONVIF is off from the factory — yours is on)",
        "unlock_status": "Status: %s",
        "unlock_where": "Where to enable it: %s",
        "unlock_reboot": "Reboot the camera after saving. This is not optional.",
        "unlock_closed_1": "There is no standard way to get a stream out of this one.",
        "unlock_closed_2": "That leaves the vendor app or a firmware change.",
        "unlock_firmware": "Requires a firmware change. Risk of bricking the camera.",

        "status_open": "works out of the box, nothing to enable",
        "status_hidden-onvif": "ONVIF is there but switched off — enable it in the vendor app",
        "status_closed": "no standard way to get a stream",
        "status_firmware-mod": "only by changing the firmware",

        # --- потоки ---
        "streams_db_expected": "Not reachable right now. The database says this model should have:",
        "streams_none": "None found. RTSP is probably disabled.",
        "path_any": "(any path)",
        "streams_catch_all_1": "The server serves a stream on ANY path, including nonexistent ones,",
        "streams_catch_all_2": "so brute-forcing will not reveal the real address.",
        "streams_db_paths": "The database says the paths for this model are:",
        "streams_onvif_hint": "ONVIF would give the exact paths — enable it and check again.",
        "stream_auth_required": "password required",
        "stream_no_auth": "no password",
        "stream_detail": "%s | codecs: %s",
        "verify_error": "check: error - %s",
        "verify_ok": "check: stream is flowing, ~%d KB/s",
        "verify_nodata": "check: no data arriving",

        # --- безопасность ---
        "sec_no_auth": "RTSP serves video with no authentication - anyone on your network can watch it.",
        "sec_no_forward_1": "Do not forward the camera's ports to the internet. To reach it from",
        "sec_no_forward_2": "another network use a VPN, for example WireGuard on your router.",

        # --- конфигурация ---
        "cfg_written": "Wrote %s",
        "cfg_run_client": "Run the client: python client.py",

        # --- заготовка для базы ---
        "fill_in": "FILL IN",
        "ph_aliases": "%s: which brands it is sold under",
        "ph_cloud_app": "%s: name of the vendor's mobile app",
        "ph_app_path": "%s: where in the app ONVIF is enabled",
        "ph_steps": "%s: one step per line",
        "ph_by": "%s: your GitHub handle",
        "rpt_file": "File: %s",
        "rpt_no_personal": "It holds no IP address of yours and no passwords - only model traits.",
        "rpt_next": "What to do next:",
        "rpt_step1": " 1. Fill in the fields marked %s.",
        "rpt_step2": " 2. Move the file into db/devices/",
        "rpt_step3": " 3. Check it: python tools/validate_db.py",
        "rpt_step4": " 4. Open a pull request. Details in CONTRIBUTING.md",

        # --- сканирование подсети ---
        "scan_progress": "checked %d%% (%d of %d)",
        "scan_devices_found": "Devices with camera ports open: %d",
        "scan_nothing": "Nothing found. Check that this is the right subnet.",
        "scan_asking_onvif": "Asking who answers over ONVIF …",
        "scan_answered": "Answered: %s",
        "scan_querying": "Querying the devices found …",
        "st_stream_available": "stream available",
        "st_enable_onvif": "needs ONVIF enabled",
        "st_onvif_no_stream": "ONVIF yes, stream no",
        "st_closed": "locked",
        "st_not_camera": "not a camera",
        "val_unknown_model": "unknown",
        "col_address": "ADDRESS",
        "col_mac": "MAC",
        "col_model": "MODEL",
        "col_state": "STATE",
        "unknown_row": "%s (%s, firmware %s)",
        "unknown_help": "Help grow the database:",
        "sum_cameras": "Cameras: %d, of them with a reachable stream: %d",
        "sum_others": "Other devices (not cameras): %d",
        "discover_none": "nobody answered",

        # --- итог одиночной проверки ---
        "scanning": "Scanning %s ...",
        "final_usable": "This camera works with third-party clients.",
        "final_url": "Working URL: %s",
        "final_write_cfg": "Save the settings: python probe.py %s --write-config",
        "final_hidden_1": "No stream yet, but on this model ONVIF is enabled from the app.",
        "final_hidden_2": "Follow the steps above, reboot the camera and run this again.",
        "final_failed": "Could not get a standard stream.",

        # --- аргументы командной строки ---
        "arg_description": "IP camera diagnostics: identify the model and find its streams",
        "arg_host": "camera IP on your own local network",
        "arg_scan": "sweep a whole subnet, for example 192.168.1.0/24",
        "arg_user": "ONVIF username",
        "arg_password": "ONVIF password",
        "arg_write_config": "write config.json for the client",
        "arg_report": "create a database entry draft for this device",
        "arg_no_verify": "do not verify streams by receiving data (single camera check)",
        "arg_discover": "look for ONVIF cameras before scanning",
        "arg_lang": "output language: en, ru or auto (default: auto)",
        "err_need_target": "give a camera IP, or a subnet via --scan",
    },

    "ru": {
        "hdr_fingerprint": "ОТПЕЧАТОК УСТРОЙСТВА",
        "hdr_match": "ОПОЗНАВАНИЕ ПО БАЗЕ",
        "hdr_access": "ДОСТУП К ПОТОКУ",
        "hdr_streams": "ПОТОКИ",
        "hdr_security": "БЕЗОПАСНОСТЬ",
        "hdr_config": "КОНФИГУРАЦИЯ",
        "hdr_report": "ЗАГОТОВКА ДЛЯ БАЗЫ",
        "hdr_scan": "СКАНИРОВАНИЕ ПОДСЕТИ %s",
        "hdr_found": "НАЙДЕННЫЕ КАМЕРЫ",
        "hdr_ready": "ГОТОВЫЕ АДРЕСА ПОТОКОВ",
        "hdr_unknown": "НЕТ В БАЗЕ",
        "hdr_summary": "ИТОГ",
        "hdr_discover": "ПОИСК ONVIF-УСТРОЙСТВ В СЕТИ",

        "lbl_address": "Адрес",
        "lbl_mac": "MAC",
        "lbl_open_ports": "Открытые порты",
        "lbl_http_server": "HTTP Server",
        "lbl_rtsp_banner": "Баннер RTSP",
        "lbl_onvif": "ONVIF",
        "lbl_manufacturer": "производитель",
        "lbl_model": "модель",
        "lbl_firmware": "прошивка",
        "lbl_ptz": "PTZ",
        "val_no_open_ports": "нет открытых",
        "val_mac_unknown": "не определён",
        "val_yes": "есть",
        "val_no": "нет",
        "val_onvif_port": "порт %s",
        "val_onvif_silent": "не отвечает",
        "val_profile": "профиль %-12s %sx%s",

        "match_none_1": "Модель в базе не найдена.",
        "match_none_2": "Помогите её пополнить: запустите с ключом --report,",
        "match_none_3": "и приложите получившийся файл к pull request.",
        "match_score": "Совпадение: %d очков (%s)",
        "match_aliases": "Также продаётся как: %s",
        "match_cloud_app": "Родное приложение  : %s",
        "match_also_like": "Похоже также на    : %s (%d)",

        "unlock_all_open": "Статус: всё уже включено, камера отдаёт поток.",
        "unlock_hidden_note": "(у этой модели ONVIF выключен с завода — у вас он включён)",
        "unlock_status": "Статус: %s",
        "unlock_where": "Где включать: %s",
        "unlock_reboot": "Обязательно перезагрузите камеру после сохранения.",
        "unlock_closed_1": "Стандартными средствами поток получить нельзя.",
        "unlock_closed_2": "Остаётся приложение вендора либо смена прошивки.",
        "unlock_firmware": "Требуется смена прошивки. Риск вывести камеру из строя.",

        "status_open": "работает сразу, ничего включать не нужно",
        "status_hidden-onvif": "ONVIF есть, но выключен — включается в приложении вендора",
        "status_closed": "штатных способов получить поток нет",
        "status_firmware-mod": "только сменой прошивки",

        "streams_db_expected": "Сейчас недоступны. По базе у этой модели должны быть:",
        "streams_none": "Не найдено. Вероятно, RTSP выключен.",
        "path_any": "(любой путь)",
        "streams_catch_all_1": "Сервер отдаёт поток на ЛЮБОЙ путь, включая несуществующий,",
        "streams_catch_all_2": "поэтому перебором точный адрес не выяснить.",
        "streams_db_paths": "По базе у этой модели пути такие:",
        "streams_onvif_hint": "Точные пути даст ONVIF — включите его и повторите проверку.",
        "stream_auth_required": "нужен пароль",
        "stream_no_auth": "без пароля",
        "stream_detail": "%s | кодеки: %s",
        "verify_error": "проверка: ошибка - %s",
        "verify_ok": "проверка: поток идёт, ~%d КБ/с",
        "verify_nodata": "проверка: данные не поступают",

        "sec_no_auth": "RTSP отдаёт поток без авторизации - его может смотреть любой в вашей сети.",
        "sec_no_forward_1": "Не пробрасывайте порты камеры наружу. Для доступа из другой сети",
        "sec_no_forward_2": "используйте VPN, например WireGuard на роутере.",

        "cfg_written": "Записан %s",
        "cfg_run_client": "Запускайте клиент: python client.py",

        "fill_in": "ЗАПОЛНИТЕ",
        "ph_aliases": "%s: под каким брендом продаётся",
        "ph_cloud_app": "%s: название мобильного приложения вендора",
        "ph_app_path": "%s: где в приложении включается ONVIF",
        "ph_steps": "%s: по шагу на строку",
        "ph_by": "%s: ваш GitHub-ник",
        "rpt_file": "Файл: %s",
        "rpt_no_personal": "В нём нет ни вашего IP, ни паролей - только признаки модели.",
        "rpt_next": "Что дальше:",
        "rpt_step1": " 1. Заполните поля, помеченные %s.",
        "rpt_step2": " 2. Перенесите файл в db/devices/",
        "rpt_step3": " 3. Проверьте: python tools/validate_db.py",
        "rpt_step4": " 4. Отправьте pull request. Подробности в CONTRIBUTING.md",

        "scan_progress": "проверено %d%% (%d из %d)",
        "scan_devices_found": "Устройств с открытыми портами камер: %d",
        "scan_nothing": "Ничего не найдено. Проверьте, та ли это подсеть.",
        "scan_asking_onvif": "Спрашиваю, кто отзывается по ONVIF …",
        "scan_answered": "Ответили: %s",
        "scan_querying": "Опрашиваю найденные устройства …",
        "st_stream_available": "поток доступен",
        "st_enable_onvif": "нужно включить ONVIF",
        "st_onvif_no_stream": "ONVIF есть, потока нет",
        "st_closed": "закрыта",
        "st_not_camera": "не похоже на камеру",
        "val_unknown_model": "неизвестно",
        "col_address": "АДРЕС",
        "col_mac": "MAC",
        "col_model": "МОДЕЛЬ",
        "col_state": "СОСТОЯНИЕ",
        "unknown_row": "%s (%s, прошивка %s)",
        "unknown_help": "Помогите пополнить базу:",
        "sum_cameras": "Камер: %d, из них с доступным потоком: %d",
        "sum_others": "Прочих устройств (не камеры): %d",
        "discover_none": "никто не ответил",

        "scanning": "Сканирую %s ...",
        "final_usable": "Камера пригодна для сторонних клиентов.",
        "final_url": "Рабочий адрес: %s",
        "final_write_cfg": "Записать настройки: python probe.py %s --write-config",
        "final_hidden_1": "Поток закрыт, но у этой модели ONVIF включается в приложении.",
        "final_hidden_2": "Выполните шаги выше, перезагрузите камеру и запустите проверку снова.",
        "final_failed": "Стандартный поток получить не удалось.",

        "arg_description": "Диагностика IP-камеры: опознавание модели и поиск потоков",
        "arg_host": "IP камеры в вашей локальной сети",
        "arg_scan": "обойти подсеть целиком, например 192.168.1.0/24",
        "arg_user": "логин ONVIF",
        "arg_password": "пароль ONVIF",
        "arg_write_config": "записать config.json для клиента",
        "arg_report": "создать заготовку записи для базы устройств",
        "arg_no_verify": "не проверять потоки приёмом данных (при проверке одной камеры)",
        "arg_discover": "найти ONVIF-камеры в сети перед сканированием",
        "arg_lang": "язык вывода: en, ru или auto (по умолчанию auto)",
        "err_need_target": "укажите IP камеры либо подсеть через --scan",
    },
}
