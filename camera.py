"""
Работа с камерой: ONVIF (управление) и RTSP (видео).
Отделено от интерфейса, чтобы логику можно было использовать и без окна.
"""
import os
import sysconfig
import threading
from urllib.parse import quote

# Без таймаута zeep ждёт ответа на запрос бесконечно: камера, принявшая
# соединение и замолчавшая, навсегда вешает и клиент, и обход подсети.
ONVIF_TIMEOUT = 10


def _wsdl_dir():
    """
    onvif-zeep кладёт wsdl мимо пакета — проверяем известные места.
    В собранном PyInstaller виде файлы лежат во временном каталоге _MEIPASS.
    """
    import sys

    import onvif

    candidates = []
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(os.path.join(bundle, "wsdl"))

    pkg = os.path.dirname(onvif.__file__)
    candidates += [
        os.path.join(pkg, "wsdl"),
        os.path.join(os.path.dirname(pkg), "wsdl"),
        os.path.join(sysconfig.get_paths()["purelib"], "wsdl"),
    ]

    for path in candidates:
        if os.path.isfile(os.path.join(path, "devicemgmt.wsdl")):
            return path
    raise RuntimeError("не найден каталог wsdl библиотеки onvif-zeep")


class Camera:
    """Одна камера: профили потоков, PTZ, снимок."""

    def __init__(self, host, onvif_port=8899, user="admin", password="", rtsp_port=554):
        self.host = host
        self.onvif_port = onvif_port
        self.rtsp_port = rtsp_port
        self.user = user
        self.password = password

        self._lock = threading.Lock()
        self._cam = None
        self._media = None
        self._ptz = None
        self.profiles = []      # [{token, name, width, height, uri}]
        self.info = {}
        self.ptz_available = False

    # ---------- подключение ----------

    def connect(self):
        from onvif import ONVIFCamera
        from zeep.transports import Transport

        with self._lock:
            self._cam = ONVIFCamera(
                self.host, self.onvif_port, self.user, self.password, _wsdl_dir(),
                transport=Transport(timeout=ONVIF_TIMEOUT,
                                    operation_timeout=ONVIF_TIMEOUT),
            )
            try:
                data = self._cam.devicemgmt.GetDeviceInformation()
                self.info = {
                    "manufacturer": data.Manufacturer,
                    "model": data.Model,
                    "firmware": data.FirmwareVersion,
                    "serial": data.SerialNumber,
                }
            except Exception:
                self.info = {}

            self._media = self._cam.create_media_service()
            self.profiles = []
            for profile in self._media.GetProfiles():
                enc = profile.VideoEncoderConfiguration
                res = getattr(enc, "Resolution", None)
                self.profiles.append(
                    {
                        "token": profile.token,
                        "name": profile.Name,
                        "width": res.Width if res else 0,
                        "height": res.Height if res else 0,
                        "uri": self._stream_uri(profile.token),
                    }
                )

            try:
                self._ptz = self._cam.create_ptz_service()
                self._ptz.GetNodes()
                self.ptz_available = True
            except Exception:
                self._ptz = None
                self.ptz_available = False
        return self

    def _stream_uri(self, token):
        request = self._media.create_type("GetStreamUri")
        request.ProfileToken = token
        request.StreamSetup = {
            "Stream": "RTP-Unicast",
            "Transport": {"Protocol": "RTSP"},
        }
        uri = self._media.GetStreamUri(request).Uri
        # камера возвращает адрес без порта и без учётных данных — дополняем
        scheme, rest = uri.split("://", 1)
        hostpart, _, path = rest.partition("/")
        if ":" not in hostpart:
            hostpart = f"{hostpart}:{self.rtsp_port}"
        # «@», «:» или «/» в пароле без экранирования ломают разбор адреса
        credentials = (f"{quote(self.user, safe='')}:{quote(self.password, safe='')}@"
                       if self.password else "")
        return f"{scheme}://{credentials}{hostpart}/{path}"

    # ---------- PTZ ----------

    def move(self, pan=0.0, tilt=0.0, zoom=0.0, profile_token=None):
        """Непрерывное движение. Остановить — stop()."""
        if not self.ptz_available:
            return False
        token = profile_token or self.profiles[0]["token"]
        request = self._ptz.create_type("ContinuousMove")
        request.ProfileToken = token
        request.Velocity = {
            "PanTilt": {"x": pan, "y": tilt},
            "Zoom": {"x": zoom},
        }
        with self._lock:
            self._ptz.ContinuousMove(request)
        return True

    def stop(self, profile_token=None):
        if not self.ptz_available:
            return False
        token = profile_token or self.profiles[0]["token"]
        with self._lock:
            self._ptz.Stop({"ProfileToken": token, "PanTilt": True, "Zoom": True})
        return True

    def presets(self, profile_token=None):
        if not self.ptz_available:
            return []
        token = profile_token or self.profiles[0]["token"]
        with self._lock:
            return self._ptz.GetPresets({"ProfileToken": token}) or []

    def goto_preset(self, preset_token, profile_token=None):
        token = profile_token or self.profiles[0]["token"]
        with self._lock:
            self._ptz.GotoPreset({"ProfileToken": token, "PresetToken": preset_token})

    def save_preset(self, name, profile_token=None):
        token = profile_token or self.profiles[0]["token"]
        with self._lock:
            return self._ptz.SetPreset({"ProfileToken": token, "PresetName": name})

    # ---------- прочее ----------

    def snapshot_uri(self, profile_token=None):
        token = profile_token or self.profiles[0]["token"]
        with self._lock:
            return self._media.GetSnapshotUri({"ProfileToken": token}).Uri

    @property
    def title(self):
        model = self.info.get("model", "камера")
        return f"{model} @ {self.host}"
