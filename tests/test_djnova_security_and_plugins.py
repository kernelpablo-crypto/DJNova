# -*- coding: utf-8 -*-
"""Suite automatizada de pruebas de seguridad, portabilidad y plugins DJNova."""
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

import pytest
from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtGui import QKeyEvent
from PyQt5.QtWidgets import QApplication, QLineEdit, QMainWindow, QMenu, QPushButton, QVBoxLayout, QWidget

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lisp.core import djnova_platform as dp
from lisp.plugins.dj_autodj.dj_autodj import AutoDjWidget
from lisp.plugins.dj_console.dj_console import DimKeyFilter
from lisp.plugins.dj_sampler.dj_sampler import resolve_pad_path
from lisp.plugins.dj_video_stage.dj_video_stage import get_cue_video_path
from lisp.plugins.dj_web_remote.dj_web_remote import DjWebRemote, REMOTE_HTML


@pytest.fixture(scope="session")
def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    return app


def test_repo_hygiene_no_personal_paths_or_shell_true():
    """Garantiza que no haya rutas personales ni /tmp inseguro ni shell=True en los módulos DJNova."""
    for ext in ("*.py", "*.json"):
        for fp in glob.glob(os.path.join(ROOT, "lisp", "**", ext), recursive=True):
            txt = open(fp, encoding="utf-8", errors="ignore").read()
            assert "/home/pablo" not in txt, f"Ruta personal encontrada en {fp}"
            assert "/tmp/lisp_" not in txt, f"Ruta /tmp insegura encontrada en {fp}"
            if f"{os.sep}dj_" in fp or "djnova_platform.py" in fp:
                assert "shell=True" not in txt, f"Uso inseguro de shell=True en {fp}"


def test_url_and_secret_security_helpers():
    assert dp.validate_http_url("https://youtube.com/watch?v=123") == "https://youtube.com/watch?v=123"
    assert dp.validate_http_url("--exec=rm -rf") is None
    assert dp.validate_http_url("-o /etc/passwd") is None
    assert dp.validate_http_url("javascript:alert(1)") is None
    assert dp.validate_http_url("https://a.com/\nfoo") is None

    assert dp.validate_rtmp_url("rtmp://a.rtmp.youtube.com/live2/abc") is not None
    assert dp.validate_rtmp_url("rtmps://live-api-s.facebook.com:443/rtmp/xyz") is not None
    assert dp.validate_rtmp_url("rtmp://evil.com/live|exec") is None
    assert dp.validate_rtmp_url("rtmp://evil.com/live[f=flv]") is None
    assert dp.validate_rtmp_url("http://not-rtmp.com") is None

    assert "secret123" not in dp.mask_secret("rtmp://host/secret123", "secret123")
    esc = dp.escape_ffmpeg_drawtext_path(r"C:\Users\DJ\now's.txt")
    assert r"\:" in esc and r"\'" in esc and "\\" not in esc.replace(r"\:", "").replace(r"\'", "")

    rt = dp.get_private_runtime_dir()
    assert os.path.isdir(rt)
    if not dp.IS_WINDOWS:
        assert (os.stat(rt).st_mode & 0o777) == 0o700


def test_bundled_sfx_resolution():
    for i, fname in enumerate(
        [
            "01_Bocina_DJ.mp3",
            "02_Aplausos.mp3",
            "03_Redoble.mp3",
            "04_Fanfarria.mp3",
            "05_Scratch_DJ.mp3",
            "06_Risas.mp3",
            "07_Fail_Trombon.mp3",
            "08_Sirena_FX.mp3",
        ],
        1,
    ):
        resolved = resolve_pad_path(fname)
        assert resolved and os.path.isfile(resolved), f"Falta SFX #{i}: {fname}"


def test_dim_key_filter_blocks_typing_context(qapp):
    win = QMainWindow()
    c = QWidget()
    lay = QVBoxLayout(c)
    btn = QPushButton("DIM [0]")
    btn.setCheckable(True)
    le = QLineEdit()
    lay.addWidget(btn)
    lay.addWidget(le)
    win.setCentralWidget(c)
    win.show()

    class FakeConsole:
        btnDim = btn

    flt = DimKeyFilter(FakeConsole(), win)
    c.setFocus()
    qapp.processEvents()
    assert flt.eventFilter(c, QKeyEvent(QEvent.KeyPress, Qt.Key_0, Qt.NoModifier, "0")) is True
    assert btn.isChecked() is True
    assert flt.eventFilter(c, QKeyEvent(QEvent.KeyPress, Qt.Key_0, Qt.NoModifier, "0")) is True
    assert btn.isChecked() is False

    le.setFocus()
    qapp.processEvents()
    assert flt.eventFilter(le, QKeyEvent(QEvent.KeyPress, Qt.Key_0, Qt.NoModifier, "0")) is False
    assert btn.isChecked() is False


