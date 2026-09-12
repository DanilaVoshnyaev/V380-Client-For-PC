"""
Генерация конфигурации WireGuard: домашний ПК как сервер, телефон как клиент.

    python vpn/wg_setup.py --endpoint 203.0.113.10 --lan 192.168.1.0/24

Создаёт в каталоге vpn/:
    server-wg0.conf   — импортируется в WireGuard на домашнем ПК
    phone.conf        — конфиг телефона
    phone-qr.png      — тот же конфиг QR-кодом, для камеры телефона

Ключи генерируются локально и никуда не отправляются. Файлы содержат
приватные ключи — в git они не попадают и передавать их никому нельзя.
"""
import argparse
import base64
import os
import sys

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives import serialization

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


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


def main():
    parser = argparse.ArgumentParser(description="Конфигурация WireGuard для доступа к камерам")
    parser.add_argument("--endpoint", required=True,
                        help="публичный IP или DDNS-имя вашего роутера")
    parser.add_argument("--port", type=int, default=51820, help="UDP-порт WireGuard")
    parser.add_argument("--lan", default="192.168.1.0/24",
                        help="домашняя подсеть, где живут камеры")
    parser.add_argument("--vpn", default="10.8.0.0/24",
                        help="служебная подсеть туннеля")
    parser.add_argument("--peers", nargs="+", default=["phone"],
                        help="имена устройств-клиентов")
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

    server, clients, server_public = build(
        args.endpoint, args.port, args.lan, args.vpn, args.peers)

    server_path = os.path.join(BASE_DIR, "server-wg0.conf")
    with open(server_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(server)
    print("Сервер : %s" % server_path)
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


if __name__ == "__main__":
    main()
