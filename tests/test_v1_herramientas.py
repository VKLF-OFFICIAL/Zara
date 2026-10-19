"""Regresiones de la auditoría v1, grupo «herramientas» (core/tools.py).

Las ramas de Windows se cubren simulando la plataforma: se sustituye el `sys` que ve
core.tools por un objeto con platform="win32" (el resto del proceso sigue en Linux) y
se interceptan subprocess/psutil, de modo que nada se ejecuta de verdad.
"""

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

import config
import core.tools as tools


@pytest.fixture
def ask_no(monkeypatch):
    """Manejador de confirmación que siempre responde «no» y apunta lo que se preguntó."""
    asked = []
    tools.set_confirmation_handler(lambda d: asked.append(d) or False)
    yield asked
    tools.set_confirmation_handler(None)


def _fake_platform(monkeypatch, platform):
    monkeypatch.setattr(tools, "sys", SimpleNamespace(platform=platform))


# --- CER-1 / SEG-1: normalizar ANTES de pedir confirmación ------------------------

def test_alias_delete_pide_confirmacion_y_no_borra(tmp_path, ask_no):
    carpeta = tmp_path / "proyectos"
    carpeta.mkdir()
    (carpeta / "a.txt").write_text("x", encoding="utf-8")

    res = tools.execute_tool("delete", {"path": str(carpeta)})
    assert res["blocked"] is True and ask_no
    assert (carpeta / "a.txt").exists()


def test_accion_con_espacios_y_mayusculas_pide_confirmacion(tmp_path, ask_no):
    carpeta = tmp_path / "web"
    carpeta.mkdir()
    for action in ("delete ", " Delete", "DELETE"):
        res = tools.execute_tool("file_manager", {"action": action, "path": str(carpeta)})
        assert res["blocked"] is True
        assert carpeta.exists()
    assert len(ask_no) == 3


def test_nombre_con_espacios_pide_confirmacion(tmp_path, ask_no):
    victima = tmp_path / "v.txt"
    victima.write_text("x", encoding="utf-8")
    res = tools.execute_tool(" run_shell ", {"command": f"rm -f {victima}"})
    assert res["blocked"] is True and ask_no
    assert victima.exists()


def test_alias_close_app_pide_confirmacion(monkeypatch, ask_no):
    def no_tocar(*a, **k):
        raise AssertionError("no debía enumerar ni cerrar procesos")

    monkeypatch.setattr(tools.psutil, "process_iter", no_tocar)
    res = tools.execute_tool("close_app", {"target": "firefox"})
    assert res["blocked"] is True
    assert ask_no == ["cerrar la aplicación firefox"]


def test_needs_confirmation_tolera_args_none():
    assert tools.needs_confirmation("run_shell", None) is None
    assert tools.needs_confirmation(None, None) is None


# --- CER-2 / SEG-12: close_app con objetivo vacío o corto ---------------------------

class _FakeProc:
    def __init__(self, pid, name, exe=""):
        self.info = {"pid": pid, "name": name, "exe": exe}
        self.terminated = False

    def terminate(self):
        self.terminated = True


def _fake_processes(monkeypatch, procs):
    monkeypatch.setattr(tools.psutil, "process_iter", lambda attrs=None: list(procs))


@pytest.mark.parametrize("target", ["", "   ", "e", "ab", ".exe"])
def test_close_app_rechaza_objetivo_vacio_o_corto(monkeypatch, target):
    procs = [_FakeProc(10, "gnome-shell"), _FakeProc(11, "firefox"), _FakeProc(12, "pipewire")]
    _fake_processes(monkeypatch, procs)
    res = tools.os_control("close_app", target=target)
    assert res["success"] is False
    assert not any(p.terminated for p in procs)


