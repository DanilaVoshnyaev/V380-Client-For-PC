"""
Проверка криптоадресов в DONATE.md.

    python tools/check_donations.py

Опечатка в адресе необратима: деньги уйдут в никуда и вернуть их нельзя.
У всех поддерживаемых форматов внутри есть контрольная сумма, поэтому
опечатку видно, не отправляя ни копейки. Проверка идёт в CI на каждый
pull request — заодно любое изменение адреса становится заметным.

Без внешних зависимостей: CI запускает её без установки пакетов.
Код возврата 0 — всё в порядке, 1 — есть ошибки.
"""
import hashlib
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DONATE_PATH = os.path.join(BASE_DIR, "DONATE.md")

BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BECH32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

# Что ищем в тексте. Прозу не ограничиваем — адреса находим по форме.
PATTERNS = [
    ("TRON / USDT TRC-20", re.compile(r"\bT[1-9A-HJ-NP-Za-km-z]{33}\b")),
    ("Bitcoin (bech32)", re.compile(r"\bbc1[ac-hj-np-z02-9]{11,71}\b")),
    ("Bitcoin (legacy)", re.compile(r"\b[13][1-9A-HJ-NP-Za-km-z]{25,34}\b")),
    ("EVM (Ethereum, BSC, Polygon)", re.compile(r"\b0x[a-fA-F0-9]{40}\b")),
]


def base58_decode(text):
    number = 0
    for char in text:
        index = BASE58.find(char)
        if index < 0:
            return None
        number = number * 58 + index
    body = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return b"\x00" * (len(text) - len(text.lstrip("1"))) + body


def base58check_ok(text):
    """Bitcoin legacy и TRON: последние 4 байта — двойной SHA256 от остального."""
    raw = base58_decode(text)
    if not raw or len(raw) < 5:
        return False
    payload, checksum = raw[:-4], raw[-4:]
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4] == checksum


def bech32_polymod(values):
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for bit in range(5):
            checksum ^= generator[bit] if ((top >> bit) & 1) else 0
    return checksum


def bech32_ok(text):
    """BIP-173 / BIP-350. Константа отличает bech32 от bech32m (SegWit v1+)."""
    text = text.lower()
    position = text.rfind("1")
    if position < 1 or position + 7 > len(text):
        return False
    hrp, data_part = text[:position], text[position + 1:]
    data = []
    for char in data_part:
        index = BECH32.find(char)
        if index < 0:
            return False
        data.append(index)
    expanded = [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]
    return bech32_polymod(expanded + data) in (1, 0x2BC830A3)


def check(kind, address):
    """Возвращает (ок, пояснение)."""
    if kind.startswith("TRON") or kind.startswith("Bitcoin (legacy)"):
        return base58check_ok(address), "контрольная сумма base58check"
    if kind.startswith("Bitcoin (bech32)"):
        return bech32_ok(address), "контрольная сумма bech32"
    # Для EVM контрольная сумма — EIP-55 на keccak256, которого нет в
    # стандартной библиотеке. Проверяем форму и предупреждаем отдельно.
    return True, "проверена только форма, контрольной суммы нет"


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if not os.path.isfile(DONATE_PATH):
        print("DONATE.md не найден — проверять нечего.")
        return 0

    text = open(DONATE_PATH, encoding="utf-8").read()

    found = []
    for kind, pattern in PATTERNS:
        for address in pattern.findall(text):
            # Легаси-адрес Bitcoin по форме совпадает и с частью других строк,
            # поэтому дубли по одному и тому же тексту отбрасываем
            if any(address == item[1] for item in found):
                continue
            found.append((kind, address))

    if not found:
        print("В DONATE.md не найдено ни одного адреса.")
        print("Либо файл пуст, либо адреса записаны в неузнаваемом виде.")
        return 1

    errors = 0
    warnings = 0
    print("Найдено адресов: %d\n" % len(found))
    for kind, address in found:
        ok, note = check(kind, address)
        if not ok:
            print("  x %-30s %s" % (kind, address))
            print("      НЕВЕРНАЯ %s — проверьте адрес посимвольно" % note)
            errors += 1
        elif "нет" in note:
            print("  ! %-30s %s" % (kind, address))
            print("      %s" % note)
            warnings += 1
        else:
            print("  ok %-29s %s" % (kind, address))

    if errors:
        print("\nОшибок: %d. Публиковать нельзя: деньги уйдут в никуда." % errors)
        return 1
    if warnings:
        print("\nПредупреждений: %d. Сверьте адрес с кошельком вручную." % warnings)
    print("\nВсе адреса корректны.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
