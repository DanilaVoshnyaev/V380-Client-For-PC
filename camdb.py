"""
База отпечатков камер: загрузка записей и сопоставление с найденным устройством.

Каждая модель описана отдельным файлом в db/devices/ — так правки из разных
pull request не конфликтуют между собой.
"""
import json
import os

import i18n
import paths

BASE_DIR = paths.app_dir()
DB_DIR = paths.db_dir()
SCHEMA_PATH = paths.schema_path()

# Вес признака при сопоставлении. Чем уникальнее признак, тем выше вес.
WEIGHTS = {
    "onvif_manufacturer": 40,
    "onvif_model": 20,
    "firmware": 15,
    "rtsp_banner": 35,
    "http_server": 15,
    "mac_prefixes": 25,
    "ports": 10,
}

# Порог, ниже которого совпадение считается недостоверным
MIN_SCORE = 35

def status_text(status):
    """Описание статуса доступа на языке вывода. Неизвестный — как есть."""
    key = "status_%s" % status
    text = i18n.t(key)
    return status if text == key else text


def load_db(directory=None):
    """Читает все записи базы. Возвращает список словарей."""
    directory = directory or DB_DIR
    if not os.path.isdir(directory):
        return []
    records = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        with open(path, encoding="utf-8") as fh:
            record = json.load(fh)
        record["_file"] = name
        records.append(record)
    return records


def localized(value, lang=None):
    """
    Текстовое поле записи бывает обычной строкой либо словарём языков:

        "notes": "English text"
        "notes": {"en": "English text", "ru": "Русский текст"}

    Английский обязателен и служит запасным вариантом: база международная,
    а перевод присылают не к каждой записи.
    """
    if isinstance(value, dict):
        lang = lang or i18n.language()
        return value.get(lang) or value.get("en") or next(iter(value.values()), "")
    if isinstance(value, list):
        return [localized(item, lang) for item in value]
    return value


def _norm(value):
    return (value or "").strip().lower()


def score(record, fingerprint):
    """
    Считает, насколько запись базы подходит найденному устройству.
    Возвращает (очки, список совпавших признаков).

    Противоречие в сильном признаке обнуляет совпадение целиком: камера
    с другим производителем в ONVIF — это заведомо другая модель.
    """
    match = record.get("match", {})
    points = 0
    reasons = []

    for field in ("onvif_manufacturer", "onvif_model", "http_server"):
        expected = _norm(match.get(field))
        actual = _norm(fingerprint.get(field))
        if not expected or not actual:
            continue
        if expected in actual or actual in expected:
            points += WEIGHTS[field]
            reasons.append(field)
        elif field == "onvif_manufacturer":
            return 0, []  # другой производитель — точно не эта запись

    banner_expected = _norm(match.get("rtsp_banner"))
    banner_actual = _norm(fingerprint.get("rtsp_banner"))
    if banner_expected and banner_actual and banner_expected in banner_actual:
        points += WEIGHTS["rtsp_banner"]
        reasons.append("rtsp_banner")

    firmware = _norm(fingerprint.get("firmware"))
    versions = [_norm(v) for v in match.get("firmware", [])]
    if firmware and versions:
        if firmware in versions:
            points += WEIGHTS["firmware"]
            reasons.append("firmware")
        else:
            reasons.append("firmware:другая")

    mac = _norm(fingerprint.get("mac")).replace("-", ":").upper()
    prefixes = [p.upper() for p in match.get("mac_prefixes", [])]
    if mac and prefixes and any(mac.startswith(p) for p in prefixes):
        points += WEIGHTS["mac_prefixes"]
        reasons.append("mac")

    expected_ports = set(match.get("ports", []))
    actual_ports = set(fingerprint.get("open_ports", []))
    if expected_ports and expected_ports.issubset(actual_ports):
        points += WEIGHTS["ports"]
        reasons.append("ports")

    return points, reasons


def identify(fingerprint, records=None):
    """
    Подбирает записи базы под отпечаток.
    Возвращает список (запись, очки, признаки), отсортированный по убыванию.
    """
    records = records if records is not None else load_db()
    results = []
    for record in records:
        points, reasons = score(record, fingerprint)
        if points >= MIN_SCORE:
            results.append((record, points, reasons))
    results.sort(key=lambda item: item[1], reverse=True)
    return results


def stream_urls(record, host):
    """Собирает полные RTSP-адреса из записи базы."""
    port = (record.get("rtsp") or {}).get("port", 554)
    urls = []
    for stream in record.get("streams", []):
        path = stream["path"]
        if not path.startswith("/"):
            path = "/" + path
        urls.append({
            "url": "rtsp://%s:%d%s" % (host, port, path),
            "label": localized(stream.get("label", "")),
            "resolution": stream.get("resolution", ""),
            "codec": stream.get("codec", ""),
        })
    return urls


def describe(record):
    """Краткое человекочитаемое описание записи."""
    status = (record.get("unlock") or {}).get("status", "?")
    return "%s — %s" % (localized(record["display_name"]), status_text(status))


def as_config(record, host, user="admin", password=""):
    """Готовый config.json для клиента по данным из базы."""
    onvif = record.get("onvif") or {}
    rtsp = record.get("rtsp") or {}
    return {
        "host": host,
        "onvif_port": onvif.get("port", 8899),
        "rtsp_port": rtsp.get("port", 554),
        "user": user or onvif.get("default_user", "admin"),
        "password": password,
        "ptz_speed": 0.5,
        "save_dir": os.path.join(os.path.expanduser("~"), "Pictures", "Camera"),
        "network_caching_ms": 300,
        "use_tcp": True,
    }
