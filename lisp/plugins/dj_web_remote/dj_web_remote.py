# -*- coding: utf-8 -*-
import hmac
import io
import json
import logging
import secrets
import socket
import subprocess
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from lisp import plugins
from lisp.core.plugin import Plugin
from lisp.cues.cue import CueState

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 65536
ALLOWED_ACTIONS = {
    "go",
    "fade_all",
    "pause_all",
    "resume_all",
    "stop_all",
    "crossfade",
    "toggle_dim",
    "toggle_mute",
    "toggle_rec",
    "cue_execute",
    "cue_stop",
    "cue_fadeout",
    "eq_set",
    "fx_trigger",
}

REMOTE_HTML = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>DJNova — Control Remoto</title>
<style>
  :root {
    --bg: #090C11; --panel: #141A26; --card: #1C2536; --border: #2E3C54;
    --cyan: #00D4FF; --green: #00E676; --amber: #FFB300; --red: #FF2A4B;
    --text: #E8F0FA; --muted: #8A9BB5;
  }
  * { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  body {
    margin: 0; padding: 10px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }
  header {
    display: flex; justify-content: space-between; align-items: center;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px 14px; margin-bottom: 10px;
  }
  .brand { font-weight: 800; color: var(--cyan); letter-spacing: 1px; font-size: 1rem; }
  .status-dot { display: inline-block; width: 9px; height: 9px; border-radius: 50%; background: var(--green); margin-right: 6px; }
  .grid-ctrl {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 8px; margin-bottom: 10px;
  }
  button {
    border: 1px solid var(--border); border-radius: 10px; padding: 14px 8px;
    font-weight: 800; font-size: 0.9rem; color: #fff; background: var(--card);
    cursor: pointer; touch-action: manipulation;
  }
  button:active { transform: scale(0.96); filter: brightness(1.2); }
  .btn-go { background: linear-gradient(180deg, #00C864, #007A3D); border-color: var(--green); grid-column: span 2; font-size: 1.15rem; }
  .btn-xfade { background: linear-gradient(180deg, #00B8E6, #006E8F); border-color: var(--cyan); }
  .btn-fade { background: linear-gradient(180deg, #E68A00, #995800); border-color: var(--amber); }
  .btn-stop { background: linear-gradient(180deg, #D91E36, #800A19); border-color: var(--red); }
  .btn-active { background: var(--red) !important; border-color: #fff !important; }
  .btn-dim-on { background: var(--amber) !important; color: #000 !important; }
  .section {
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    padding: 10px; margin-bottom: 10px;
  }
  .section h3 {
    margin: 0 0 8px 0; font-size: 0.8rem; color: var(--cyan);
    text-transform: uppercase; letter-spacing: 1px;
  }
  .cue-list { display: flex; flex-direction: column; gap: 7px; }
  .cue-item {
    display: flex; align-items: center; justify-content: space-between;
    background: var(--card); border: 1px solid var(--border); border-left: 5px solid var(--border);
    border-radius: 8px; padding: 10px 12px; gap: 8px;
  }
  .cue-item.running { border-left-color: var(--green); background: #102820; }
  .cue-item.paused { border-left-color: var(--amber); background: #261E0D; }
  .cue-info { flex: 1; min-width: 0; }
  .cue-name { font-weight: 700; font-size: 0.95rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .cue-time { font-family: monospace; font-size: 0.8rem; color: var(--muted); }
  .cue-btns { display: flex; gap: 6px; }
  .cue-btns button { padding: 8px 11px; font-size: 0.8rem; }
  .fx-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
  .fx-btn { padding: 10px 4px; font-size: 0.78rem; border-width: 2px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .eq-row { display: grid; grid-template-columns: 60px 1fr 48px 54px; align-items: center; gap: 8px; margin-bottom: 6px; }
  input[type=range] { width: 100%; accent-color: var(--cyan); }
  #pinOverlay {
    position: fixed; inset: 0; background: rgba(9, 12, 17, 0.96);
    display: none; align-items: center; justify-content: center; z-index: 999; padding: 20px;
  }
  .pin-box {
    background: var(--panel); border: 1px solid var(--cyan); border-radius: 12px;
    padding: 20px; width: 100%; max-width: 320px; text-align: center;
  }
  .pin-box input {
    width: 100%; padding: 12px; font-size: 1.3rem; text-align: center; letter-spacing: 6px;
    border-radius: 8px; border: 1px solid var(--border); background: var(--bg); color: #fff; margin: 12px 0;
  }
</style>
</head>
<body>
<div id="pinOverlay">
  <div class="pin-box">
    <div class="brand" style="margin-bottom:6px;">DJNOVA REMOTO</div>
    <div style="color:var(--muted);font-size:0.85rem;">Ingresa el PIN mostrado en la consola (o escanea el QR):</div>
    <input id="pinInput" type="password" inputmode="numeric" maxlength="8" placeholder="PIN">
    <div id="pinErr" style="color:var(--red);font-size:0.8rem;min-height:18px;"></div>
    <button class="btn-xfade" style="width:100%;" onclick="submitPin()">CONECTAR</button>
  </div>
</div>

<header>
  <div class="brand"><span class="status-dot" id="dot"></span>DJNOVA REMOTO</div>
  <div id="clock" style="font-family:monospace;font-weight:700;color:var(--cyan);">--:--:--</div>
</header>

<div class="grid-ctrl">
  <button class="btn-go" onclick="act('go')">▶ GO / SIGUIENTE</button>
  <button class="btn-xfade" onclick="act('crossfade')">XFADE ▶</button>
  <button class="btn-fade" onclick="act('fade_all')">FADE ALL</button>
  <button onclick="act('pause_all')">❚❚ PAUSA</button>
  <button class="btn-stop" onclick="act('stop_all')">■ STOP ALL</button>
  <button id="btnDim" onclick="act('toggle_dim')">DIM -12dB</button>
  <button id="btnMute" onclick="act('toggle_mute')">MUTE</button>
  <button id="btnRec" onclick="act('toggle_rec')">● REC</button>
</div>

<div class="section" id="fxSection" style="display:none;">
  <h3>Sampler FX (Teclas 1–8)</h3>
  <div class="fx-grid" id="fxGrid"></div>
</div>

<div class="section">
  <h3>Pistas / Cues de la Sesión</h3>
  <div class="cue-list" id="cueList"><div style="color:var(--muted);font-size:0.85rem;">Cargando pistas...</div></div>
</div>

<div class="section" id="eqSection" style="display:none;">
  <h3>Ecualizador DJ 3 Bandas</h3>
  <div class="eq-row">
    <span style="color:var(--cyan);font-weight:700;font-size:0.8rem;">AGUDOS</span>
    <input type="range" id="eqHigh" min="-24" max="12" value="0" onchange="sendEq()">
    <span id="lblHigh" style="font-family:monospace;font-size:0.8rem;">0dB</span>
    <button id="kHigh" style="padding:6px;" onclick="toggleKill('high')">KILL</button>
  </div>
  <div class="eq-row">
    <span style="color:var(--green);font-weight:700;font-size:0.8rem;">MEDIOS</span>
    <input type="range" id="eqMid" min="-24" max="12" value="0" onchange="sendEq()">
    <span id="lblMid" style="font-family:monospace;font-size:0.8rem;">0dB</span>
    <button id="kMid" style="padding:6px;" onclick="toggleKill('mid')">KILL</button>
  </div>
  <div class="eq-row">
    <span style="color:var(--amber);font-weight:700;font-size:0.8rem;">GRAVES</span>
    <input type="range" id="eqLow" min="-24" max="12" value="0" onchange="sendEq()">
    <span id="lblLow" style="font-family:monospace;font-size:0.8rem;">0dB</span>
    <button id="kLow" style="padding:6px;" onclick="toggleKill('low')">KILL</button>
  </div>
</div>

<script>
const urlPin = new URLSearchParams(window.location.search).get('pin');
let accessPin = urlPin || sessionStorage.getItem('djnova_pin') || '';
if (urlPin) sessionStorage.setItem('djnova_pin', urlPin);

let eqState = {low:0, mid:0, high:0, kill_low:false, kill_mid:false, kill_high:false};
let eqBusy = false;

function escHtml(str) {
  return String(str ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}

function submitPin() {
  accessPin = document.getElementById('pinInput').value.trim();
  sessionStorage.setItem('djnova_pin', accessPin);
  document.getElementById('pinErr').textContent = '';
  poll();
}

function fmtTime(ms) {
  if (!ms || ms < 0) return "00:00";
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const r = s % 60;
  return String(m).padStart(2,'0') + ":" + String(r).padStart(2,'0');
}

async function act(action, extra={}) {
  try {
    const res = await fetch('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-DJNova-Pin': accessPin},
      body: JSON.stringify(Object.assign({action}, extra))
    });
    if (res.status === 401) {
      document.getElementById('pinOverlay').style.display = 'flex';
      return;
    }
    setTimeout(poll, 120);
  } catch (e) {}
}

function sendEq() {
  eqBusy = true;
  eqState.low = parseInt(document.getElementById('eqLow').value);
  eqState.mid = parseInt(document.getElementById('eqMid').value);
  eqState.high = parseInt(document.getElementById('eqHigh').value);
  act('eq_set', eqState).finally(() => { setTimeout(() => eqBusy = false, 400); });
}

function toggleKill(band) {
  eqState['kill_' + band] = !eqState['kill_' + band];
  act('eq_set', eqState);
}

async function poll() {
  try {
    const res = await fetch('/api/state', {headers: {'X-DJNova-Pin': accessPin}});
    if (res.status === 401) {
      document.getElementById('dot').style.background = '#FFB300';
      document.getElementById('pinOverlay').style.display = 'flex';
      if (accessPin) document.getElementById('pinErr').textContent = 'PIN incorrecto';
      return;
    }
    document.getElementById('pinOverlay').style.display = 'none';
    const data = await res.json();
    document.getElementById('dot').style.background = '#00E676';
    document.getElementById('clock').textContent = data.clock || '--:--:--';

    document.getElementById('btnDim').className = data.console.dim ? 'btn-dim-on' : '';
    document.getElementById('btnMute').className = data.console.mute ? 'btn-active' : '';
    const recBtn = document.getElementById('btnRec');
    recBtn.className = data.console.recording ? 'btn-active' : '';
    recBtn.textContent = data.console.recording ? ('● REC ' + data.console.rec_time) : '● REC';

    const list = document.getElementById('cueList');
    if (!data.cues.length) {
      list.innerHTML = '<div style="color:var(--muted);font-size:0.85rem;">No hay cues en la sesión actual.</div>';
    } else {
      list.innerHTML = data.cues.map(c => {
        const cls = c.running ? 'running' : (c.paused ? 'paused' : '');
        const badge = c.running ? '▶ SONANDO' : (c.paused ? '❚❚ PAUSA' : 'LISTO');
        const cid = escHtml(c.id);
        return `<div class="cue-item ${cls}">
          <div class="cue-info">
            <div class="cue-name">${c.index + 1}. ${escHtml(c.name)}</div>
            <div class="cue-time">${badge} • ${fmtTime(c.current_time)} / ${fmtTime(c.duration)}</div>
          </div>
          <div class="cue-btns">
            <button style="background:#007A3D;border-color:#00E676;" onclick="act('cue_execute',{cue_id:'${cid}'})">${c.running ? '❚❚' : '▶'}</button>
            <button style="background:#995800;border-color:#FFB300;" onclick="act('cue_fadeout',{cue_id:'${cid}'})">FADE</button>
            <button style="background:#800A19;border-color:#FF2A4B;" onclick="act('cue_stop',{cue_id:'${cid}'})">■</button>
          </div>
        </div>`;
      }).join('');
    }

    if (data.fx && data.fx.length) {
      document.getElementById('fxSection').style.display = 'block';
      document.getElementById('fxGrid').innerHTML = data.fx.map((p, i) => {
        const col = /^#[0-9a-fA-F]{6}$/.test(p.color || '') ? p.color : '#00D4FF';
        return `<button class="fx-btn" style="border-color:${col};opacity:${p.ready?1:0.45};" onclick="act('fx_trigger',{index:${i}})">${escHtml(p.name)}</button>`;
      }).join('');
    }

    if (data.eq) {
      document.getElementById('eqSection').style.display = 'block';
      if (!eqBusy) {
        eqState = data.eq;
        document.getElementById('eqLow').value = eqState.low;
        document.getElementById('eqMid').value = eqState.mid;
        document.getElementById('eqHigh').value = eqState.high;
      }
      document.getElementById('lblLow').textContent = eqState.kill_low ? 'KILL' : (eqState.low + 'dB');
      document.getElementById('lblMid').textContent = eqState.kill_mid ? 'KILL' : (eqState.mid + 'dB');
      document.getElementById('lblHigh').textContent = eqState.kill_high ? 'KILL' : (eqState.high + 'dB');
      document.getElementById('kLow').className = eqState.kill_low ? 'btn-active' : '';
      document.getElementById('kMid').className = eqState.kill_mid ? 'btn-active' : '';
      document.getElementById('kHigh').className = eqState.kill_high ? 'btn-active' : '';
    }
  } catch (e) {
    document.getElementById('dot').style.background = '#FF2A4B';
  }
}

setInterval(poll, 700);
poll();
</script>
</body>
</html>
"""


def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def render_qr_pixmap(url):
    """Genera un QPixmap con el código QR usando qrcode (multiplataforma) o qrencode."""
    try:
        import qrcode

        qr = qrcode.QRCode(box_size=7, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        pm = QPixmap()
        pm.loadFromData(buf.getvalue(), "PNG")
        return pm
    except Exception:
        pass

    try:
        png_bytes = subprocess.check_output(
            ["qrencode", "-t", "PNG", "-s", "7", "-m", "2", "-o", "-", url],
            timeout=3,
        )
        pm = QPixmap()
        pm.loadFromData(png_bytes, "PNG")
        return pm
    except Exception:
        return None


class RemoteBridge(QObject):
    """Thread-safe bridge from HTTP worker threads to the Qt main thread."""

    action_requested = pyqtSignal(str, dict)


class QrRemoteDialog(QDialog):
    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin
        self.setWindowTitle("DJNova — Control Remoto Web (Celular / Tablet)")
        self.setMinimumWidth(360)

        lay = QVBoxLayout(self)
        lay.setSpacing(10)

        title = QLabel(
            "Escanea este código QR desde tu celular o tablet\n(conectado a la misma red Wi-Fi):",
            self,
        )
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet("font-weight: bold;")
        lay.addWidget(title)

        self.qr_label = QLabel(self)
        self.qr_label.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.qr_label)

        self.pin_lbl = QLabel(self)
        self.pin_lbl.setAlignment(Qt.AlignCenter)
        self.pin_lbl.setStyleSheet(
            "font-family: monospace; font-size: 12pt; font-weight: bold; "
            "color: #00E676; background: #0E1F18; border: 1px solid #00E676; "
            "border-radius: 6px; padding: 5px 10px;"
        )
        lay.addWidget(self.pin_lbl)

        self.url_lbl = QLabel(self)
        self.url_lbl.setAlignment(Qt.AlignCenter)
        self.url_lbl.setOpenExternalLinks(True)
        lay.addWidget(self.url_lbl)

        btns = QHBoxLayout()
        btn_open = QPushButton("Abrir en navegador", self)
        btn_open.clicked.connect(lambda: webbrowser.open(self.plugin.get_access_url()))
        btn_regen = QPushButton("Nuevo PIN", self)
        btn_regen.clicked.connect(self._regen_pin)
        btn_copy = QPushButton("Copiar enlace", self)
        btn_copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self.plugin.get_access_url())
        )
        btn_close = QPushButton("Cerrar", self)
        btn_close.clicked.connect(self.accept)
        btns.addWidget(btn_open)
        btns.addWidget(btn_regen)
        btns.addWidget(btn_copy)
        btns.addWidget(btn_close)
        lay.addLayout(btns)

        self._refresh_qr()

    def _regen_pin(self):
        self.plugin.regenerate_pin()
        self._refresh_qr()

    def _refresh_qr(self):
        url = self.plugin.get_access_url()
        pm = render_qr_pixmap(url)
        if pm is not None and not pm.isNull():
            self.qr_label.setPixmap(pm)
        else:
            self.qr_label.setText("(Instala python-qrcode o qrencode para ver el QR)")

        if self.plugin.pin_enabled:
            self.pin_lbl.setText(f"PIN de Acceso Seguro: {self.plugin.pin}")
            self.pin_lbl.show()
        else:
            self.pin_lbl.hide()

        self.url_lbl.setText(
            f'<a href="{url}" style="color:#00D4FF;font-size:11pt;font-weight:bold;">{url}</a>'
        )


class DjWebRemote(Plugin):
    """Servidor de Control Remoto Web Táctil DJ (Celular / Tablet + QR con PIN)."""

    Name = "Control Remoto Web DJ (Celular / QR)"
    Authors = ("DJNova",)
    Description = (
        "Sirve una consola DJ táctil protegida por PIN en tu red Wi-Fi local (puerto 9095) con código QR "
        "para controlar pistas, GO, Crossfade, Fade All, EQ 3 bandas y Sampler FX desde el celular."
    )
    Version = "1.1.0"

    def __init__(self, app):
        super().__init__(app)
        self.port = int(self.Config.get("port", 9095))
        self.pin_enabled = bool(self.Config.get("pin_enabled", True))
        saved_pin = str(self.Config.get("pin", "") or "").strip()
        if not saved_pin:
            saved_pin = f"{secrets.randbelow(9000) + 1000}"
        self.pin = saved_pin
        self.server = None
        self.thread = None
        self.bridge = RemoteBridge()
        self.bridge.action_requested.connect(self._execute_on_main_thread)

        self._start_server()

        self.action = QAction("Control Remoto Web DJ (Mostrar QR)...", self.app.window.menuTools)
        self.action.triggered.connect(self._show_qr_dialog)
        self.app.window.menuTools.addAction(self.action)

    def regenerate_pin(self):
        self.pin = f"{secrets.randbelow(9000) + 1000}"
        self.Config["pin"] = self.pin
        self.Config.write()

    def get_access_url(self):
        base = f"http://{get_lan_ip()}:{self.port}/"
        if self.pin_enabled and self.pin:
            return f"{base}?pin={urllib.parse.quote(self.pin)}"
        return base

    def is_authorized(self, headers, query_pin=""):
        if not self.pin_enabled:
            return True
        provided = str(headers.get("X-DJNova-Pin") or query_pin or "").strip()
        return bool(provided) and hmac.compare_digest(provided, self.pin)

    def _collect_state(self):
        cues_out = []
        layout = self.app.layout if self.app.session is not None else None
        if layout is not None:
            for idx, cue in enumerate(layout.cues()):
                try:
                    cues_out.append({
                        "id": str(cue.id),
                        "index": idx,
                        "name": cue.name or f"Cue {idx + 1}",
                        "running": bool(cue.state & CueState.Running),
                        "paused": bool(cue.state & CueState.Pause),
                        "duration": int(cue.duration or 0),
                        "current_time": int(cue.current_time() or 0),
                    })
                except Exception:
                    pass

        console_info = {"dim": False, "mute": False, "recording": False, "rec_time": "00:00"}
        clock_str = ""
        try:
            djc = plugins.get_plugin("DjConsole")
            if djc and djc.console_widget:
                w = djc.console_widget
                clock_str = w.clockLabel.text()
                console_info = {
                    "dim": bool(w._dim_active),
                    "mute": bool(w._mute_active),
                    "recording": bool(getattr(w, "_is_recording", False)),
                    "rec_time": getattr(w, "_rec_elapsed_str", lambda: "00:00")(),
                }
        except Exception:
            pass

        eq_info = None
        try:
            eqp = plugins.get_plugin("DjEqMixer")
            if eqp and eqp.eq_widget:
                eq_info = eqp.eq_widget.get_state()
        except Exception:
            pass

        fx_info = []
        try:
            smp = plugins.get_plugin("DjSampler")
            if smp and smp.sampler_widget:
                for p in smp.sampler_widget.pads:
                    fx_info.append({
                        "name": p.pad_data.get("name", "FX"),
                        "color": p.pad_data.get("color", "#00D4FF"),
                        "ready": bool(p.pad_data.get("path")),
                    })
        except Exception:
            pass

        return {
            "clock": clock_str,
            "console": console_info,
            "cues": cues_out,
            "eq": eq_info,
            "fx": fx_info,
        }

    def _execute_on_main_thread(self, action, payload):
        if action not in ALLOWED_ACTIONS:
            return
        layout = self.app.layout if self.app.session is not None else None
        try:
            if action == "go" and layout:
                layout.go()
            elif action == "fade_all" and layout:
                layout.fadeout_all()
            elif action == "pause_all" and layout:
                layout.pause_all()
            elif action == "resume_all" and layout:
                layout.resume_all()
            elif action == "stop_all" and layout:
                layout.stop_all()
            elif action == "crossfade":
                djc = plugins.get_plugin("DjConsole")
                if djc and djc.console_widget:
                    djc.console_widget._on_crossfade()
            elif action == "toggle_dim":
                djc = plugins.get_plugin("DjConsole")
                if djc and djc.console_widget:
                    djc.console_widget.btnDim.toggle()
            elif action == "toggle_mute":
                djc = plugins.get_plugin("DjConsole")
                if djc and djc.console_widget:
                    djc.console_widget.btnMute.toggle()
            elif action == "toggle_rec":
                djc = plugins.get_plugin("DjConsole")
                if djc and djc.console_widget and hasattr(djc.console_widget, "btnRec"):
                    djc.console_widget.btnRec.toggle()
            elif action in ("cue_execute", "cue_stop", "cue_fadeout"):
                cue = self.app.cue_model.get(str(payload.get("cue_id", "")))
                if cue is not None:
                    if action == "cue_execute":
                        cue.execute()
                    elif action == "cue_stop":
                        cue.stop(fade=False)
                    elif action == "cue_fadeout":
                        cue.stop(fade=True)
            elif action == "eq_set":
                eqp = plugins.get_plugin("DjEqMixer")
                if eqp and eqp.eq_widget:
                    def _clamp_eq(val):
                        if val is None:
                            return None
                        return max(-24, min(12, int(val)))

                    eqp.eq_widget.set_state(
                        low=_clamp_eq(payload.get("low")),
                        mid=_clamp_eq(payload.get("mid")),
                        high=_clamp_eq(payload.get("high")),
                        kill_low=bool(payload["kill_low"]) if "kill_low" in payload else None,
                        kill_mid=bool(payload["kill_mid"]) if "kill_mid" in payload else None,
                        kill_high=bool(payload["kill_high"]) if "kill_high" in payload else None,
                    )
            elif action == "fx_trigger":
                idx = int(payload.get("index", -1))
                smp = plugins.get_plugin("DjSampler")
                if smp and smp.sampler_widget and 0 <= idx < len(smp.sampler_widget.pads):
                    smp.sampler_widget.pads[idx].trigger_pad()
        except Exception:
            logger.exception(f"Error ejecutando acción remota '{action}'")

    def _start_server(self):
        plugin = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def _send(self, code, content_type, body):
                raw = body.encode("utf-8") if isinstance(body, str) else body
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                self.wfile.write(raw)

            def _query_pin(self):
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                return (qs.get("pin") or [""])[0]

            def do_GET(self):
                path_only = urllib.parse.urlparse(self.path).path
                if path_only == "/":
                    self._send(200, "text/html; charset=utf-8", REMOTE_HTML)
                elif path_only == "/api/state":
                    if not plugin.is_authorized(self.headers, self._query_pin()):
                        self._send(401, "application/json", '{"ok":false,"error":"PIN requerido"}')
                        return
                    data = plugin._collect_state()
                    self._send(200, "application/json; charset=utf-8", json.dumps(data))
                else:
                    self._send(404, "text/plain", "Not Found")

            def do_POST(self):
                path_only = urllib.parse.urlparse(self.path).path
                if path_only != "/api/action":
                    self._send(404, "text/plain", "Not Found")
                    return
                if not plugin.is_authorized(self.headers, self._query_pin()):
                    self._send(401, "application/json", '{"ok":false,"error":"PIN requerido"}')
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self._send(400, "application/json", '{"ok":false,"error":"Content-Length inválido"}')
                    return
                if length <= 0 or length > MAX_BODY_BYTES:
                    self._send(413, "application/json", '{"ok":false,"error":"Payload excede límite"}')
                    return
                raw = self.rfile.read(length).decode("utf-8", "replace")
                try:
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError("JSON debe ser un objeto")
                    action = str(payload.get("action", ""))
                    if action not in ALLOWED_ACTIONS:
                        self._send(400, "application/json", '{"ok":false,"error":"Acción no permitida"}')
                        return
                    plugin.bridge.action_requested.emit(action, payload)
                    self._send(200, "application/json", '{"ok":true}')
                except Exception as e:
                    self._send(400, "application/json", json.dumps({"ok": False, "error": str(e)}))

        try:
            ThreadingHTTPServer.allow_reuse_address = True
            self.server = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
            logger.info("Control Remoto Web DJ iniciado en %s", self.get_access_url())
        except Exception as e:
            logger.warning("No se pudo iniciar servidor Control Remoto Web en puerto %s: %s", self.port, e)

    def _show_qr_dialog(self):
        dlg = QrRemoteDialog(self, self.app.window)
        dlg.exec_()

    def finalize(self):
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
        if self.action is not None:
            self.app.window.menuTools.removeAction(self.action)
        super().finalize()
