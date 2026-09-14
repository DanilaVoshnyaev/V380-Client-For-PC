"""
Отчёт по объекту: результат обхода подсети одной HTML-страницей.

Нужен для выезда к заказчику. В терминале результат видит только тот, кто
его запустил; отчёт можно оставить клиенту, переслать в мессенджере или
распечатать.

Страница намеренно самодостаточная: стили внутри, ни одной внешней ссылки,
ни одного запроса наружу. Она должна открываться с флешки, с телефона и
без интернета — на объекте интернета может не быть.

Язык берётся из i18n, как и у остального вывода.
"""
import datetime
import html

import i18n
from i18n import t

# Состояние камеры -> что с ней можно сделать и каким цветом это показать.
# Ключи совпадают с теми, что проставляет probe.run_scan.
ACTIONS = {
    "ready": ("rh_act_ready", "ok"),
    "enable": ("rh_act_enable", "todo"),
    "setup": ("rh_act_setup", "todo"),
    "locked": ("rh_act_locked", "bad"),
}

STYLE = """
:root { color-scheme: light; }
body { margin: 0; padding: 32px 24px; background: #f6f7f9; color: #16181d;
       font: 15px/1.55 "Segoe UI", system-ui, -apple-system, Arial, sans-serif; }
.sheet { max-width: 900px; margin: 0 auto; background: #fff; padding: 36px 40px;
         border-radius: 10px; box-shadow: 0 1px 3px rgba(0,0,0,.10); }
h1 { margin: 0 0 4px; font-size: 26px; letter-spacing: -.01em; }
.sub { color: #6b7280; margin-bottom: 28px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .06em;
     color: #6b7280; margin: 32px 0 12px; font-weight: 600; }
.cards { display: flex; flex-wrap: wrap; gap: 12px; }
.card { flex: 1 1 150px; background: #f3f4f6; border-radius: 8px; padding: 14px 16px; }
.card .n { font-size: 28px; font-weight: 600; line-height: 1.1; }
.card .l { color: #6b7280; font-size: 13px; margin-top: 2px; }
.card.ok .n { color: #15803d; }
.card.todo .n { color: #b45309; }
.card.bad .n { color: #b91c1c; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th { text-align: left; font-weight: 600; color: #6b7280; font-size: 12px;
     text-transform: uppercase; letter-spacing: .05em;
     border-bottom: 2px solid #e5e7eb; padding: 8px 10px; }
td { border-bottom: 1px solid #eef0f3; padding: 9px 10px; vertical-align: top; }
tr:last-child td { border-bottom: none; }
code { font: 13px/1.4 Consolas, "SF Mono", Menlo, monospace;
       background: #f3f4f6; padding: 1px 5px; border-radius: 4px; }
.tag { display: inline-block; font-size: 12px; padding: 2px 8px; border-radius: 999px;
       white-space: nowrap; }
.tag.ok { background: #dcfce7; color: #15803d; }
.tag.todo { background: #fef3c7; color: #b45309; }
.tag.bad { background: #fee2e2; color: #b91c1c; }
ul.urls { list-style: none; padding: 0; margin: 0; }
ul.urls li { padding: 5px 0; }
.note { color: #6b7280; font-size: 13px; margin-top: 10px; }
.warn { background: #fef3c7; border-left: 3px solid #f59e0b;
        padding: 12px 16px; border-radius: 0 6px 6px 0; }
footer { margin-top: 36px; padding-top: 16px; border-top: 1px solid #eef0f3;
         color: #9ca3af; font-size: 12px; }
@media print {
  body { background: #fff; padding: 0; }
  .sheet { box-shadow: none; max-width: none; padding: 0; }
  h2 { break-after: avoid; }
  tr { break-inside: avoid; }
}
"""


def _esc(value):
    return html.escape(str(value if value is not None else ""))


def _card(number, label, kind=""):
    return ('<div class="card %s"><div class="n">%d</div>'
            '<div class="l">%s</div></div>' % (kind, number, _esc(label)))


def build(rows, subnet, tool_name="CameraProbe", tool_version=""):
    """
    Собирает HTML. rows — строки из probe.run_scan, каждая с ключом
    'action' ('ready' / 'enable' / 'setup' / 'locked') и 'is_camera'.
    """
    stamp = datetime.datetime.now().strftime("%d.%m.%Y %H:%M")
    cameras = [r for r in rows if r["is_camera"]]
    others = len(rows) - len(cameras)

    ready = [r for r in cameras if r["action"] == "ready"]
    can_free = [r for r in cameras if r["action"] in ("enable", "setup")]
    locked = [r for r in cameras if r["action"] == "locked"]

    out = []
    add = out.append
    add("<style>%s</style>" % STYLE)
    add('<div class="sheet">')
    add("<h1>%s</h1>" % _esc(t("rh_title")))
    add('<div class="sub">%s</div>' % _esc(t("rh_subtitle", subnet, stamp)))

    add("<h2>%s</h2>" % _esc(t("rh_summary")))
    add('<div class="cards">')
    add(_card(len(cameras), t("rh_total")))
    add(_card(len(ready), t("rh_ready"), "ok"))
    add(_card(len(can_free), t("rh_can_free"), "todo"))
    if locked:
        add(_card(len(locked), t("rh_locked"), "bad"))
    add("</div>")
    if others:
        add('<div class="note">%s</div>' % _esc(t("rh_others", others)))

    add("<h2>%s</h2>" % _esc(t("rh_devices")))
    add("<table><tr><th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr>" % (
        _esc(t("rh_col_addr")), _esc(t("rh_col_model")),
        _esc(t("rh_col_state")), _esc(t("rh_col_action"))))
    for row in cameras:
        key, kind = ACTIONS.get(row["action"], ("rh_act_setup", "todo"))
        add("<tr><td><code>%s</code></td><td>%s</td>"
            '<td><span class="tag %s">%s</span></td><td>%s</td></tr>' % (
                _esc(row["host"]), _esc(row["model"]), kind,
                _esc(row["status"]), _esc(t(key))))
    add("</table>")

    if ready:
        add("<h2>%s</h2>" % _esc(t("rh_urls")))
        add('<ul class="urls">')
        for row in ready:
            add("<li><code>%s</code></li>" % _esc(row["stream"]))
        add("</ul>")
        add('<div class="note">%s</div>' % _esc(t("rh_urls_note")))

    # Про отсутствие пароля владелец обязан узнать, даже если не спрашивал
    open_streams = [r for r in cameras if r.get("no_auth")]
    if open_streams:
        add("<h2>%s</h2>" % _esc(t("rh_security")))
        add('<div class="warn"><strong>%s</strong><br>%s</div>' % (
            _esc(t("rh_sec_open", len(open_streams))), _esc(t("rh_sec_note"))))

    add("<footer>%s<br>%s</footer>" % (
        _esc(t("rh_disclaimer")),
        _esc(t("rh_footer", tool_name, tool_version, stamp))))
    add("</div>")
    return "\n".join(out)


def save(path, rows, subnet, tool_name="CameraProbe", tool_version=""):
    header = [
        "<!doctype html>",
        '<html lang="%s">' % i18n.language(),
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        "<title>%s</title>" % _esc(t("rh_title")),
    ]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(header) + "\n")
        fh.write(build(rows, subnet, tool_name, tool_version))
        fh.write("\n</html>\n")
    return path
