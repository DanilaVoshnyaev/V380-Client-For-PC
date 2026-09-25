"""
Клиент для IP-камеры (ONVIF + RTSP) под Windows.
Видео декодирует установленный VLC, управление поворотом идёт по ONVIF.

Запуск:  python client.py
Настройки берутся из config.json (создаётся при первом запуске).
"""
import datetime
import json
import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import simpledialog, ttk

import vlc

from camera import Camera

import fingerprint
import paths

BASE_DIR = paths.app_dir()
CONFIG_PATH = paths.config_path()

DEFAULT_CONFIG = {
    # Пусто намеренно: при первом запуске клиент откроет диалог подключения
    # и найдёт камеры сам, вместо того чтобы стучаться в адрес-заглушку.
    "host": "",
    "onvif_port": 8899,
    "rtsp_port": 554,
    "user": "admin",
    "password": "",
    "ptz_speed": 0.5,
    "save_dir": os.path.join(os.path.expanduser("~"), "Pictures", "Camera"),
    "network_caching_ms": 300,
    "use_tcp": True,
}

BG = "#1e1f22"
FG = "#e8e8e8"
ACCENT = "#3b82f6"


def load_config():
    if not os.path.isfile(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(DEFAULT_CONFIG, fh, indent=2, ensure_ascii=False)
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_PATH, encoding="utf-8") as fh:
        config = dict(DEFAULT_CONFIG)
        config.update(json.load(fh))
        return config


