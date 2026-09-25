"""
Генерация конфигурации WireGuard: домашний ПК как сервер, телефон как клиент.

    python vpn/wg_setup.py

Создаёт в каталоге vpn/:
    server-wg0.conf   — импортируется в WireGuard на домашнем ПК
    phone.conf        — конфиг телефона
    phone-qr.png      — тот же конфиг QR-кодом, для камеры телефона

Внешний адрес и домашняя подсеть определяются сами; задать их вручную —
--endpoint и --lan.

    python vpn/wg_setup.py --install

Настраивает Windows целиком: ставит туннель службой, включает маршрутизацию,
NAT и правило брандмауэра. Нужны права администратора — скрипт запросит их
сам. Если конфигов ещё нет, сначала создаёт их. Отменить — --uninstall.

Ключи генерируются локально и никуда не отправляются. Файлы содержат
приватные ключи — в git они не попадают и передавать их никому нельзя.
"""
import argparse
import base64
import ctypes
import ipaddress
import os
import shutil
import subprocess
import sys
import urllib.request

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(BASE_DIR))

import fingerprint  # noqa: E402

TUNNEL = "server-wg0"
SERVER_CONF = os.path.join(BASE_DIR, "%s.conf" % TUNNEL)
NAT_NAME = "WGCameras"
FIREWALL_RULE = "WireGuard cameras"


def keypair():
    """Пара ключей X25519 в формате WireGuard (base64)."""
    private = X25519PrivateKey.generate()
    private_bytes = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return (base64.b64encode(private_bytes).decode(),
            base64.b64encode(public_bytes).decode())


def build(endpoint, port, lan, vpn_net, peers):
    server_private, server_public = keypair()
    gateway = vpn_net.replace("0/24", "1")

    clients = []
    server_peers = []
    for index, name in enumerate(peers, start=2):
        client_private, client_public = keypair()
        address = vpn_net.replace("0/24", str(index))

        server_peers.append(
            "\n[Peer]\n"
            "# %s\n"
            "PublicKey = %s\n"
            "AllowedIPs = %s/32\n" % (name, client_public, address)
        )

        # AllowedIPs только домашняя сеть: обычный трафик телефона идёт
        # напрямую, через туннель ходит лишь обращение к камерам.
        clients.append((name,
            "[Interface]\n"
            "PrivateKey = %s\n"
            "Address = %s/32\n"
            "\n"
            "[Peer]\n"
            "PublicKey = %s\n"
            "Endpoint = %s:%d\n"
            "AllowedIPs = %s, %s\n"
            "PersistentKeepalive = 25\n"
            % (client_private, address, server_public, endpoint, port,
               lan, vpn_net)))

    server = (
        "[Interface]\n"
        "# Домашний ПК — сервер WireGuard\n"
        "PrivateKey = %s\n"
        "Address = %s/24\n"
        "ListenPort = %d\n"
        % (server_private, gateway, port)
    ) + "".join(server_peers)

    return server, clients, server_public


# ---------------------------------------------------------------- адреса

def public_ip(timeout=5):
    """
    Внешний адрес роутера. Изнутри сети его не узнать, поэтому спрашиваем
    у внешнего сервиса: он видит, с какого адреса пришёл запрос.
    """
    for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                text = response.read(64).decode("ascii", "replace").strip()
            return str(ipaddress.IPv4Address(text))
        except (OSError, ValueError):
            continue
    return None


def behind_carrier_nat(ip):
    """
    Негде принять входящее соединение: адрес NAT провайдера (100.64.x.x) или
    вовсе частный. Проброс порта на роутере тогда не поможет.
    """
    return not ipaddress.IPv4Address(ip).is_global


# ---------------------------------------------------------------- генерация

