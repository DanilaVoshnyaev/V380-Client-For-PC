"""
Снятие сетевого отпечатка камеры.

Собирает всё, по чему устройство можно опознать: открытые порты, MAC,
баннеры RTSP и HTTP, ответ ONVIF. Ничего не меняет на камере — только читает.
"""
import re
import socket
import subprocess
import sys

DEFAULT_PORTS = [80, 81, 443, 554, 8000, 8080, 8081, 8554, 8899, 9000, 34567]

# Пути потоков, встречающиеся у разных прошивок. Проверяются, только если
# ONVIF не сообщил адреса сам.
COMMON_RTSP_PATHS = [
    "/live/ch00_0", "/live/ch00_1", "/onvif1", "/onvif2",
    "/11", "/12", "/av0_0", "/ch0_0.h264", "/stream0", "/stream1",
    "/h264", "/live", "/live/main", "/media/video1", "/video1",
    "/cam/realmonitor?channel=1&subtype=0", "/user=admin&password=&channel=1&stream=0.sdp",
]


# ---------------------------------------------------------------- порты

def scan_ports(host, ports=None, timeout=1.2):
    """Возвращает список открытых TCP-портов. Порты проверяются параллельно."""
    from concurrent.futures import ThreadPoolExecutor

    ports = ports or DEFAULT_PORTS

    def check(port):
        sock = socket.socket()
        sock.settimeout(timeout)
        try:
            sock.connect((host, port))
            return port
        except OSError:
            return None
        finally:
            sock.close()

    with ThreadPoolExecutor(max_workers=min(len(ports), 16)) as pool:
        return sorted(p for p in pool.map(check, ports) if p)


# ---------------------------------------------------------------- MAC