class App(tk.Tk):
    def __init__(self, config):
        super().__init__()
        self.config_data = config
        self.camera = None
        self.profile_index = 0
        self.recorder = None
        self.recorder_instance = None
        self.record_path = None
        self._presets = []
        self._ptz_state = None
        self._ptz_queue = queue.Queue()
        threading.Thread(target=self._ptz_worker, daemon=True).start()

        self.title("Камера — клиент")
        self.geometry("1060x680")
        self.minsize(760, 520)
        self.configure(bg=BG)

        self._build_ui()
        self._init_vlc()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        # Есть адрес — подключаемся сразу; нет — сначала спрашиваем данные,
        # чтобы человеку не пришлось искать и править config.json руками.
        if self.config_data.get("host"):
            self.after(200, self.connect_async)
        else:
            self.after(200, self.open_connect_dialog)

    # ---------------- интерфейс ----------------

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TButton", background="#2b2d31", foreground=FG,
                        borderwidth=0, focuscolor=BG, padding=6)
        style.map("TButton", background=[("active", ACCENT)])
        style.configure("TCombobox", fieldbackground="#2b2d31", background="#2b2d31",
                        foreground=FG, arrowcolor=FG)

        # видео
        self.video_panel = tk.Frame(self, bg="black")
        self.video_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)

        # боковая панель
        side = tk.Frame(self, bg=BG, width=250)
        side.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=8)
        side.pack_propagate(False)

        def header(text):
            tk.Label(side, text=text, bg=BG, fg="#9aa0a6",
                     font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 4))

        header("ПОТОК")
        self.quality = ttk.Combobox(side, state="readonly", values=["—"])
        self.quality.pack(fill=tk.X)
        self.quality.bind("<<ComboboxSelected>>", self.on_quality_change)

        header("УПРАВЛЕНИЕ")
        pad = tk.Frame(side, bg=BG)
        pad.pack()
        moves = [
            ("↖", 0, 0, -1, 1), ("↑", 0, 1, 0, 1), ("↗", 0, 2, 1, 1),
            ("←", 1, 0, -1, 0), ("■", 1, 1, 0, 0), ("→", 1, 2, 1, 0),
            ("↙", 2, 0, -1, -1), ("↓", 2, 1, 0, -1), ("↘", 2, 2, 1, -1),
        ]
        self.ptz_buttons = []
        for text, row, col, dx, dy in moves:
            btn = tk.Button(pad, text=text, width=4, height=2, bd=0,
                            bg="#2b2d31", fg=FG, activebackground=ACCENT,
                            font=("Segoe UI", 11))
            btn.grid(row=row, column=col, padx=2, pady=2)
            if (dx, dy) == (0, 0):
                btn.configure(command=self.ptz_stop)
            else:
                btn.bind("<ButtonPress-1>", lambda e, x=dx, y=dy: self.ptz_move(x, y))
                btn.bind("<ButtonRelease-1>", lambda e: self.ptz_stop())
            self.ptz_buttons.append(btn)

        zoom = tk.Frame(side, bg=BG)
        zoom.pack(pady=(8, 0))
        for text, direction in (("Zoom −", -1), ("Zoom +", 1)):
            btn = tk.Button(zoom, text=text, width=8, bd=0, bg="#2b2d31", fg=FG,
                            activebackground=ACCENT)
            btn.pack(side=tk.LEFT, padx=2)
            btn.bind("<ButtonPress-1>", lambda e, z=direction: self.ptz_move(0, 0, z))
            btn.bind("<ButtonRelease-1>", lambda e: self.ptz_stop())
            self.ptz_buttons.append(btn)

        header("ПРЕСЕТЫ")
        self.preset_box = ttk.Combobox(side, state="readonly", values=[])
        self.preset_box.pack(fill=tk.X)
        preset_row = tk.Frame(side, bg=BG)
        preset_row.pack(fill=tk.X, pady=4)
        ttk.Button(preset_row, text="Перейти", command=self.goto_preset).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 2))
        ttk.Button(preset_row, text="Сохранить", command=self.save_preset).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(2, 0))

        header("ЗАПИСЬ")
        ttk.Button(side, text="Снимок  (S)", command=self.snapshot).pack(fill=tk.X, pady=2)
        self.record_btn = ttk.Button(side, text="Начать запись  (R)", command=self.toggle_record)
        self.record_btn.pack(fill=tk.X, pady=2)
        ttk.Button(side, text="Открыть папку", command=self.open_folder).pack(fill=tk.X, pady=2)

        header("ТЕЛЕФОН")
        ttk.Button(side, text="Смотреть на телефоне…", command=self.show_phone_qr).pack(
            fill=tk.X, pady=2)

        header("")
        ttk.Button(side, text="Подключение…", command=self.open_connect_dialog).pack(
            fill=tk.X, pady=2)
        ttk.Button(side, text="Переподключиться", command=self.connect_async).pack(
            fill=tk.X, pady=2)

        # статус
        self.status = tk.Label(self, text="Запуск…", bg="#141517", fg="#9aa0a6",
                               anchor="w", padx=10, font=("Segoe UI", 9))
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

        # горячие клавиши
        self.bind("<Key>", self.on_key)
        self.bind("<KeyRelease>", self.on_key_release)
        self.video_panel.bind("<Double-Button-1>", lambda e: self.toggle_fullscreen())
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))

    def _init_vlc(self):
        opts = ["--quiet"]
        if self.config_data.get("use_tcp", True):
            opts.append("--rtsp-tcp")
        opts.append("--network-caching=%d" % self.config_data.get("network_caching_ms", 300))
        self.vlc_instance = vlc.Instance(*opts)
        self.player = self.vlc_instance.media_player_new()
        # Иначе окно VLC забирает мышь и клавиатуру себе, и до Tk не доходят
        # ни двойной клик по видео, ни горячие клавиши.
        self.player.video_set_mouse_input(False)
        self.player.video_set_key_input(False)
        self.update_idletasks()
        self.player.set_hwnd(self.video_panel.winfo_id())

    def set_status(self, text, color="#9aa0a6"):
        self.status.configure(text=text, fg=color)

    def save_config(self):
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(self.config_data, fh, indent=2, ensure_ascii=False)

    # ---------------- диалог подключения ----------------

    def open_connect_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title("Подключение к камере")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        found = {}  # "текст в списке" -> {"host", "port"}

        def row(label):
            tk.Label(dialog, text=label, bg=BG, fg="#9aa0a6",
                     font=("Segoe UI", 9)).pack(anchor="w", padx=16, pady=(10, 2))

        # --- поиск камер в сети ---
        subnet = fingerprint.local_subnet()[1]
        net_text = ("Ваша сеть: %s" % subnet) if subnet else "Сеть не определена"
        find_status = tk.Label(dialog, text=net_text, bg=BG, fg="#9aa0a6",
                               font=("Segoe UI", 9))

        row("КАМЕРЫ В СЕТИ")
        camera_box = ttk.Combobox(dialog, state="readonly", width=36,
                                  values=["— нажмите «Найти камеры» —"])
        camera_box.current(0)
        camera_box.pack(padx=16, fill=tk.X)

        def on_pick(_event=None):
            item = found.get(camera_box.get())
            if item:
                host_var.set(item["host"])
                if item.get("port"):
                    onvif_var.set(str(item["port"]))

        camera_box.bind("<<ComboboxSelected>>", on_pick)

        def find_worker():
            addresses = fingerprint.discover_onvif(timeout=4) or []
            values, mapping = [], {}
            for address in addresses:
                try:
                    hostport = address.split("://", 1)[1].split("/", 1)[0]
                    host, _, port = hostport.partition(":")
                except IndexError:
                    continue
                label = "%s (ONVIF %s)" % (host, port or "80")
                values.append(label)
                mapping[label] = {"host": host, "port": int(port) if port else 80}

            def done():
                found.clear()
                found.update(mapping)
                if values:
                    camera_box.configure(values=values)
                    camera_box.current(0)
                    on_pick()
                    find_status.configure(
                        text="Найдено камер: %d" % len(values), fg="#22c55e")
                else:
                    camera_box.configure(values=["— камеры не найдены —"])
                    camera_box.current(0)
                    find_status.configure(
                        text="Камеры не ответили. Введите адрес вручную ниже.",
                        fg="#f59e0b")
                find_btn.configure(state=tk.NORMAL, text="Найти камеры")

            self.after(0, done)

        def start_find():
            find_btn.configure(state=tk.DISABLED, text="Ищу…")
            find_status.configure(text="Ищу камеры в сети…", fg="#9aa0a6")
            threading.Thread(target=find_worker, daemon=True).start()

        find_btn = ttk.Button(dialog, text="Найти камеры", command=start_find)
        find_btn.pack(padx=16, pady=(6, 2), fill=tk.X)
        find_status.pack(anchor="w", padx=16)

        # --- поля ввода ---
        host_var = tk.StringVar(value=self.config_data.get("host", ""))
        user_var = tk.StringVar(value=self.config_data.get("user", "admin"))
        pass_var = tk.StringVar(value=self.config_data.get("password", ""))
        onvif_var = tk.StringVar(value=str(self.config_data.get("onvif_port", 8899)))

        def entry(var, show=None):
            widget = tk.Entry(dialog, textvariable=var, show=show, width=38,
                              bg="#2b2d31", fg=FG, insertbackground=FG,
                              relief=tk.FLAT)
            widget.pack(padx=16, ipady=4, fill=tk.X)
            return widget

        row("АДРЕС КАМЕРЫ (ID устройства)")
        entry(host_var)
        row("ЛОГИН")
        entry(user_var)
        row("ПАРОЛЬ")
        entry(pass_var, show="•")

        # порт ONVIF нужен редко — прячем за компактной строкой
        row("ПОРТ ONVIF")
        entry(onvif_var)

        def do_connect():
            host = host_var.get().strip()
            if not host:
                find_status.configure(text="Введите адрес камеры.", fg="#ef4444")
                return
            try:
                port = int(onvif_var.get().strip() or 8899)
            except ValueError:
                port = 8899
            self.config_data["host"] = host
            self.config_data["user"] = user_var.get().strip() or "admin"
            self.config_data["password"] = pass_var.get()
            self.config_data["onvif_port"] = port
            self.save_config()
            dialog.destroy()
            self.connect_async()

        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill=tk.X, padx=16, pady=14)
        ttk.Button(buttons, text="Подключиться", command=do_connect).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(buttons, text="Отмена", command=dialog.destroy).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        dialog.bind("<Return>", lambda e: do_connect())
        dialog.bind("<Escape>", lambda e: dialog.destroy())
        dialog.update_idletasks()
        # по центру родительского окна
        x = self.winfo_rootx() + (self.winfo_width() - dialog.winfo_width()) // 2
        y = self.winfo_rooty() + (self.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry("+%d+%d" % (max(x, 0), max(y, 0)))

    # ---------------- подключение ----------------

    def connect_async(self):
        self.set_status("Подключение к камере…")
        threading.Thread(target=self._connect_worker, daemon=True).start()

    def _connect_worker(self):
        try:
            camera = Camera(
                self.config_data["host"],
                self.config_data["onvif_port"],
                self.config_data["user"],
                self.config_data["password"],
                self.config_data["rtsp_port"],
            ).connect()
        except Exception as exc:
            message = "Не удалось подключиться: %s: %s" % (type(exc).__name__, exc)
            self.after(0, lambda: self.set_status(message, "#ef4444"))
            return
        self.after(0, lambda: self._on_connected(camera))

    def _on_connected(self, camera):
        self.camera = camera
        self._ptz_state = None
        names = []
        for profile in camera.profiles:
            if profile["width"]:
                names.append("%dx%d" % (profile["width"], profile["height"]))
            else:
                names.append(profile["name"])
        self.quality.configure(values=names)
        if names:
            self.quality.current(0)
        self.title("Камера — %s" % camera.title)

        state = tk.NORMAL if camera.ptz_available else tk.DISABLED
        for btn in self.ptz_buttons:
            btn.configure(state=state)
        self.refresh_presets()

        info = camera.info
        self.set_status(
            "%s %s · прошивка %s · PTZ: %s" % (
                info.get("manufacturer", "?"),
                info.get("model", "?"),
                info.get("firmware", "?"),
                "есть" if camera.ptz_available else "нет",
            ),
            "#22c55e",
        )
        self.play_profile(0)

    def play_profile(self, index):
        if not self.camera or index >= len(self.camera.profiles):
            return
        self.profile_index = index
        uri = self.camera.profiles[index]["uri"]
        media = self.vlc_instance.media_new(uri)
        self.player.set_media(media)
        self.player.set_hwnd(self.video_panel.winfo_id())
        self.player.play()

    def on_quality_change(self, _event=None):
        self.play_profile(self.quality.current())

    # ---------------- PTZ ----------------

    def _profile_token(self):
        return self.camera.profiles[self.profile_index]["token"]

    def ptz_move(self, dx, dy, zoom=0.0):
        if not self.camera or not self.camera.ptz_available:
            return
        # Зажатая стрелка на Windows повторяет нажатие десятки раз в секунду,
        # а камере хватает одной команды движения.
        if self._ptz_state == (dx, dy, zoom):
            return
        self._ptz_state = (dx, dy, zoom)
        speed = self.config_data.get("ptz_speed", 0.5)
        self._ptz_queue.put((self.camera.move,
                             (dx * speed, dy * speed, zoom * speed,
                              self._profile_token())))

    def ptz_stop(self):
        if not self.camera or not self.camera.ptz_available:
            return
        self._ptz_state = None
        self._ptz_queue.put((self.camera.stop, (self._profile_token(),)))

    def _ptz_worker(self):
        # Команды уходят строго по очереди из одного потока: при отдельном
        # потоке на каждую «стоп» мог обогнать «движение», и камера
        # продолжала крутиться после отпускания кнопки.
        while True:
            fn, args = self._ptz_queue.get()
            self._safe(fn, *args)

    def _safe(self, fn, *args):
        try:
            fn(*args)
        except Exception as exc:
            message = "Ошибка: %s" % exc
            self.after(0, lambda: self.set_status(message, "#ef4444"))

    # ---------------- пресеты ----------------

    def refresh_presets(self):
        if not self.camera or not self.camera.ptz_available:
            return
        token = self._profile_token()

        def work():
            try:
                presets = self.camera.presets(token)
            except Exception:
                return
            self._presets = presets
            names = [getattr(p, "Name", None) or p.token for p in presets]
            self.after(0, lambda: self.preset_box.configure(values=names))

        threading.Thread(target=work, daemon=True).start()

    def goto_preset(self):
        index = self.preset_box.current()
        if index < 0 or not self._presets:
            return
        preset_token = self._presets[index].token
        token = self._profile_token()
        threading.Thread(
            target=lambda: self._safe(self.camera.goto_preset, preset_token, token),
            daemon=True,
        ).start()

    def save_preset(self):
        if not self.camera or not self.camera.ptz_available:
            return
        name = simpledialog.askstring("Пресет", "Название точки:", parent=self)
        if not name:
            return
        token = self._profile_token()

        def work():
            self._safe(self.camera.save_preset, name, token)
            self.refresh_presets()

        threading.Thread(target=work, daemon=True).start()

    # ---------------- снимки и запись ----------------

    def _save_dir(self):
        path = self.config_data["save_dir"]
        os.makedirs(path, exist_ok=True)
        return path

    def snapshot(self):
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(self._save_dir(), "snapshot_%s.png" % stamp)
        if self.player.video_take_snapshot(0, path, 0, 0) == 0:
            self.set_status("Снимок сохранён: %s" % path, "#22c55e")
        else:
            self.set_status("Не удалось сделать снимок", "#ef4444")

    def toggle_record(self):
        if self.recorder:
            self.recorder.stop()
            self.recorder.release()
            self.recorder = None
            self.recorder_instance = None
            self.record_btn.configure(text="Начать запись  (R)")
            self.set_status("Запись сохранена: %s" % self.record_path, "#22c55e")
            return

        if not self.camera:
            return
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.record_path = os.path.join(self._save_dir(), "record_%s.mp4" % stamp)
        uri = self.camera.profiles[self.profile_index]["uri"]
        # VLC не любит обратные слэши в строке вывода
        dst = self.record_path.replace("\\", "/")
        sout = "--sout=#standard{access=file,mux=mp4,dst=%s}" % dst
        self.recorder_instance = vlc.Instance("--quiet", "--rtsp-tcp", sout, "--sout-keep")
        self.recorder = self.recorder_instance.media_player_new()
        self.recorder.set_media(self.recorder_instance.media_new(uri))
        self.recorder.play()
        self.record_btn.configure(text="■ Остановить запись")
        self.set_status("Идёт запись → %s" % self.record_path, "#f59e0b")

    def show_phone_qr(self):
        if not self.camera:
            self.set_status("Сначала подключитесь к камере", "#f59e0b")
            return
        import qrcode

        uri = self.camera.profiles[self.profile_index]["uri"]
        code = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
        code.add_data(uri)
        code.make(fit=True)
        matrix = code.get_matrix()

        dialog = tk.Toplevel(self)
        dialog.title("Смотреть на телефоне")
        dialog.configure(bg=BG)
        dialog.resizable(False, False)
        dialog.transient(self)

        # Рисуем модули прямо на холсте: так не нужен Pillow в сборке
        cell = max(4, 300 // len(matrix))
        size = cell * len(matrix)
        canvas = tk.Canvas(dialog, width=size, height=size, bg="white",
                           highlightthickness=0)
        canvas.pack(padx=16, pady=(16, 8))
        for y, line in enumerate(matrix):
            for x, dark in enumerate(line):
                if dark:
                    canvas.create_rectangle(x * cell, y * cell, (x + 1) * cell,
                                            (y + 1) * cell, fill="black", width=0)

        steps = (
            "1. Установите на телефон VLC (Google Play или App Store).\n"
            "2. Подключите телефон к той же сети Wi-Fi, что и камера.\n"
            "3. Наведите камеру телефона на код и откройте ссылку в VLC.\n"
            "    На iPhone: скопируйте ссылку, в VLC выберите\n"
            "    «Сеть» → «Открыть сетевой поток» и вставьте её."
        )
        tk.Label(dialog, text=steps, bg=BG, fg=FG, justify=tk.LEFT,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=16)

        link = tk.Entry(dialog, bg="#2b2d31", fg=FG, relief=tk.FLAT,
                        readonlybackground="#2b2d31", width=48)
        link.insert(0, uri)
        link.configure(state="readonly")
        link.pack(padx=16, pady=(10, 4), ipady=4, fill=tk.X)

        def copy():
            self.clipboard_clear()
            self.clipboard_append(uri)
            copy_btn.configure(text="Скопировано")

        buttons = tk.Frame(dialog, bg=BG)
        buttons.pack(fill=tk.X, padx=16, pady=(4, 8))
        copy_btn = ttk.Button(buttons, text="Скопировать ссылку", command=copy)
        copy_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
        ttk.Button(buttons, text="Закрыть", command=dialog.destroy).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

        hint = "Вне дома код не откроется: нужен VPN, см. vpn/README.md."
        if self.camera.password:
            hint += "\nВ коде зашит пароль камеры — не пересылайте его снимок."
        tk.Label(dialog, text=hint, bg=BG, fg="#9aa0a6", justify=tk.LEFT,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=16, pady=(0, 14))

        dialog.bind("<Escape>", lambda e: dialog.destroy())

    def open_folder(self):
        os.startfile(self._save_dir())

    # ---------------- ввод ----------------

    def on_key(self, event):
        key = event.keysym.lower()
        moves = {"up": (0, 1), "down": (0, -1), "left": (-1, 0), "right": (1, 0)}
        if key in moves:
            self.ptz_move(*moves[key])
        elif key == "s":
            self.snapshot()
        elif key == "r":
            self.toggle_record()
        elif key == "f":
            self.toggle_fullscreen()

    def on_key_release(self, event):
        if event.keysym.lower() in ("up", "down", "left", "right"):
            self.ptz_stop()

    def toggle_fullscreen(self):
        self.attributes("-fullscreen", not self.attributes("-fullscreen"))

    def on_close(self):
        try:
            if self.recorder:
                self.recorder.stop()
            self.player.stop()
        except Exception:
            pass
        self.destroy()


def main():
    # Пустой host — не ошибка: клиент откроет диалог подключения сам.
    App(load_config()).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