def test_autodj_ghost_eos_does_not_stop_active_track(qapp):
    class FakeApp:
        session = None
        layout = None
        class session_created:
            @staticmethod
            def connect(_cb): pass

    class FakePlugin:
        app = FakeApp()
        Config = {"crossfade_sec": 3.0, "loop": False, "write": lambda: None}

    class MockSig:
        def __init__(self):
            self.cbs = []
        def connect(self, cb):
            self.cbs.append(cb)
        def emit(self, *a, **kw):
            for cb in list(self.cbs):
                cb(*a, **kw)

    class MockCue:
        def __init__(self, cid, name):
            self.id = cid
            self.name = name
            self.duration = 10000
            self._cur = 0
            self.current_time = lambda: self._cur
            self.state = 0
            self.started = False
            self.stopped_count = 0
            self.end = MockSig()
            self.stopped = MockSig()
        def start(self): self.started = True; self.state = 1
        def stop(self): self.stopped_count += 1; self.state = 0
        def fadeout(self, *_): pass
        def fadein(self, *_): pass

    w = AutoDjWidget(FakePlugin())
    c1, c2 = MockCue("c1", "Track 1"), MockCue("c2", "Track 2")
    w.add_cues([c1, c2])
    w.play_index(0, crossfade=False)

    # Dispara crossfade en _tick()
    c1._cur = 8500
    w._tick()
    assert w.current_cue is c2

    # Señal end tardía de Track 1 debe ignorarse por completo
    c1.end.emit(c1)
    time.sleep(0.05)
    qapp.processEvents()
    assert w.is_active is True
    assert w.current_cue is c2
    assert c2.stopped_count == 0


def test_web_remote_pin_auth_and_xss_protection(qapp):
    assert "escHtml(" in REMOTE_HTML

    class FakeWin:
        menuTools = QMenu()

    class FakeApp:
        session = None
        layout = None
        window = FakeWin()

    class Cfg(dict):
        def write(self): pass

    orig_cfg = DjWebRemote.Config
    DjWebRemote.Config = Cfg({"port": 0, "pin_enabled": True, "pin": "4821"})
    try:
        # Encontrar puerto libre
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        DjWebRemote.Config["port"] = port

        plugin = DjWebRemote(FakeApp())
        base = f"http://127.0.0.1:{port}"

        # 1. Sin PIN -> 401
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{base}/api/state", timeout=2)
        assert exc.value.code == 401

        # 2. PIN incorrecto -> 401
        req_bad = urllib.request.Request(f"{base}/api/state", headers={"X-DJNova-Pin": "0000"})
        with pytest.raises(urllib.error.HTTPError) as exc2:
            urllib.request.urlopen(req_bad, timeout=2)
        assert exc2.value.code == 401

        # 3. PIN correcto -> 200 JSON
        req_ok = urllib.request.Request(f"{base}/api/state", headers={"X-DJNova-Pin": "4821"})
        with urllib.request.urlopen(req_ok, timeout=2) as resp:
            assert resp.status == 200
            data = json.loads(resp.read().decode())
            assert "cues" in data

        # 4. Acción prohibida con PIN válido -> 400
        req_act = urllib.request.Request(
            f"{base}/api/action",
            data=json.dumps({"action": "rm_rf"}).encode(),
            headers={"Content-Type": "application/json", "X-DJNova-Pin": "4821"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc3:
            urllib.request.urlopen(req_act, timeout=2)
        assert exc3.value.code == 400

        plugin.finalize()
    finally:
        DjWebRemote.Config = orig_cfg


def test_video_stage_detects_mp4_cue_and_ignores_mp3(tmp_path, qapp):
    import gc
    from PyQt5.QtWidgets import QMainWindow
    from lisp.cues.cue_model import CueModel
    from lisp.cues.media_cue import MediaCue
    from lisp.plugins.dj_video_stage.dj_video_stage import DjVideoStage

    mp4 = tmp_path / "show_clip.mp4"
    mp4.write_bytes(b"fake mp4")
    mp3 = tmp_path / "song.mp3"
    mp3.write_bytes(b"fake mp3")

    class FakeUri:
        def __init__(self, p):
            self.absolute_path = str(p)

    class FakeSig:
        def connect(self, *_): pass

    class FakeMedia:
        elements_changed = FakeSig()
        error = FakeSig()
        eos = FakeSig()
        def __init__(self, p):
            self._p = p
        def changed(self, _):
            return FakeSig()
        def element(self, _):
            return None
        def input_uri(self):
            return FakeUri(self._p)

    cue_mp4 = MediaCue(None, FakeMedia(mp4))
    cue_mp3 = MediaCue(None, FakeMedia(mp3))
    assert get_cue_video_path(cue_mp4) == str(mp4)
    assert get_cue_video_path(cue_mp3) == ""
    assert get_cue_video_path(object()) == ""

    # Verificar que Signal (weakref) NO pierda la conexión tras gc.collect()
    win = QMainWindow()
    class FakeApp:
        window = win
        cue_model = CueModel()

    class Cfg(dict):
        def write(self): pass

    orig_cfg = DjVideoStage.Config
    DjVideoStage.Config = Cfg({"auto_play_cue_videos": True, "videos": [], "standby_image": "", "loop_videos": False, "mute_video": False, "volume": 100})
    triggered = []
    try:
        plugin = DjVideoStage(FakeApp())
        plugin.dialog = type("FakeDlg", (), {
            "sync_play_cue_video": lambda _s, c, p: triggered.append((c, p)),
            "sync_pause_cue": lambda *_a: None,
            "sync_stop_cue": lambda *_a: None,
            "shutdown": lambda *_a: None,
            "close": lambda *_a: None,
        })()
        FakeApp.cue_model.add(cue_mp4)
        gc.collect()
        cue_mp4.started.emit(cue_mp4)
        qapp.processEvents()
        assert len(triggered) == 1 and triggered[0][1] == str(mp4)
        plugin.finalize()
    finally:
        DjVideoStage.Config = orig_cfg
