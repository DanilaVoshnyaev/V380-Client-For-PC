"""
Проверка базы устройств перед приёмом pull request.

    python tools/validate_db.py

Без внешних зависимостей — запускается в CI без установки пакетов.
Код возврата 0 — всё в порядке, 1 — есть ошибки.
"""
import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_DIR = os.path.join(BASE_DIR, "db", "devices")
SCHEMA_PATH = os.path.join(BASE_DIR, "db", "schema.json")

VALID_STATUS = {"open", "hidden-onvif", "closed", "firmware-mod"}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9.]+)*$")
MAC_RE = re.compile(r"^([0-9A-F]{2}:){2}[0-9A-F]{2}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# probe.py помечает незаполненные поля на языке пользователя — ловим оба
PLACEHOLDERS = ("ЗАПОЛНИТЕ", "FILL IN")


class Report:
    def __init__(self):
        self.errors = []
        self.warnings = []

    def error(self, where, text):
        self.errors.append("%s: %s" % (where, text))

    def warn(self, where, text):
        self.warnings.append("%s: %s" % (where, text))


def allowed_keys():
    """Список разрешённых полей верхнего уровня берём из самой схемы."""
    with open(SCHEMA_PATH, encoding="utf-8") as fh:
        schema = json.load(fh)
    return set(schema["properties"].keys()), schema


# Поля, которые читает пользователь: строка либо словарь языков {en, ru}
LOCALIZED_FIELDS = (
    ("display_name", lambda r: [r.get("display_name")]),
    ("notes", lambda r: [r.get("notes")]),
    ("unlock.app_path", lambda r: [(r.get("unlock") or {}).get("app_path")]),
    ("unlock.steps", lambda r: (r.get("unlock") or {}).get("steps") or []),
    ("security.notes", lambda r: [(r.get("security") or {}).get("notes")]),
    ("streams[].label", lambda r: [s.get("label") for s in r.get("streams", [])]),
)


def check_localized(record, where, report):
    """
    Перевод без английского не принимаем: база международная, и английский —
    запасной вариант для всех остальных языков.
    """
    for name, extract in LOCALIZED_FIELDS:
        for value in extract(record):
            if value is None or isinstance(value, str):
                continue
            if not isinstance(value, dict):
                report.error(where, "%s: ожидается строка или словарь языков" % name)
                continue
            if not value.get("en"):
                report.error(where, "%s: в переводе обязателен ключ 'en'" % name)
            for code, text in value.items():
                if len(code) != 2 or not code.isalpha() or not code.islower():
                    report.error(where, "%s: '%s' не похож на код языка" % (name, code))
                elif not isinstance(text, str) or not text.strip():
                    report.error(where, "%s: пустой перевод для '%s'" % (name, code))


def check_record(record, filename, report, top_keys, schema):
    where = filename

    for field in ("id", "display_name", "match", "unlock", "verified"):
        if field not in record:
            report.error(where, "нет обязательного поля '%s'" % field)
    if report.errors and "id" not in record:
        return

    record_id = record.get("id", "")
    if not ID_RE.match(record_id):
        report.error(where, "id '%s' должен быть в нижнем регистре через дефис" % record_id)
    expected_file = record_id + ".json"
    if filename != expected_file:
        report.error(where, "имя файла должно совпадать с id: ожидается %s" % expected_file)

    unknown = set(record.keys()) - top_keys - {"_file"}
    if unknown:
        report.error(where, "неизвестные поля: %s" % ", ".join(sorted(unknown)))

    # вложенные поля сверяем со схемой
    for section in ("match", "unlock", "onvif", "rtsp", "features", "security", "verified"):
        if section not in record or not isinstance(record[section], dict):
            continue
        allowed = set(schema["properties"][section].get("properties", {}).keys())
        extra = set(record[section].keys()) - allowed
        if extra:
            report.error(where, "в '%s' неизвестные поля: %s" % (section, ", ".join(sorted(extra))))

    unlock = record.get("unlock", {})
    status = unlock.get("status")
    if status not in VALID_STATUS:
        report.error(where, "unlock.status '%s' вне списка %s" % (status, sorted(VALID_STATUS)))
    if status == "hidden-onvif":
        steps = unlock.get("steps") or []
        if not steps:
            report.error(where, "для статуса hidden-onvif нужны шаги включения (unlock.steps)")
        if not unlock.get("app_path"):
            report.warn(where, "не указано unlock.app_path — где в приложении искать настройку")

    match = record.get("match", {})
    if not match:
        report.error(where, "пустой блок match — запись невозможно сопоставить с устройством")
    else:
        strong = any(match.get(k) for k in ("onvif_manufacturer", "rtsp_banner", "http_server"))
        if not strong:
            report.warn(where, "нет сильных признаков (onvif_manufacturer / rtsp_banner / "
                               "http_server) — опознавание будет ненадёжным")
    for prefix in match.get("mac_prefixes", []):
        if not MAC_RE.match(prefix):
            report.error(where, "MAC-префикс '%s' должен быть вида 4C:2F:7B" % prefix)

    for stream in record.get("streams", []):
        path = stream.get("path", "")
        if not path.startswith("/"):
            report.error(where, "путь потока '%s' должен начинаться со слэша" % path)

    check_localized(record, where, report)

    verified = record.get("verified", {})
    if not DATE_RE.match(verified.get("date", "")):
        report.error(where, "verified.date должна быть в формате ГГГГ-ММ-ДД")

    security = record.get("security") or {}
    if security.get("rtsp_without_auth") and not security.get("notes"):
        report.warn(where, "поток без авторизации, но нет пояснения в security.notes")

    # незаполненные заготовки
    blob = json.dumps(record, ensure_ascii=False)
    for marker in PLACEHOLDERS:
        if marker in blob:
            report.error(where, "остались незаполненные поля с пометкой '%s'" % marker)


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if not os.path.isdir(DB_DIR):
        print("Каталог базы не найден: %s" % DB_DIR)
        return 1

    top_keys, schema = allowed_keys()
    report = Report()
    seen_ids = {}
    count = 0

    for filename in sorted(os.listdir(DB_DIR)):
        if not filename.endswith(".json"):
            report.warn(filename, "посторонний файл в каталоге базы")
            continue
        count += 1
        path = os.path.join(DB_DIR, filename)
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
        except json.JSONDecodeError as exc:
            report.error(filename, "битый JSON: %s" % exc)
            continue

        record_id = record.get("id")
        if record_id in seen_ids:
            report.error(filename, "id '%s' уже занят файлом %s" % (record_id, seen_ids[record_id]))
        else:
            seen_ids[record_id] = filename

        check_record(record, filename, report, top_keys, schema)

    print("Проверено записей: %d" % count)
    if report.warnings:
        print("\nПредупреждения:")
        for text in report.warnings:
            print("  ! %s" % text)
    if report.errors:
        print("\nОшибки:")
        for text in report.errors:
            print("  x %s" % text)
        print("\nПроверка не пройдена.")
        return 1

    print("\nВсе записи корректны.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