def get_mac(host):
    """MAC устройства из ARP-таблицы. Работает только в своей подсети."""
    try:
        # прогреваем ARP-запись
        socket.create_connection((host, 80), 0.4).close()
    except OSError:
        pass
    try:
        if sys.platform == "win32":
            out = subprocess.run(["arp", "-a", host], capture_output=True,
                                 text=True, timeout=5).stdout
        else:
            out = subprocess.run(["arp", "-n", host], capture_output=True,
                                 text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    for line in out.splitlines():
        if host not in line:
            continue
        found = re.search(r"([0-9a-fA-F]{2}[:-]){5}[0-9a-fA-F]{2}", line)
        if found:
            return found.group(0).replace("-", ":").upper()
    return None


# ---------------------------------------------------------------- RTSP

def _rtsp_request(host, port, method, url, extra="", timeout=5):
    """Одиночный RTSP-запрос. Возвращает (заголовки, тело) или (None, None)."""
    try:
        sock = socket.create_connection((host, port), timeout)
    except OSError:
        return None, None
    sock.settimeout(timeout)
    try:
        request = "%s %s RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: camprobe\r\n%s\r\n" % (
            method, url, extra)
        sock.sendall(request.encode())

        # Сначала дочитываем заголовки целиком
        data = b""
        while b"\r\n\r\n" not in data and len(data) < 8192:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk

        head, separator, body = data.partition(b"\r\n\r\n")
        if not separator:
            return None, None

        # Тело читаем ровно столько, сколько обещано в Content-Length.
        # Ждать «пока не отвалится по таймауту» нельзя: короткий ответ
        # на OPTIONS тогда вообще не возвращается.
        length = re.search(rb"(?im)^Content-Length:\s*(\d+)", head)
        if length:
            expected = int(length.group(1))
            while len(body) < expected and len(body) < 65536:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                body += chunk

        return head.decode("utf-8", "replace"), body.decode("utf-8", "replace")
    except OSError:
        return None, None
    finally:
        sock.close()


def probe_rtsp_path(host, port, path, timeout=5):
    """
    Проверяет один путь. Возвращает словарь с кодом ответа и данными SDP,
    либо None, если сервер не ответил.
    """
    url = "rtsp://%s:%d%s" % (host, port, path)
    head, body = _rtsp_request(host, port, "DESCRIBE", url,
                               "Accept: application/sdp\r\n", timeout)
    if head is None:
        return None

    status = 0
    found = re.match(r"RTSP/1\.\d (\d+)", head)
    if found:
        status = int(found.group(1))

    info = {
        "path": path,
        "status": status,
        "auth_required": status == 401,
        "url": url,
        "codecs": [],
        "banner": None,
    }

    server = re.search(r"(?im)^Server:\s*(.+)$", head)
    if server:
        info["server"] = server.group(1).strip()

    if status == 200 and body:
        session_name = re.search(r"(?m)^s=(.+)$", body)
        if session_name:
            info["banner"] = session_name.group(1).strip()
        info["codecs"] = re.findall(r"(?m)^a=rtpmap:\d+ ([\w-]+)/", body)
        info["sdp"] = body
    return info


def find_rtsp_streams(host, port=554, paths=None, timeout=4):
    """
    Перебирает пути и возвращает те, что отвечают потоком.

    Часть прошивок (в том числе macro-video) отдаёт один и тот же поток на
    ЛЮБОЙ путь, включая заведомо несуществующий. Поэтому сначала проверяем
    контрольный несуществующий адрес: если он отвечает потоком, перебор
    бессмысленен и точные пути надо брать из ONVIF или из базы.
    """
    explicit = paths is not None
    control = probe_rtsp_path(host, port, "/camprobe-nonexistent-path", timeout)
    catch_all = bool(control and control["status"] == 200)

    if catch_all and not explicit:
        control["path"] = "(любой путь)"
        control["url"] = "rtsp://%s:%d/" % (host, port)
        control["catch_all"] = True
        return [control]

    working = []
    seen = set()
    for path in (paths or COMMON_RTSP_PATHS):
        info = probe_rtsp_path(host, port, path, timeout)
        if not info or info["status"] not in (200, 401):
            continue
        info["catch_all"] = catch_all
        # Одинаковые ответы на разные пути схлопываем — но только при переборе.
        # Пути, полученные от ONVIF, авторитетны: это разные профили, и терять
        # второй из-за похожего SDP нельзя.
        if not explicit:
            key = info.get("sdp") or info["status"]
            if key in seen:
                continue
            seen.add(key)
        working.append(info)
    return working


def verify_stream(host, port, path, seconds=5):
    """
    Честная проверка: полное рукопожатие RTSP и приём RTP-пакетов.
    Не требует видеовыхода, поэтому работает и без окна.
    Возвращает (получено_байт, ошибка).
    """
    url = "rtsp://%s:%d%s" % (host, port, path)
    try:
        sock = socket.create_connection((host, port), 5)
    except OSError as exc:
        return 0, str(exc)
    sock.settimeout(6)

    cseq = [0]

    def request(method, extra="", target=url):
        cseq[0] += 1
        sock.sendall(("%s %s RTSP/1.0\r\nCSeq: %d\r\nUser-Agent: camprobe\r\n%s\r\n"
                      % (method, target, cseq[0], extra)).encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        return head.decode("utf-8", "replace"), rest

    try:
        request("DESCRIBE", "Accept: application/sdp\r\n")
        head, _ = request("SETUP",
                          "Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n",
                          url + "/track1")
        session = ""
        for line in head.splitlines():
            if line.lower().startswith("session:"):
                session = line.split(":", 1)[1].split(";")[0].strip()
        head, leftover = request("PLAY",
                                 "Session: %s\r\nRange: npt=0.000-\r\n" % session)
        if "200" not in head.splitlines()[0]:
            return 0, "PLAY отклонён: " + head.splitlines()[0]

        import time
        total = len(leftover)
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                chunk = sock.recv(65535)
            except socket.timeout:
                break
            if not chunk:
                break
            total += len(chunk)
        return total, None
    except OSError as exc:
        return 0, str(exc)
    finally:
        sock.close()


# ---------------------------------------------------------------- HTTP

def http_banner(host, ports, timeout=3):
    """Заголовок Server с первого откликнувшегося HTTP-порта."""
    for port in ports:
        if port not in (80, 81, 443, 8000, 8080, 8081, 8899, 9000):
            continue
        try:
            sock = socket.create_connection((host, port), timeout)
        except OSError:
            continue
        try:
            sock.settimeout(timeout)
            sock.sendall(b"GET / HTTP/1.0\r\nHost: %s\r\n\r\n" % host.encode())
            data = sock.recv(2048).decode("utf-8", "replace")
            found = re.search(r"(?im)^Server:\s*(.+)$", data)
            if found:
                return found.group(1).strip()
        except OSError:
            pass
        finally:
            sock.close()
    return None


# ---------------------------------------------------------------- ONVIF

def discover_onvif(timeout=5):
    """WS-Discovery: находит ONVIF-устройства в локальной сети."""
    try:
        from wsdiscovery.discovery import ThreadedWSDiscovery
        from wsdiscovery import QName
    except ImportError:
        return []

    service = ThreadedWSDiscovery()
    service.start()
    try:
        found = service.searchServices(
            types=[QName("http://www.onvif.org/ver10/network/wsdl",
                         "NetworkVideoTransmitter")],
            timeout=timeout,
        )
    except Exception:
        return []
    finally:
        service.stop()

    addresses = []
    for item in found:
        addresses.extend(item.getXAddrs())
    return addresses


def query_onvif(host, port, user="admin", password=""):
    """
    Опрашивает ONVIF: сведения об устройстве, профили, возможности PTZ.
    Возвращает словарь или None.
    """
    try:
        from camera import Camera
    except ImportError:
        return None
    try:
        camera = Camera(host, port, user, password).connect()
    except Exception:
        return None

    return {
        "manufacturer": camera.info.get("manufacturer"),
        "model": camera.info.get("model"),
        "firmware": camera.info.get("firmware"),
        "serial": camera.info.get("serial"),
        "ptz": camera.ptz_available,
        "profiles": camera.profiles,
        "port": port,
    }


# ---------------------------------------------------------------- сборка

def speaks_protocol(host, open_ports, timeout=2.0):
    """
    Проверяет, что устройство действительно говорит по ожидаемому протоколу,
    а не просто принимает TCP-соединение.

    Нужно потому, что в некоторых сетях (Docker, WSL, VPN, captive portal)
    соединение успешно устанавливается с любым адресом и портом. Без этой
    проверки сканирование «находит» сотни несуществующих камер.

    Возвращает 'rtsp', 'http' или None.
    """
    if 554 in open_ports:
        head, _ = _rtsp_request(host, 554, "OPTIONS", "rtsp://%s:554/" % host,
                                timeout=timeout)
        if head and head.startswith("RTSP/"):
            return "rtsp"

    for port in open_ports:
        if port not in (80, 81, 443, 8000, 8080, 8081, 8899, 9000, 34567):
            continue
        try:
            sock = socket.create_connection((host, port), timeout)
        except OSError:
            continue
        try:
            sock.settimeout(timeout)
            sock.sendall(b"GET / HTTP/1.0\r\nHost: %s\r\n\r\n" % host.encode())
            data = sock.recv(256)
            if data.startswith(b"HTTP/"):
                return "http"
        except OSError:
            pass
        finally:
            sock.close()
    return None


def scan_subnet(cidr, ports=None, workers=64, timeout=0.6, progress=None,
                validate=True):
    """
    Быстрый обход подсети: ищет хосты с открытыми «камерными» портами.

    Возвращает список {host, open_ports, protocol}. Полный отпечаток не
    снимает — это отдельный, более долгий шаг для найденных адресов.

    validate=True отсеивает адреса, которые принимают соединение, но не
    отвечают ни по RTSP, ни по HTTP.
    """
    import ipaddress
    from concurrent.futures import ThreadPoolExecutor

    ports = ports or [554, 8899, 80, 8000, 8080, 34567]
    network = ipaddress.ip_network(cidr, strict=False)
    hosts = [str(ip) for ip in network.hosts()]

    def check(host):
        found = []
        for port in ports:
            sock = socket.socket()
            sock.settimeout(timeout)
            try:
                sock.connect((host, port))
                found.append(port)
            except OSError:
                pass
            finally:
                sock.close()
        return {"host": host, "open_ports": found} if found else None

    results = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for item in pool.map(check, hosts):
            done += 1
            if progress:
                progress(done, len(hosts))
            if item:
                results.append(item)

    if not validate or not results:
        for item in results:
            item.setdefault("protocol", None)
        return results

    def confirm(item):
        item["protocol"] = speaks_protocol(item["host"], item["open_ports"])
        return item

    with ThreadPoolExecutor(max_workers=min(len(results), 32)) as pool:
        confirmed = list(pool.map(confirm, results))

    return [item for item in confirmed if item["protocol"]]


def build(host, user="admin", password="", ports=None, deep=True,
          known_ports=None, onvif_ports=None):
    """
    Полный отпечаток устройства. Формат совпадает с полями match в базе,
    поэтому результат можно напрямую сопоставлять с записями.

    known_ports — уже известные открытые порты, чтобы не сканировать повторно.
    onvif_ports — на каких портах пробовать ONVIF. Опрос медленный, поэтому
    при массовом обходе список стоит сужать.
    """
    result = {
        "host": host,
        "open_ports": known_ports if known_ports is not None else scan_ports(host, ports),
        "mac": get_mac(host),
        "onvif_manufacturer": None,
        "onvif_model": None,
        "firmware": None,
        "rtsp_banner": None,
        "http_server": None,
        "onvif": None,
        "rtsp_streams": [],
    }

    result["http_server"] = http_banner(host, result["open_ports"])

    for port in (onvif_ports if onvif_ports is not None else (8899, 80, 8000, 8080)):
        if port not in result["open_ports"]:
            continue
        data = query_onvif(host, port, user, password)
        if data:
            result["onvif"] = data
            result["onvif_manufacturer"] = data["manufacturer"]
            result["onvif_model"] = data["model"]
            result["firmware"] = data["firmware"]
            break

    if 554 in result["open_ports"]:
        paths = None
        if result["onvif"]:
            # ONVIF уже назвал точные пути — перебор не нужен
            paths = []
            for profile in result["onvif"]["profiles"]:
                uri = profile["uri"]
                tail = uri.split("://", 1)[1]
                paths.append("/" + tail.partition("/")[2])
        if deep or paths:
            result["rtsp_streams"] = find_rtsp_streams(host, 554, paths)
        for stream in result["rtsp_streams"]:
            if stream.get("banner"):
                result["rtsp_banner"] = stream["banner"]
                break

    return result