def test_close_app_compara_nombres_exactos_y_protege_sistema_y_zara(monkeypatch):
    procs = [
        _FakeProc(os.getpid(), "python"),          # la propia Zara
        _FakeProc(20, "python"),
        _FakeProc(21, "python-helper-daemon"),     # subcadena: no se toca
        _FakeProc(22, "gnome-shell"),
        _FakeProc(23, None),                       # nombre desconocido: no rompe el bucle
        _FakeProc(24, "gnome-text-edit", "/usr/bin/gnome-text-editor"),  # nombre truncado (Linux)
    ]
    _fake_processes(monkeypatch, procs)
    res = tools.os_control("close_app", target="python")
    assert res["success"] is True
    assert [p.info["pid"] for p in procs if p.terminated] == [20]

    res = tools.os_control("close_app", target="gnome-text-editor")
    assert res["success"] is True and procs[5].terminated

    assert tools.os_control("close_app", target="gnome-shell")["success"] is False
    assert not procs[3].terminated


# --- CER-3: borrar un enlace no borra su destino ---------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="crear symlinks en Windows requiere permisos")
def test_delete_enlace_a_carpeta_no_toca_el_destino(tmp_path):
    real = tmp_path / "storage" / "app" / "public"
    real.mkdir(parents=True)
    (real / "keep.txt").write_text("subida", encoding="utf-8")
    enlace = tmp_path / "public_storage"
    enlace.symlink_to(real, target_is_directory=True)

    assert "enlace" in tools.needs_confirmation("file_manager", {"action": "delete", "path": str(enlace)})
    res = tools.file_manager("delete", str(enlace))
    assert res["success"] is True
    assert not enlace.is_symlink() and not enlace.exists()
    assert (real / "keep.txt").read_text(encoding="utf-8") == "subida"


@pytest.mark.skipif(sys.platform == "win32", reason="crear symlinks en Windows requiere permisos")
def test_delete_enlace_a_archivo_no_toca_el_destino(tmp_path):
    real = tmp_path / "datos.txt"
    real.write_text("importante", encoding="utf-8")
    enlace = tmp_path / "acceso.txt"
    enlace.symlink_to(real)
    assert tools.file_manager("delete", str(enlace))["success"] is True
    assert real.read_text(encoding="utf-8") == "importante"


# --- CER-4 / SEG-11 / SEG-9: invoke_claude_code -------------------------------------

