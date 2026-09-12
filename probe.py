"""
Разведка IP-камеры: порты -> ONVIF -> RTSP.
Определяет, отдаёт ли камера стандартный поток, и находит его точный адрес.

Запуск:  python probe.py 192.168.1.100 --user admin --password ПАРОЛЬ
"""
import argparse
import os
import socket
import sys
import time

PORTS = [80, 81, 443, 554, 8000, 8080, 8081, 8554, 8899, 9000, 34567]

RTSP_PATHS = [
    "/live/ch00_0", "/live/ch00_1", "/onvif1", "/onvif2",
    "/11", "/12", "/av0_0", "/ch0_0.h264", "/stream0",
    "/h264", "/live", "/media/video1", "/cam/realmonitor?channel=1&subtype=0",
]


def find_wsdl():
    """onvif-zeep ставит wsdl то в пакет, то рядом с ним — ищем оба варианта."""
    import onvif
    import sysconfig

    pkg = os.path.dirname(onvif.__file__)
    candidates = [
        os.path.join(pkg, "wsdl"),
        os.path.join(os.path.dirname(pkg), "wsdl"),
        os.path.join(sysconfig.get_paths()["purelib"], "wsdl"),
        os.path.join(sys.prefix, "wsdl"),
    ]
    for path in candidates:
        if os.path.isfile(os.path.join(path, "devicemgmt.wsdl")):
            return path
    return None


def log(section):
    print(f"\n=== {section} ===")


def scan_ports(host):
    log("ПОРТЫ")
    open_ports = []
    for port in PORTS:
        sock = socket.socket()
        sock.settimeout(1.2)
        try:
            sock.connect((host, port))
            open_ports.append(port)
            print(f"  {port:<6} OPEN")
        except OSError:
            pass
        finally:
            sock.close()
    if not open_ports:
        print("  ни один порт не открыт")
    return open_ports


def discover_onvif():
    log("ONVIF WS-DISCOVERY")
    try:
        from wsdiscovery.discovery import ThreadedWSDiscovery
        from wsdiscovery import QName
    except ImportError as exc:
        print(f"  пропущено: {exc}")
        return []

    wsd = ThreadedWSDiscovery()
    wsd.start()
    try:
        services = wsd.searchServices(
            types=[QName("http://www.onvif.org/ver10/network/wsdl", "NetworkVideoTransmitter")],
            timeout=5,
        )
    finally:
        wsd.stop()

    found = []
    for service in services:
        for addr in service.getXAddrs():
            print(f"  найдено: {addr}")
            found.append(addr)
    if not found:
        print("  никто не ответил")
    return found


def query_onvif(host, port, user, password):
    """Спрашиваем у камеры её профили и готовый RTSP URI."""
    log(f"ONVIF ЗАПРОС {host}:{port}")
    try:
        from onvif import ONVIFCamera
        import onvif
    except ImportError as exc:
        print(f"  пропущено: {exc}")
        return []

    wsdl = find_wsdl()
    if wsdl is None:
        print("  не найден каталог wsdl")
        return []
    try:
        cam = ONVIFCamera(host, port, user, password, wsdl)
        info = cam.devicemgmt.GetDeviceInformation()
        print(f"  производитель : {info.Manufacturer}")
        print(f"  модель        : {info.Model}")
        print(f"  прошивка      : {info.FirmwareVersion}")
        print(f"  серийный      : {info.SerialNumber}")
    except Exception as exc:
        print(f"  не отвечает по ONVIF: {type(exc).__name__}: {exc}")
        return []

    uris = []
    try:
        media = cam.create_media_service()
        for profile in media.GetProfiles():
            req = media.create_type("GetStreamUri")
            req.ProfileToken = profile.token
            req.StreamSetup = {
                "Stream": "RTP-Unicast",
                "Transport": {"Protocol": "RTSP"},
            }
            uri = media.GetStreamUri(req).Uri
            enc = getattr(profile.VideoEncoderConfiguration, "Encoding", "?")
            res = getattr(profile.VideoEncoderConfiguration, "Resolution", None)
            size = f"{res.Width}x{res.Height}" if res else "?"
            print(f"  профиль '{profile.Name}' [{enc} {size}]")
            print(f"    -> {uri}")
            uris.append(uri)
    except Exception as exc:
        print(f"  профили недоступны: {type(exc).__name__}: {exc}")
    return uris


def with_credentials(uri, user, password):
    if "://" not in uri or "@" in uri.split("://", 1)[1].split("/", 1)[0]:
        return uri
    scheme, rest = uri.split("://", 1)
    return f"{scheme}://{user}:{password}@{rest}"


def test_rtsp(uri, timeout=8):
    """Пробуем реально открыть поток и получить кадр."""
    import vlc

    instance = vlc.Instance("--quiet", "--no-audio", "--rtsp-tcp")
    player = instance.media_player_new()
    player.set_media(instance.media_new(uri))
    player.play()

    deadline = time.time() + timeout
    try:
        while time.time() < deadline:
            width, height = player.video_get_size(0)
            if width and height:
                return True, f"{width}x{height}"
            if player.get_state() in (vlc.State.Error, vlc.State.Ended):
                return False, "ошибка соединения"
            time.sleep(0.3)
        return False, "таймаут"
    finally:
        player.stop()
        player.release()


def main():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default="")
    parser.add_argument("--onvif-port", type=int, default=None)
    args = parser.parse_args()

    open_ports = scan_ports(args.host)
    discover_onvif()

    candidates = []
    onvif_ports = [args.onvif_port] if args.onvif_port else [
        p for p in (8899, 80, 8000, 8080) if p in open_ports
    ]
    for port in onvif_ports:
        candidates += query_onvif(args.host, port, args.user, args.password)

    log("ПРОВЕРКА RTSP")
    if 554 not in open_ports and not candidates:
        print("  порт 554 закрыт и ONVIF молчит — перебирать нечего")

    tried = []
    for uri in candidates:
        tried.append(with_credentials(uri, args.user, args.password))
    if 554 in open_ports:
        for path in RTSP_PATHS:
            tried.append(f"rtsp://{args.user}:{args.password}@{args.host}:554{path}")

    working = []
    for uri in dict.fromkeys(tried):
        shown = uri.replace(args.password, "***") if args.password else uri
        ok, detail = test_rtsp(uri)
        print(f"  [{'OK ' if ok else '-- '}] {shown}  ({detail})")
        if ok:
            working.append(uri)

    log("ИТОГ")
    if working:
        print("  Рабочий поток найден:")
        for uri in working:
            print(f"    {uri}")
        print("\n  Этот адрес можно вставлять в VLC, Agent DVR или в наш клиент.")
    else:
        print("  Стандартный поток получить не удалось.")
        print("  Проверьте, что ONVIF включён в приложении и камера перезагружена.")
    return 0 if working else 1


if __name__ == "__main__":
    sys.exit(main())