def generate(args):
    local_ip, subnet = fingerprint.local_subnet()
    lan = args.lan or subnet
    if not lan:
        raise SystemExit("Не удалось определить домашнюю подсеть. "
                         "Укажите её сами: --lan 192.168.1.0/24")

    endpoint = args.endpoint
    if not endpoint:
        endpoint = public_ip()
        if not endpoint:
            raise SystemExit("Не удалось узнать внешний адрес: нет интернета или "
                             "сервис недоступен. Укажите его сами: --endpoint АДРЕС")
        print("Внешний адрес: %s (определён автоматически)" % endpoint)
        if behind_carrier_nat(endpoint):
            raise SystemExit("Адрес %s принадлежит NAT провайдера: снаружи до вашего "
                             "роутера не достучаться. См. «Провайдерский NAT» в "
                             "vpn/README.md." % endpoint)
    print("Домашняя сеть: %s" % lan)

    server, clients, server_public = build(
        endpoint, args.port, lan, args.vpn, args.peers)

    with open(SERVER_CONF, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(server)
    print("Сервер : %s" % SERVER_CONF)
    print("         публичный ключ %s" % server_public)

    try:
        import qrcode
    except ImportError:
        qrcode = None

    for name, config in clients:
        path = os.path.join(BASE_DIR, "%s.conf" % name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(config)
        print("\nКлиент : %s" % path)

        if qrcode:
            image_path = os.path.join(BASE_DIR, "%s-qr.png" % name)
            qrcode.make(config).save(image_path)
            print("QR     : %s" % image_path)
            code = qrcode.QRCode(border=1)
            code.add_data(config)
            code.print_ascii(invert=True)

    print("\nФайлы содержат приватные ключи. Не выкладывайте их и не пересылайте.")
    return local_ip


# ---------------------------------------------------------------- установка

def wireguard_exe():
    for path in (os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                              "WireGuard", "wireguard.exe"),
                 shutil.which("wireguard")):
        if path and os.path.isfile(path):
            return path
    return None


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except AttributeError:
        return False


def relaunch_as_admin():
    """Перезапуск с правами администратора: Windows покажет запрос UAC."""
    params = subprocess.list2cmdline(
        [os.path.abspath(__file__)] + sys.argv[1:] + ["--elevated"])
    code = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, params, None, 1)
    # ShellExecute возвращает число больше 32 при успехе
    if code <= 32:
        raise SystemExit("Права администратора не получены — настройка отменена.")
    print("Настройка продолжится в новом окне с правами администратора.")


def ps_quote(text):
    return "'%s'" % text.replace("'", "''")


def ps_wireguard(wireguard, *arguments):
    """
    wireguard.exe — оконная программа: через «&» PowerShell её не ждёт
    и кода возврата не видит. Start-Process -Wait даёт и то, и другое.
    """
    return ("$p = Start-Process -FilePath %s -ArgumentList %s -Wait -PassThru; "
            "if ($p.ExitCode -ne 0) { throw 'wireguard.exe завершился с ошибкой' }"
            % (ps_quote(wireguard),
               ", ".join(ps_quote('"%s"' % a) for a in arguments)))


def install_script(wireguard, local_ip, vpn_net, port):
    service = "WireGuardTunnel$%s" % TUNNEL
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",

        # Конфиг мог смениться — переустанавливаем туннель начисто
        "if (Get-Service -Name %s -ErrorAction SilentlyContinue) {" % ps_quote(service),
        "    " + ps_wireguard(wireguard, "/uninstalltunnelservice", TUNNEL),
        "    Start-Sleep -Seconds 2",
        "}",
        "Write-Host 'Туннель: ставлю службой, чтобы он поднимался сам после перезагрузки'",
        ps_wireguard(wireguard, "/installtunnelservice", SERVER_CONF),
        "for ($i = 0; $i -lt 30 -and -not (Get-NetAdapter -Name %s "
        "-ErrorAction SilentlyContinue); $i++) { Start-Sleep -Milliseconds 500 }"
        % ps_quote(TUNNEL),

        # Без маршрутизации и NAT камера получит пакет от адреса туннеля,
        # не будет знать обратного маршрута и ответит в никуда
        "Write-Host 'Маршрутизация: туннель <-> домашняя сеть'",
        "Set-NetIPInterface -InterfaceAlias %s -AddressFamily IPv4 -Forwarding Enabled"
        % ps_quote(TUNNEL),
        "$lan = (Get-NetIPAddress -IPAddress %s).InterfaceIndex" % ps_quote(local_ip),
        "Set-NetIPInterface -InterfaceIndex $lan -AddressFamily IPv4 -Forwarding Enabled",

        "Write-Host 'NAT для адресов туннеля'",
        "if (-not (Get-NetNat -Name %s -ErrorAction SilentlyContinue)) {" % ps_quote(NAT_NAME),
        "    New-NetNat -Name %s -InternalIPInterfaceAddressPrefix %s | Out-Null"
        % (ps_quote(NAT_NAME), ps_quote(vpn_net)),
        "}",

        "Write-Host 'Брандмауэр: входящий UDP %d'" % port,
        "Get-NetFirewallRule -DisplayName %s -ErrorAction SilentlyContinue "
        "| Remove-NetFirewallRule" % ps_quote(FIREWALL_RULE),
        "New-NetFirewallRule -DisplayName %s -Direction Inbound -Protocol UDP "
        "-LocalPort %d -Action Allow -Profile Any | Out-Null"
        % (ps_quote(FIREWALL_RULE), port),
    ])