@pytest.fixture
def claude_falso(monkeypatch):
    llamadas = []
    real_which = tools.shutil.which
    monkeypatch.setattr(tools.shutil, "which", lambda n: "/usr/bin/claude" if n == "claude" else real_which(n))

    class FakePopen:
        def __init__(self, cmd, **kw):
            llamadas.append((cmd, kw.get("cwd")))
            self.pid, self.returncode = 4_000_000, 0

        def communicate(self, timeout=None):
            return "hecho", ""

    def fake_run(cmd, **kw):
        llamadas.append((cmd, kw.get("cwd")))
        return SimpleNamespace(returncode=0, stdout="hecho", stderr="")

    monkeypatch.setattr(tools.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    return llamadas


@pytest.mark.parametrize("ruta", ["", None, "/no/existe/zara_proyecto_xyz"])
def test_claude_code_rechaza_ruta_vacia_o_inexistente(claude_falso, ruta):
    res = tools.invoke_claude_code(ruta, "crea una web")
    assert res["success"] is False
    assert claude_falso == []  # nunca se vuelve al cwd (el repositorio de Zara)


def test_claude_code_rechaza_la_carpeta_de_zara_y_las_protegidas(claude_falso):
    for ruta in (config.BASE_DIR, config.BASE_DIR / "core", config.BASE_DIR.parent, Path.home(), "/etc"):
        res = tools.invoke_claude_code(str(ruta), "quita las comprobaciones de core/tools.py")
        assert res["success"] is False, ruta
    assert claude_falso == []


def test_claude_code_expande_rutas_y_describe_carpeta_y_tarea(tmp_path, monkeypatch, claude_falso):
    proyecto = tmp_path / "miweb"
    proyecto.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    args = {"project_path": "~/miweb", "instruction": "crea una web Laravel con login"}
    descripcion = tools.needs_confirmation("invoke_claude_code", args)
    assert str(proyecto) in descripcion and "crea una web Laravel con login" in descripcion

    res = tools.execute_tool("invoke_claude_code", args)
    assert res["success"] is True
    assert claude_falso[0][1] == str(proyecto.resolve())


def test_descripcion_de_confirmacion_no_recorta_comandos_largos():
    cmd = 'echo "' + "Actualizando la caché de iconos del escritorio " * 3 + '"; rm -rf ~/Documentos'
    descripcion = tools.needs_confirmation("run_shell", {"command": cmd})
    assert "rm -rf ~/Documentos" in descripcion
    assert "panel" in descripcion


# --- CER-10 / MUL-3 / MUL-6: árbol de procesos y UTF-8 ------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="usa bash")
def test_timeout_mata_tambien_a_los_nietos(tmp_path):
    t0 = time.monotonic()
    res = tools.run_shell("sleep 47 & echo $! > nieto.pid; wait", timeout=1, cwd=str(tmp_path))
    assert res["success"] is False and res["exit_code"] == 124
    assert time.monotonic() - t0 < 15
    pid = int((tmp_path / "nieto.pid").read_text().strip())
    deadline = time.monotonic() + 5
    vivo = True
    while time.monotonic() < deadline:
        try:
            vivo = psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            vivo = False
        if not vivo:
            break
        time.sleep(0.1)
    assert not vivo, "el proceso nieto sigue vivo tras el timeout"


class _FakePopen:
    instancias = []

    def __init__(self, cmd, **kwargs):
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 4_000_000  # no existe: psutil.Process lanzará NoSuchProcess
        self.returncode = None
        self.killed = False
        self._llamadas = 0
        _FakePopen.instancias.append(self)

    def communicate(self, timeout=None):
        self._llamadas += 1
        if self._llamadas == 1:
            raise tools.subprocess.TimeoutExpired(self.cmd, timeout)
        return "parcial", ""

    def kill(self):
        self.killed = True


def test_windows_timeout_usa_grupo_propio_taskkill_y_utf8(tmp_path, monkeypatch):
    _fake_platform(monkeypatch, "win32")
    _FakePopen.instancias = []
    runs = []
    monkeypatch.setattr(tools.subprocess, "Popen", _FakePopen)
    monkeypatch.setattr(tools.subprocess, "run", lambda cmd, **kw: runs.append((cmd, kw)) or SimpleNamespace(returncode=0))

    res = tools.run_shell("php artisan serve", timeout=2, cwd=str(tmp_path))

    assert res["success"] is False and res["exit_code"] == 124
    proc = _FakePopen.instancias[0]
    assert proc.kwargs["creationflags"] & tools._WIN_NEW_GROUP
    assert proc.kwargs["encoding"] == "utf-8"
    assert proc.cmd[-1].startswith("try { [Console]::OutputEncoding")
    assert proc.cmd[-1].endswith("php artisan serve")
    assert ["taskkill", "/T", "/F", "/PID", str(proc.pid)] in [c for c, _ in runs]
    assert proc.killed


def test_windows_background_se_desvincula_de_la_consola(tmp_path, monkeypatch):
    _fake_platform(monkeypatch, "win32")
    _FakePopen.instancias = []
    monkeypatch.setattr(tools.subprocess, "Popen", _FakePopen)
    res = tools.run_shell("php artisan serve", background=True, cwd=str(tmp_path))
    assert res["success"] is True
    kwargs = _FakePopen.instancias[0].kwargs
    assert kwargs["creationflags"] == tools._WIN_NEW_GROUP | tools._WIN_NO_WINDOW
    assert "start_new_session" not in kwargs


def test_windows_get_clipboard_en_utf8(monkeypatch):
    _fake_platform(monkeypatch, "win32")
    runs = []

    def fake_run(cmd, **kw):
        runs.append((cmd, kw))
        return SimpleNamespace(returncode=0, stdout="Diseño\r\n", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    res = tools.os_control("read_clipboard")
    assert res["clipboard"] == "Diseño"
    cmd, kw = runs[0]
    assert "OutputEncoding" in cmd[-1] and cmd[-1].endswith("Get-Clipboard")
    assert kw["encoding"] == "utf-8"


# --- SEG-4: sin inyección de PowerShell con comillas tipográficas -------------------

def test_windows_portapapeles_y_notificacion_no_interpolan_texto(monkeypatch):
    _fake_platform(monkeypatch, "win32")
    malicioso = "hola’; Start-Process calc; ’"
    runs, popens = [], []
    monkeypatch.setattr(
        tools.subprocess, "run",
        lambda cmd, **kw: runs.append((cmd, kw)) or SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    monkeypatch.setattr(tools.subprocess, "Popen", lambda cmd, **kw: popens.append((cmd, kw)))

    assert tools.os_control("write_clipboard", value=malicioso)["success"] is True
    assert tools.os_control("notify", value=malicioso)["success"] is True
    for cmd, kw in runs + popens:
        assert "Start-Process" not in " ".join(cmd)
        assert kw["env"]["ZARA_TEXT"] == malicioso


# --- SEG-5 / SEG-7: el propio Zara y los secretos ----------------------------------

def test_no_se_puede_escribir_en_la_carpeta_de_zara():
    destino = config.BASE_DIR / "zara_prueba_protegida.tmp"
    try:
        for action in ("write", "append"):
            res = tools.file_manager(action, str(destino), content='OLLAMA_HOST="http://atacante"')
            assert res.get("blocked") is True
        assert tools.file_manager("edit", str(config.BASE_DIR / "config.py"), old_str="BASE_DIR", new_str="X")["blocked"]
        assert not destino.exists()
    finally:
        if destino.exists():
            destino.unlink()


def test_leer_secretos_pide_confirmacion(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DB_PASSWORD=secreto\n", encoding="utf-8")
    assert tools.needs_confirmation("file_manager", {"action": "read", "path": str(env)})
    clave = str(Path.home() / ".ssh" / "id_ed25519")
    assert tools.needs_confirmation("file_manager", {"action": "read", "path": clave})
    # Las plantillas y el código normal no piden nada
    assert tools.needs_confirmation("file_manager", {"action": "read", "path": str(tmp_path / ".env.example")}) is None
    assert tools.needs_confirmation("file_manager", {"action": "read", "path": str(tmp_path / "index.php")}) is None


def test_search_en_carpeta_no_lee_los_env(tmp_path):
    (tmp_path / ".env").write_text("DB_PASSWORD=secreto\n", encoding="utf-8")
    (tmp_path / "config.php").write_text("'password' => env('DB_PASSWORD'),\n", encoding="utf-8")
    res = tools.file_manager("search", str(tmp_path), pattern="DB_PASSWORD")
    assert res["count"] == 1 and "config.php" in res["matches"][0]
    assert not any("secreto" in m for m in res["matches"])


def test_modificar_archivos_de_arranque_pide_confirmacion():
    for ruta in ("~/.bashrc", "~/.config/autostart/x.desktop"):
        assert "iniciar sesión" in tools.needs_confirmation("file_manager", {"action": "append", "path": ruta})


# --- SEG-8: borrar un ANCESTRO de una ruta protegida ---------------------------------

def test_delete_bloquea_ancestros_de_rutas_protegidas(tmp_path, monkeypatch):
    home = tmp_path / "Users" / "vk"
    (home / "Documentos").mkdir(parents=True)
    monkeypatch.setattr(tools, "PROTECTED_EXACT", [str(home)])
    monkeypatch.setattr(tools, "PROTECTED_TREES", [])
    res = tools.file_manager("delete", str(tmp_path / "Users"))  # como C:\Users o /home
    assert res.get("blocked") is True and home.exists()
    assert tools.is_path_protected(config.BASE_DIR.parent, ancestors=True)


def test_delete_bloquea_puntos_de_montaje_y_sus_padres(tmp_path, monkeypatch):
    usb = tmp_path / "media" / "vk" / "USB"
    usb.mkdir(parents=True)
    monkeypatch.setattr(tools, "_mount_points", lambda: [usb])
    assert tools.file_manager("delete", str(tmp_path / "media"))["blocked"] is True
    real_ismount = os.path.ismount
    monkeypatch.setattr(tools.os.path, "ismount", lambda p: Path(p) == usb or real_ismount(p))
    assert tools.file_manager("delete", str(usb))["blocked"] is True
    assert usb.exists()


def test_delete_con_ruta_vacia_no_borra_la_carpeta_de_trabajo(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "trabajo.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv("ZARA_WORKSPACE", str(ws))
    for ruta in ("", "   ", None):
        assert tools.file_manager("delete", ruta)["success"] is False
    assert (ws / "trabajo.txt").exists()


def test_no_se_borran_las_carpetas_personales(tmp_path, monkeypatch):
    docs = tmp_path / "Documentos"
    docs.mkdir()
    monkeypatch.setattr(tools, "user_folders", lambda: {"home": tmp_path, "documentos": docs})
    assert tools.file_manager("delete", str(docs))["blocked"] is True
    assert tools.file_manager("mkdir", str(docs))["success"] is True  # crear una carpeta existente no destruye nada
    assert docs.exists()


# --- CER-11: tipos laxos del LLM ----------------------------------------------------

@pytest.mark.parametrize("name,args", [
    ("web_search", {"query": None}),
    ("os_control", {"action": None}),
    ("os_control", {"action": "open_app", "target": None}),
    ("file_manager", {"action": None, "path": None}),
    ("invoke_claude_code", {"project_path": None, "instruction": None}),
    ("inspect_screen", None),
])
def test_argumentos_null_no_tumban_el_turno(name, args, monkeypatch):
    monkeypatch.setattr(tools, "inspect_screen", lambda prompt="": {"success": False, "error": "sin pantalla"})
    res = tools.execute_tool(name, args)
    assert isinstance(res, dict) and "success" in res


def test_volumen_cero_es_valido(monkeypatch):
    _fake_platform(monkeypatch, "linux")
    vistos = []
    monkeypatch.setattr(tools, "_set_linux_volume", lambda pct: vistos.append(pct) or (True, "ok"))
    res = tools.execute_tool("os_control", {"action": "set_volume", "value": 0})
    assert res["success"] is True and vistos == [0.0]


def test_contenido_json_como_objeto_se_escribe_como_json(tmp_path):
    destino = tmp_path / "package.json"
    res = tools.execute_tool("file_manager", {"action": "write", "path": str(destino), "content": {"name": "web"}})
    assert res["success"] is True
    assert '"name": "web"' in destino.read_text(encoding="utf-8")


# --- CER-5 (resto): edit conserva CRLF y no corrompe archivos no UTF-8 ---------------

def test_edit_conserva_crlf(tmp_path):
    f = tmp_path / "web.php"
    f.write_bytes(b"<?php\r\nRoute::get('/a');\r\nRoute::get('/b');\r\n")
    res = tools.file_manager("edit", str(f), old_str="Route::get('/a');\nRoute::get('/b');",
                             new_str="Route::get('/a');\nRoute::get('/c');")
    assert res["success"] is True
    assert f.read_bytes() == b"<?php\r\nRoute::get('/a');\r\nRoute::get('/c');\r\n"


def test_edit_rechaza_archivo_no_utf8_sin_tocarlo(tmp_path):
    f = tmp_path / "viejo.txt"
    original = "Diseño: año\n".encode("cp1252")
    f.write_bytes(original)
    res = tools.file_manager("edit", str(f), old_str="Dise", new_str="Plan")
    assert res["success"] is False
    assert f.read_bytes() == original


# --- CER-15 / MUL-10 / MUL-11: Linux sin efecto real -> success False -------------

def _which_only(monkeypatch, available):
    monkeypatch.setattr(tools.shutil, "which", lambda n: f"/usr/bin/{n}" if n in available else None)


def _run_returning(monkeypatch, codes, calls):
    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=codes.get(cmd[0], 0), stdout="", stderr="")

    monkeypatch.setattr(tools.subprocess, "run", fake_run)


def test_linux_open_app_calculadora_usa_alias_y_comprueba_gtk_launch(monkeypatch):
    _fake_platform(monkeypatch, "linux")
    _which_only(monkeypatch, {"gtk-launch", "gnome-calculator"})
    calls, popens = [], []
    _run_returning(monkeypatch, {"gtk-launch": 1}, calls)

    class P:
        def __init__(self, cmd, **kw):
            popens.append(cmd)

        def wait(self, timeout=None):
            raise tools.subprocess.TimeoutExpired("x", timeout)

    monkeypatch.setattr(tools.subprocess, "Popen", P)
    res = tools.os_control("open_app", target="calculadora")
    assert res["success"] is True
    assert ["gtk-launch", "calculadora"] in calls
    assert popens == [["/usr/bin/gnome-calculator"]]


def test_linux_open_app_inexistente_o_apagado_no_finge_exito(monkeypatch):
    _fake_platform(monkeypatch, "linux")
    _which_only(monkeypatch, {"gtk-launch", "xdg-open", "reboot"})
    calls = []
    _run_returning(monkeypatch, {"gtk-launch": 1}, calls)
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **k: pytest.fail("no debía lanzar nada"))
    assert tools.os_control("open_app", target="aplicacion_inexistente_zzz")["success"] is False
    assert tools.os_control("open_app", target="reboot")["success"] is False


@pytest.mark.parametrize("target", [r"C:\Users\vk\AppData\Local\Temp\a.bat", "~/.local/share/applications/zz.desktop", "x.ps1"])
def test_open_app_no_ejecuta_scripts(monkeypatch, target):
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **k: pytest.fail("no debía lanzar nada"))
    res = tools.os_control("open_app", target=target)
    assert res["success"] is False and res.get("blocked") is True


@pytest.mark.parametrize("action", ["lock_screen", "media_play_pause", "media_next", "show_desktop", "mute", "notify", "set_volume"])
def test_linux_sin_herramientas_devuelve_error_claro(monkeypatch, action):
    _fake_platform(monkeypatch, "linux")
    _which_only(monkeypatch, set())
    res = tools.os_control(action, value="50")
    assert res["success"] is False and res["error"]


def test_linux_bloqueo_no_miente_si_loginctl_falla(monkeypatch):
    _fake_platform(monkeypatch, "linux")
    _which_only(monkeypatch, {"loginctl"})
    calls = []
    _run_returning(monkeypatch, {"loginctl": 1}, calls)
    res = tools.os_control("lock_screen")
    assert res["success"] is False and "desbloqueado" in res["error"]


def test_linux_volumen_prueba_pactl_si_wpctl_falla(monkeypatch):
    _fake_platform(monkeypatch, "linux")
    _which_only(monkeypatch, {"wpctl", "pactl"})
    calls = []
    _run_returning(monkeypatch, {"wpctl": 1, "pactl": 0}, calls)
    res = tools.os_control("set_volume", value="40")
    assert res["success"] is True and "pactl" in res["message"]
    assert [c[0] for c in calls] == ["wpctl", "pactl"]


def test_linux_wayland_show_desktop_no_usa_xdotool(monkeypatch):
    _fake_platform(monkeypatch, "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _which_only(monkeypatch, {"xdotool"})
    calls = []
    _run_returning(monkeypatch, {}, calls)
    assert tools.os_control("show_desktop")["success"] is False
    assert calls == []


def test_linux_wayland_portapapeles_prefiere_wl_copy(monkeypatch):
    _fake_platform(monkeypatch, "linux")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _which_only(monkeypatch, {"xclip", "wl-copy"})
    calls = []
    _run_returning(monkeypatch, {}, calls)
    res = tools.os_control("write_clipboard", value="hola")
    assert res["success"] is True and calls[0] == ["wl-copy"]


# --- MUL-12: captura de pantalla --------------------------------------------------

class _FakeShot:
    def __init__(self, color):
        self.size = (4, 3)
        self.width, self.height = self.size
        self.bgra = bytes(color) * (4 * 3)


def _fake_mss(monkeypatch, color, with_MSS):
    class Ctx:
        monitors = [{}, {"left": 0}]

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def grab(self, mon):
            return _FakeShot(color)

    module = SimpleNamespace(mss=lambda: Ctx())
    if with_MSS:
        module.MSS = lambda: Ctx()
    monkeypatch.setitem(sys.modules, "mss", module)


def _imagegrab_falla(monkeypatch):
    from PIL import ImageGrab

    def grab(*a, **k):
        raise OSError("sin X11")

    monkeypatch.setattr(ImageGrab, "grab", grab)


def test_inspect_screen_mss_sin_clase_MSS(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    _imagegrab_falla(monkeypatch)
    _fake_mss(monkeypatch, (10, 20, 30, 0), with_MSS=False)  # mss 10.0/10.1
    res = tools.inspect_screen("¿qué hay?")
    assert res["success"] is True and res["base64_data"]


@pytest.mark.skipif(sys.platform == "win32", reason="Wayland es de Linux")
def test_inspect_screen_wayland_negro_no_es_exito(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _which_only(monkeypatch, set())
    from PIL import Image, ImageGrab

    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: Image.new("RGB", (8, 8)))
    _fake_mss(monkeypatch, (0, 0, 0, 0), with_MSS=True)
    res = tools.inspect_screen()
    assert res["success"] is False and "grim" in res["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="Wayland es de Linux")
def test_inspect_screen_wayland_usa_grim(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    _which_only(monkeypatch, {"grim"})
    from PIL import Image, ImageGrab

    monkeypatch.setattr(ImageGrab, "grab", lambda *a, **k: pytest.fail("en Wayland no debía usar X11"))

    def fake_run(cmd, **kw):
        Image.new("RGB", (5, 5), (200, 10, 10)).save(cmd[-1])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(tools.subprocess, "run", fake_run)
    res = tools.inspect_screen()
    assert res["success"] is True and res["width"] == 5


# --- Ronda 2 -----------------------------------------------------------------------

# NUEVO: borrar un enlace que ES una ruta protegida (/bin -> usr/bin)
@pytest.mark.skipif(sys.platform == "win32", reason="crear symlinks en Windows requiere permisos")
def test_delete_enlace_que_es_ruta_protegida_se_bloquea(tmp_path, monkeypatch):
    real = tmp_path / "usr" / "bin"
    real.mkdir(parents=True)
    enlace = tmp_path / "bin"
    enlace.symlink_to("usr/bin", target_is_directory=True)
    monkeypatch.setattr(tools, "PROTECTED_TREES", [str(enlace)])

    assert tools.needs_confirmation("file_manager", {"action": "delete", "path": str(enlace)}) is None
    res = tools.file_manager("delete", str(enlace))
    assert res["success"] is False and res.get("blocked") is True
    assert enlace.is_symlink()


@pytest.mark.skipif(not os.path.islink("/bin"), reason="/bin no es un enlace en este sistema")
def test_delete_del_enlace_real_bin_se_bloquea():
    assert tools._link_protected(Path("/bin"))
    assert tools._describe_delete("/bin") is None


# SEG-7: sin 'action', file_manager lee: leer un secreto debe pedir confirmación
def test_leer_secreto_sin_action_pide_confirmacion(tmp_path, ask_no):
    secreto = tmp_path / ".env"
    secreto.write_text("API_KEY=xyz", encoding="utf-8")
    assert tools.needs_confirmation("file_manager", {"path": str(secreto)})
    res = tools.execute_tool("file_manager", {"path": str(secreto)})
    assert res.get("blocked") is True and ask_no
    assert "xyz" not in str(res)


# MUL-3: en Windows no se cierran los pipes que tienen los hilos lectores de communicate()
def test_windows_timeout_no_cierra_pipes_bloqueados(monkeypatch):
    _fake_platform(monkeypatch, "win32")

    class PipeBloqueado:
        def close(self):
            pytest.fail("close() esperaría el lock del hilo lector y colgaría el turno")

    class P(_FakePopen):
        def __init__(self, cmd, **kwargs):
            super().__init__(cmd, **kwargs)
            self.stdout, self.stderr = PipeBloqueado(), PipeBloqueado()

        def communicate(self, timeout=None):
            raise tools.subprocess.TimeoutExpired(self.cmd, timeout)

    monkeypatch.setattr(tools.subprocess, "Popen", P)
    monkeypatch.setattr(tools, "kill_process_tree", lambda proc: None)
    assert tools._run_capture(["x"], timeout=1) == (124, "", "", True)


# SEG-6: open_app en Windows no ejecuta scripts resueltos por PATH/PATHEXT
def test_windows_open_app_no_ejecuta_script_resuelto_por_which(monkeypatch):
    _fake_platform(monkeypatch, "win32")
    monkeypatch.setattr(tools.shutil, "which", lambda n: r"C:\Users\vk\Documents\script.BAT" if n == "script" else None)
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **k: pytest.fail("no debía lanzar nada"))
    res = tools.os_control("open_app", target="script")
    assert res["success"] is False and res.get("blocked") is True


def test_windows_open_app_startfile_no_completa_a_script(tmp_path, monkeypatch):
    _fake_platform(monkeypatch, "win32")
    (tmp_path / "limpieza.cmd").write_text("del /q *", encoding="utf-8")
    monkeypatch.setattr(tools.shutil, "which", lambda n: None)
    monkeypatch.setenv("APPDATA", str(tmp_path / "nada"))
    monkeypatch.setenv("ProgramData", str(tmp_path / "nada"))
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.BAT;.CMD")
    monkeypatch.setattr(tools.os, "startfile", lambda p: pytest.fail("no debía ejecutar"), raising=False)
    res = tools.os_control("open_app", target=str(tmp_path / "limpieza"))
    assert res["success"] is False and res.get("blocked") is True


# NUEVO: el respaldo por PATH de Linux no lanza herramientas de administración (sbin)
def test_linux_open_app_no_lanza_herramientas_de_sbin(tmp_path, monkeypatch):
    _fake_platform(monkeypatch, "linux")
    sbin, bin_ = tmp_path / "sbin", tmp_path / "bin"
    sbin.mkdir()
    bin_.mkdir()
    herramienta = sbin / "update-grub"
    herramienta.write_text("#!/bin/sh\n", encoding="utf-8")
    herramienta.chmod(0o755)
    monkeypatch.setattr(tools, "_SBIN_DIRS", (str(sbin),))
    monkeypatch.setattr(tools, "_BIN_DIRS", (str(bin_),))
    _which_only(monkeypatch, {"gtk-launch", "update-grub"})
    _run_returning(monkeypatch, {"gtk-launch": 1}, [])
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **k: pytest.fail("no debía lanzar nada"))
    assert tools.os_control("open_app", target="update-grub")["success"] is False
    monkeypatch.setattr(tools.shutil, "which", lambda n: "/usr/sbin/fdisk" if n == "fdisk" else None)
    assert tools.os_control("open_app", target="fdisk")["success"] is False


# CER-3: junctions de Windows con Python 3.10/3.11 (sin os.path.isjunction)
def test_junction_se_detecta_sin_isjunction(tmp_path, monkeypatch):
    _fake_platform(monkeypatch, "win32")
    monkeypatch.delattr(tools.os.path, "isjunction", raising=False)
    junction = tmp_path / "public_storage"
    junction.mkdir()
    real_lstat = os.lstat

    def fake_lstat(p, *a, **k):
        st = real_lstat(p, *a, **k)
        if os.fspath(p) == str(junction):
            return SimpleNamespace(st_mode=st.st_mode, st_reparse_tag=tools._IO_REPARSE_TAG_MOUNT_POINT)
        return st

    monkeypatch.setattr(tools.os, "lstat", fake_lstat)
    assert tools._link_path(str(junction)) == junction


# CER-11: un fallo comprobando la confirmación no tumba el turno ni ejecuta
def test_fallo_en_needs_confirmation_no_tumba_ni_ejecuta(monkeypatch, ask_no):
    def falla(name, args):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(tools, "needs_confirmation", falla)
    monkeypatch.setattr(tools, "_dispatch", lambda *a: pytest.fail("no debía ejecutar"))
    res = tools.execute_tool("file_manager", {"action": "delete", "path": "a\x00b"})
    assert res["success"] is False
