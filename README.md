*[Русская версия](README.ru.md)*

# Free your cheap IP camera from the vendor cloud

Cheap Chinese cameras are sold under dozens of brands, but inside they run
a handful of identical firmwares. **Many of them do have ONVIF and RTSP —
just switched off at the factory.** Turn it back on and the camera works with
VLC, Home Assistant, Frigate, Blue Iris, Scrypted and anything else. No vendor
cloud, no phone app, no Android emulator on your desktop.

The catch is that "on this model ONVIF is enabled *here*" is scattered across
forum posts in fragments. This project collects it.

Two parts:

1. **A fingerprint database** (`db/devices/`) — model and firmware → is there
   a hidden ONVIF, where it is enabled, which stream paths and ports it uses.
2. **Tools** — `probe.py` identifies a camera over the network and prints what
   to do next; `client.py` shows the video and drives pan/tilt/zoom.

## Identify your camera

    python probe.py 192.168.1.100 --user admin --password YOUR_PASSWORD

It scans ports, takes a fingerprint (RTSP and HTTP banners, MAC, ONVIF data),
looks the model up in the database and tells you what to do. If streams are
already reachable, it verifies them by actually receiving data and prints the
working URLs.

Write out a config for the client:

    python probe.py 192.168.1.100 --write-config

Output language follows your system. Force it with `--lang en` or `--lang ru`.

## Sweep a whole site at once

    python probe.py --scan 192.168.1.0/24

Finds every camera on the subnet, identifies the models and lays them out in
one table: which ones already serve a stream, which need ONVIF switched on,
and which are locked shut. A /24 takes about a minute and a half.

    ADDRESS         MAC                MODEL                     STATE
    192.168.1.1     90:FB:5D:XX:XX:XX  unknown                   not a camera
    192.168.1.100   4C:2F:7B:XX:XX:XX  V380 / Galatron PTZ       stream available

A device has to actually *answer* over RTSP or HTTP, not merely hold a port
open. Without that check, scanning a network with Docker, WSL or a VPN on it
"finds" hundreds of cameras that do not exist: there, a TCP connection
succeeds against any address at all.

### No terminal at all

    python probe.py --scan auto

`auto` detects your own network, so you do not have to look up your IP or know
what a subnet is. In the packaged build, double-clicking **scan-cameras.bat**
does exactly this — scans the network, writes a report and opens it, with no
command line involved. Scanning an address outside your own network is refused
unless you pass `--i-own-this`.

### A report you can hand over

    python probe.py --scan 192.168.1.0/24 --report-html survey.html

Writes the survey as one self-contained HTML page: a summary, the device
table, the working stream URLs, and a warning for every camera serving video
without a password. It pulls in nothing from the internet, so it opens
offline, on a phone, from a USB stick, and prints to PDF cleanly.

The report holds the addresses and MAC addresses of real equipment — keep it
out of the repository.

## Which cameras are supported

**Any of them.** `probe.py` does not need your model to be in the database.
It fingerprints the device, finds the streams, verifies them by receiving
data and prints the working URLs — that works with any camera that speaks
ONVIF or RTSP, from any vendor.

The database answers a different question: *ONVIF is off — where exactly do
I switch it on for this model?* You cannot learn that over the network. It
can only be written down once, by someone who has the camera in their hands,
and passed on.

| Model | Firmware | Access | Vendor app |
|---|---|---|---|
| V380 / Galatron PTZ (macro-video) | 2.4 | ONVIF enabled from the app | V380 Pro |

One entry so far. [Add yours](CONTRIBUTING.md) — one command and half an hour.

Your camera is not in the database? Send it in:

    python probe.py 192.168.1.100 --report

The generated draft contains **no IP address, no passwords and no serial
number** — only model traits shared by every device of that kind. Details in
[CONTRIBUTING.md](CONTRIBUTING.md).

---

# Windows client

A replacement for the vendor app: live video and camera control straight over
standard protocols.