def uninstall_script(wireguard):
    service = "WireGuardTunnel$%s" % TUNNEL
    lines = ["$ErrorActionPreference = 'Continue'"]
    if wireguard:
        lines += [
            "if (Get-Service -Name %s -ErrorAction SilentlyContinue) {" % ps_quote(service),
            "    " + ps_wireguard(wireguard, "/uninstalltunnelservice", TUNNEL),
            "}",
        ]
    lines += [
        "Get-NetNat -Name %s -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false"
        % ps_quote(NAT_NAME),
        "Get-NetFirewallRule -DisplayName %s -ErrorAction SilentlyContinue "
        "| Remove-NetFirewallRule" % ps_quote(FIREWALL_RULE),
    ]
    return "\n".join(lines)


def run_powershell(script):
    result = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script])
    return result.returncode == 0


def install(args):
    wireguard = wireguard_exe()
    if not wireguard and not args.dry_run:
        raise SystemExit("WireGuard не установлен. Поставьте его и запустите снова:\n\n"
                         "    winget install --id WireGuard.WireGuard -e")

    if os.path.isfile(SERVER_CONF):
        # Не перевыпускаем ключи: иначе уже настроенный телефон отвалится
        print("Беру готовый конфиг: %s" % SERVER_CONF)
        local_ip = fingerprint.local_subnet()[0]
    else:
        local_ip = generate(args)
    if not local_ip:
        raise SystemExit("Не удалось определить адрес этого компьютера в домашней сети.")

    script = install_script(wireguard or r"C:\Program Files\WireGuard\wireguard.exe",
                            local_ip, args.vpn, args.port)
    if args.dry_run:
        print("\n" + script)
        return
    if not run_powershell(script):
        raise SystemExit("\nНастройка прервалась на шаге выше. Частая причина — NAT "
                         "уже занят Docker или WSL: в Windows он может быть только "
                         "один (проверить: Get-NetNat).")

    print("\nКомпьютер настроен. Осталось два шага руками:\n")
    print("1. Роутер: переадресация порта UDP %d на адрес %s, порт %d."
          % (args.port, local_ip, args.port))
    print("   Там же закрепите за компьютером этот адрес (DHCP reservation),")
    print("   иначе после перезагрузки он может смениться.")
    print("2. Телефон: приложение WireGuard → добавить туннель → сканировать")
    print("   QR-код из %s." % os.path.join(BASE_DIR, "phone-qr.png"))


def uninstall(args):
    script = uninstall_script(wireguard_exe())
    if args.dry_run:
        print(script)
        return
    run_powershell(script)
    print("Туннель, NAT и правило брандмауэра удалены. Конфиги в vpn/ остались.")


def main():
    parser = argparse.ArgumentParser(description="Конфигурация WireGuard для доступа к камерам")
    parser.add_argument("--endpoint",
                        help="публичный IP или DDNS-имя роутера (по умолчанию — "
                             "определить автоматически)")
    parser.add_argument("--port", type=int, default=51820, help="UDP-порт WireGuard")
    parser.add_argument("--lan",
                        help="домашняя подсеть, где живут камеры (по умолчанию — "
                             "сеть этого компьютера)")
    parser.add_argument("--vpn", default="10.8.0.0/24",
                        help="служебная подсеть туннеля")
    parser.add_argument("--peers", nargs="+", default=["phone"],
                        help="имена устройств-клиентов")
    parser.add_argument("--install", action="store_true",
                        help="настроить Windows: туннель, маршрутизация, NAT, брандмауэр")
    parser.add_argument("--uninstall", action="store_true",
                        help="убрать всё, что сделал --install")
    parser.add_argument("--dry-run", action="store_true",
                        help="только показать команды PowerShell, ничего не менять")
    parser.add_argument("--elevated", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    if not (args.install or args.uninstall):
        generate(args)
        return

    if sys.platform != "win32":
        raise SystemExit("--install и --uninstall работают только в Windows.")
    # Проверяем до запроса прав, чтобы не гонять человека через UAC впустую
    if args.install and not args.dry_run and not wireguard_exe():
        raise SystemExit("WireGuard не установлен. Поставьте его и запустите снова:\n\n"
                         "    winget install --id WireGuard.WireGuard -e")
    if not args.dry_run and not is_admin():
        relaunch_as_admin()
        return

    try:
        if args.uninstall:
            uninstall(args)
        else:
            install(args)
    except SystemExit as exc:
        if not args.elevated or exc.code in (None, 0):
            raise
        print(exc.code)
    finally:
        # Окно с правами администратора открылось отдельно — не даём ему
        # закрыться раньше, чем человек прочтёт результат
        if args.elevated:
            input("\nНажмите Enter, чтобы закрыть окно.")


if __name__ == "__main__":
    main()