## Running it

Double-click `run.bat`, or:

    python client.py

## Features

- Live video (H264 720p / 360p, switch on the fly)
- PTZ: 8 directions, zoom, arrow-key control
- Presets: save the current position and return to it
- Snapshot (`S`)
- MP4 recording (`R`) — runs alongside the live view without interrupting it
- Fullscreen — double-click the video or press `F`, leave with `Esc`

Snapshots and recordings go to `save_dir` from `config.json`
(by default `Users\<you>\Pictures\Camera`).

## Files

| File | Purpose |
|---|---|
| `db/devices/*.json` | Fingerprint database: one model per file |
| `db/schema.json` | Schema for a database entry |
| `probe.py` | Camera identification, stream discovery, database draft |
| `fingerprint.py` | Network fingerprinting: ports, banners, MAC, ONVIF |
| `camdb.py` | Loading the database and weighted trait matching |
| `i18n.py` | Output messages in English and Russian |
| `client.py` | Application window, UI, hotkeys |
| `camera.py` | Talking to the camera: ONVIF, stream profiles, PTZ |
| `tools/validate_db.py` | Database validation, runs in CI on every PR |
| `config.json` | Your connection settings (never committed) |

## Settings (`config.json`)

    host                 camera IP
    onvif_port           ONVIF port (8899 on this camera)
    rtsp_port            RTSP port (554)
    user / password      ONVIF credentials
    ptz_speed            pan/tilt speed, 0.1-1.0
    save_dir             where snapshots and recordings go
    network_caching_ms   buffer. Lower means less latency, higher means smoother
    use_tcp              RTSP over TCP. Leave true if you drop frames

Copy `config.example.json` to `config.json` and fill in your values:

    copy config.example.json config.json

`config.json` is in `.gitignore` — it holds your camera password.

## Direct camera URLs

    720p stream   rtsp://192.168.1.100:554/live/ch00_0
    360p stream   rtsp://192.168.1.100:554/live/ch00_1
    Snapshot      http://192.168.1.100:8899/snapshot/PROFILE_000
    ONVIF         http://192.168.1.100:8899/onvif/device_service

The same URLs work in VLC, Agent DVR, Blue Iris, Frigate and Home Assistant.

## Requirements

Python 3.12, VLC installed (64-bit), and:

    pip install onvif-zeep WSDiscovery python-vlc

## Building the .exe

So that people without Python can use the tool:

    pip install pyinstaller
    python build.py

`dist/` will contain `CameraClient.exe` (~20 MB) and `CameraProbe.exe`
(~17 MB), each with its SHA256 printed.

VLC is deliberately **not** bundled: it stays an external dependency. That is
the honest reading of its LGPL license, and it keeps the build from gaining a
few hundred megabytes. The user needs VLC installed, matching the build's
architecture.

## Security note

This camera's RTSP server serves the stream **without any authentication** —
anyone on your local network can watch it if they know the path. Do not
forward ports 554 and 8899 to the internet. To reach the cameras from
elsewhere, use a VPN: see [vpn/](vpn/README.md) for a WireGuard setup.

## Contributing

Adding your camera to the database takes one command and half an hour, and
saves the next person an evening of forum archaeology. See
[CONTRIBUTING.md](CONTRIBUTING.md).

Entries with status `closed` — "there is no standard way in" — are valuable
too. They save people from trying.

## Support the project

The project is free and stays free. If it saved you an evening, there is a
wallet in [DONATE.md](DONATE.md) — entirely optional, and nothing is owed in
return.

Adding your camera to the database is worth more than money, and costs one
command.

## License

Code — [MIT](LICENSE). Take it and do what you like.

Device database (`db/`) — [CC BY-SA 4.0](db/LICENSE). Free to use, including
commercially; a derived database has to stay open. The copyleft covers the
data, not your code that reads it.

Contributions are accepted under [CLA.md](CLA.md).
