"""Instalación y lanzadores (requirements.txt, setup_and_run.*, zara.cmd, CI).

Cubre V1_-1 (Python 3.14), MUL-9 (torch de CPU en Linux sin GPU), MUL-4/V1_-2 (lanzador de
Windows y no arrancar sin dependencias), MUL-8 (venv roto en Linux), V1_-3 (paquetes del
sistema en Linux) y MUL-14/V1_-14 (matriz de CI).
"""

import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

import setup_and_run as sr

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
PS1 = ROOT / "setup_and_run.ps1"
SH = ROOT / "setup_and_run.sh"
ZARA_CMD = ROOT / "zara.cmd"
CI = ROOT / ".github" / "workflows" / "ci.yml"


# --- requirements.txt (V1_-1) -------------------------------------------------------------

def _requirements():
    reqs = {}
    for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            req = Requirement(line)
            reqs[req.name.lower()] = req
    return reqs


def _env(python_version, platform):
    return {
        "python_version": python_version,
        "python_full_version": f"{python_version}.0",
        "sys_platform": platform,
        "platform_system": "Windows" if platform == "win32" else "Linux",
        "os_name": "nt" if platform == "win32" else "posix",
    }


def _wanted(req, python_version, platform):
    return req.marker is None or req.marker.evaluate(_env(python_version, platform))


@pytest.mark.parametrize("platform", ["linux", "win32"])
def test_kokoro_is_skipped_on_python_314_and_kept_before(platform):
    """kokoro-onnx exige Python < 3.14: sin marcador, pip abortaba toda la instalación."""
    kokoro = _requirements()["kokoro-onnx"]
    for version in ("3.10", "3.11", "3.12", "3.13"):
        assert _wanted(kokoro, version, platform), version
    for version in ("3.14", "3.15"):
        assert not _wanted(kokoro, version, platform), version


def test_pillow_allows_versions_with_python_314_wheels():
    """pillow 10.x no tiene wheels cp314 (la primera es la 11.3): el tope <11 obligaba a compilar."""
    pillow = _requirements()["pillow"]
    assert pillow.specifier.contains(Version("11.3.0"))
    assert pillow.specifier.contains(Version("12.0.0"))
    assert pillow.specifier.contains(Version("10.4.0"))  # quien ya la tenga no reinstala


def test_every_requirement_line_parses_and_pycaw_is_windows_only():
    reqs = _requirements()
    assert {"torch", "sounddevice", "pyside6", "edge-tts"} <= set(reqs)
    assert _wanted(reqs["pycaw"], "3.12", "win32") and not _wanted(reqs["pycaw"], "3.12", "linux")


# --- torch de solo CPU en Linux sin GPU (MUL-9) --------------------------------------------

def test_cpu_torch_only_on_linux_without_nvidia_gpu():
    assert sr.wants_cpu_torch("linux", env={}, gpu=False) is True
    assert sr.wants_cpu_torch("linux", env={}, gpu=True) is False  # con GPU, el de PyPI (CUDA)
    assert sr.wants_cpu_torch("win32", env={}, gpu=False) is False  # en Windows PyPI ya es CPU
    assert sr.wants_cpu_torch("linux", env={"ZARA_TORCH": "cuda"}, gpu=False) is False
    assert sr.wants_cpu_torch("win32", env={"ZARA_TORCH": "cpu"}, gpu=False) is True


def test_install_commands_put_cpu_torch_first():
    cmds = sr.build_install_commands("/venv/bin/python", cpu_torch=True)
    assert cmds[0] == ["/venv/bin/python", "-m", "pip", "install", "torch>=2.2.0,<3",
                       "--index-url", "https://download.pytorch.org/whl/cpu"]
    assert cmds[-1][-2:] == ["-r", str(sr.REQUIREMENTS_FILE)]
    assert len(sr.build_install_commands("python", cpu_torch=False)) == 1


@pytest.fixture
def fake_venv(tmp_path, monkeypatch):
    """Simula que setup_and_run corre dentro de un venv con su propio requirements.txt."""
    venv = tmp_path / "venv"
    venv.mkdir()
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("torch>=2.2.0,<3\nnumpy\n", encoding="utf-8")
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "base_prefix", str(tmp_path / "base"))
    monkeypatch.setattr(sr, "REQUIREMENTS_FILE", reqs)
    return SimpleNamespace(venv=venv, reqs=reqs, marker=venv / sr.DEPS_MARKER_NAME)


def _fake_pip(results):
    calls = []

    def run(cmd, *args, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=results.pop(0) if results else 0)

    return run, calls


def test_install_falls_back_to_pypi_when_cpu_index_is_unreachable(fake_venv, monkeypatch):
    monkeypatch.setattr(sr, "wants_cpu_torch", lambda: True)
    monkeypatch.setattr(sr, "_torch_installed", lambda: False)
    run, calls = _fake_pip([1, 0])  # índice de PyTorch bloqueado; PyPI bien
    assert sr.install_requirements(run=run) is True
    assert "--index-url" in calls[0] and calls[1][-2:] == ["-r", str(fake_venv.reqs)]
    assert fake_venv.marker.exists()


def test_failed_install_leaves_no_marker(fake_venv, monkeypatch):
    monkeypatch.setattr(sr, "wants_cpu_torch", lambda: False)
    run, calls = _fake_pip([1])
    assert sr.install_requirements(run=run) is False
    assert len(calls) == 1 and not fake_venv.marker.exists()


def test_cpu_step_is_skipped_when_torch_is_already_installed(fake_venv, monkeypatch):
    """Quien ya tiene torch (p. ej. el de CUDA) no lo pierde ni lo vuelve a descargar."""
    monkeypatch.setattr(sr, "wants_cpu_torch", lambda: True)
    monkeypatch.setattr(sr, "_torch_installed", lambda: True)
    run, calls = _fake_pip([])
    assert sr.install_requirements(run=run) is True
    assert len(calls) == 1 and "--index-url" not in calls[0]


# --- Marcador de instalación completa (MUL-8) ----------------------------------------------

def test_install_pending_follows_marker_and_requirements_changes(fake_venv):
    assert sr.install_pending() is True  # venv recién creado o instalación cortada
    sr._write_deps_marker()
    assert sr.install_pending() is False
    fake_venv.reqs.write_text("torch>=2.2.0,<3\nnumpy\nhttpx\n", encoding="utf-8")  # git pull
    assert sr.install_pending() is True


def test_no_marker_outside_a_virtualenv(monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", sys.prefix)
    assert sr.deps_marker_path() is None
    assert sr.install_pending() is False


def test_check_dependencies_completes_an_interrupted_install(fake_venv, monkeypatch):
    monkeypatch.setattr(sr, "probe_dependencies", lambda: ([], {}))
    calls = []
    monkeypatch.setattr(sr, "install_requirements", lambda: calls.append(1) or True)
    assert sr.check_dependencies(auto_install=True) is True
    assert calls == [1]
    # sin --install no se toca nada
    calls.clear()
    assert sr.check_dependencies(auto_install=False) is True
    assert calls == []


def test_missing_packages_without_install_fail_and_clear_marker(fake_venv, monkeypatch, capsys):
    sr._write_deps_marker()
    monkeypatch.setattr(sr, "probe_dependencies", lambda: (["pyside6"], {}))
    assert sr.check_dependencies(auto_install=False) is False
    assert not fake_venv.marker.exists()  # el lanzador volverá a instalar
    assert "--install" in capsys.readouterr().out


def test_missing_packages_are_installed_and_rechecked(fake_venv, monkeypatch):
    results = [(["pyside6"], {}), ([], {})]
    monkeypatch.setattr(sr, "probe_dependencies", lambda: results.pop(0))
    monkeypatch.setattr(sr, "install_requirements", lambda: True)
    assert sr.check_dependencies(auto_install=True) is True


# --- Módulos que no cargan por librerías del sistema (V1_-3) -------------------------------

def test_portaudio_oserror_is_reported_not_raised():
    def fake_import(name):
        if name == "sounddevice":
            raise OSError("PortAudio library not found")
        if name == "mss":
            raise ModuleNotFoundError("No module named 'mss'")
        return object()

    missing, broken = sr.probe_dependencies(import_module=fake_import)
    assert missing == ["mss"]
    assert "PortAudio library not found" in broken["sounddevice"]


def test_missing_portaudio_warns_with_apt_and_dnf_but_does_not_block(capsys):
    assert sr.report_broken({"sounddevice": "OSError: PortAudio library not found"}, "linux") is False
    out = capsys.readouterr().out
    assert "sudo apt install -y libportaudio2" in out and "sudo dnf install -y portaudio" in out


def test_broken_torch_on_windows_blocks_and_points_to_vc_redist(capsys):
    assert sr.report_broken({"torch": "OSError: [WinError 126] c10.dll"}, "win32") is True
    assert "vc_redist" in capsys.readouterr().out


# --- Paquetes del sistema en Linux (V1_-3) -------------------------------------------------

def _which_only(*present):
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_x11_session_reports_portaudio_xcb_cursor_clipboard_and_playerctl(monkeypatch):
    monkeypatch.setattr(sr, "_bundled_xcb_cursor", lambda: False)
    missing = sr.find_missing_linux_packages(
        env={"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"},
        which=_which_only(),
        find_library=lambda name: None,
    )
    assert missing == ["portaudio", "xcb-cursor", "xclip", "playerctl"]


def test_wayland_session_asks_for_wl_clipboard_not_xcb_cursor(monkeypatch):
    monkeypatch.setattr(sr, "_bundled_xcb_cursor", lambda: False)
    missing = sr.find_missing_linux_packages(
        env={"WAYLAND_DISPLAY": "wayland-0", "DISPLAY": ":0", "XDG_SESSION_TYPE": "wayland"},
        which=_which_only("playerctl"),
        find_library=lambda name: "libportaudio.so.2" if name == "portaudio" else None,
    )
    assert missing == ["wl-clipboard"]


def test_complete_desktop_and_headless_session_report_nothing_extra():
    libs = {"portaudio": "libportaudio.so.2", "xcb-cursor": "libxcb-cursor.so.0"}
    full = sr.find_missing_linux_packages(
        env={"DISPLAY": ":0"}, which=_which_only("xclip", "playerctl"), find_library=libs.get,
    )
    assert full == []
    headless = sr.find_missing_linux_packages(env={}, which=_which_only(), find_library=libs.get)
    assert headless == []


@pytest.mark.parametrize("os_release, manager", [
    ('ID=kali\nID_LIKE=debian\n', "apt"),
    ('ID="ubuntu"\nID_LIKE=debian\n', "apt"),
    ('ID=fedora\n', "dnf"),
    ('ID=manjaro\nID_LIKE=arch\n', "pacman"),
    ('ID=nixos\n', ""),
])
def test_package_manager_from_os_release(os_release, manager):
    assert sr.linux_package_manager(os_release) == manager


def test_install_commands_use_each_distro_package_names():
    missing = ["portaudio", "xcb-cursor", "wl-clipboard", "playerctl"]
    assert sr.linux_install_commands(missing, "apt") == [
        "sudo apt install -y libportaudio2 libxcb-cursor0 wl-clipboard playerctl"]
    assert sr.linux_install_commands(missing, "dnf") == [
        "sudo dnf install -y portaudio xcb-util-cursor wl-clipboard playerctl"]
    unknown = sr.linux_install_commands(["portaudio"], "")
    assert unknown == ["sudo apt install -y libportaudio2", "sudo dnf install -y portaudio"]


def test_system_package_check_prints_install_command_and_skips_windows(capsys):
    missing = sr.check_linux_system_packages(
        "linux", env={"DISPLAY": ":0"}, which=_which_only("xclip"),
        find_library=lambda name: "libxcb-cursor.so.0" if name == "xcb-cursor" else None,
        os_release="ID=fedora\n",
    )
    assert missing == ["portaudio", "playerctl"]
    assert "sudo dnf install -y portaudio playerctl" in capsys.readouterr().out
    assert sr.check_linux_system_packages("win32") == []
    assert capsys.readouterr().out == ""


# --- No lanzar main.py sin dependencias (MUL-4/V1_-2) ---------------------------------------

def test_main_exits_with_error_instead_of_launching_without_dependencies(monkeypatch):
    for name in ("print_banner", "check_os_environment", "check_linux_system_packages",
                 "ensure_env_file", "check_audio_devices", "check_ollama_service"):
        monkeypatch.setattr(sr, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(sr, "check_dependencies", lambda auto_install=False: False)
    launched = []
    monkeypatch.setattr(sr.subprocess, "run", lambda *a, **k: launched.append(a))
    monkeypatch.setattr(sys, "argv", ["setup_and_run.py", "--cli"])
    with pytest.raises(SystemExit) as exc:
        sr.main()
    assert exc.value.code == 1
    assert launched == []


# --- Lanzador de Linux: venv roto (MUL-8) ---------------------------------------------------

FAKE_PYTHON3 = textwrap.dedent("""\
    #!/usr/bin/env bash
    # python3 del sistema simulado: `-m venv` crea un venv falso; lo demás va al Python real.
    if [ "$1" = "-m" ] && [ "$2" = "venv" ]; then
        mkdir -p "$3/bin"
        cp "$FAKE_VENV_PY" "$3/bin/python"
        chmod +x "$3/bin/python"
        if [ -n "$FAKE_NO_ENSUREPIP" ]; then
            touch "$3/nopip" "$3/noensurepip"
            echo "The virtual environment was not created successfully because ensurepip is not available." >&2
            exit 1
        fi
        exit 0
    fi
    if [ "$1" = "--version" ]; then echo "Python 3.12.3"; exit 0; fi
    exec "$REAL_PYTHON" "$@"
""")

FAKE_VENV_PY = textwrap.dedent("""\
    #!/usr/bin/env bash
    VENV="$(cd "$(dirname "$0")/.." && pwd)"
    if [ "$1" = "-m" ] && [ "$2" = "pip" ]; then
        if [ -f "$VENV/nopip" ]; then echo "No module named pip" >&2; exit 1; fi
        exit 0
    fi
    if [ "$1" = "-m" ] && [ "$2" = "ensurepip" ]; then
        # Debian/Kali sin python3-venv: ensurepip no existe
        if [ -f "$VENV/noensurepip" ]; then echo "No module named ensurepip" >&2; exit 1; fi
        rm -f "$VENV/nopip"; exit 0
    fi
    if [ "$1" = "-c" ]; then exit 0; fi
    if [ "$1" = "--version"; then echo "Python 3.12.3"; exit 0; fi
    echo "LAUNCH $*"
""")


@pytest.fixture
def sh_sandbox(tmp_path):
    if sys.platform == "win32" or not shutil.which("bash"):
        pytest.skip("usa bash y rutas POSIX")
    app = tmp_path / "zara"
    app.mkdir()
    shutil.copy(SH, app / "setup_and_run.sh")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "python3").write_text(FAKE_PYTHON3, encoding="utf-8")
    venv_py = tmp_path / "venv_python"
    venv_py.write_text(FAKE_VENV_PY, encoding="utf-8")
    for exe in (fake_bin / "python3", venv_py):
        exe.chmod(0o755)
    env = dict(os.environ)
    env.update({
        "PATH": f"{fake_bin}{os.pathsep}{env.get('PATH', '')}",
        "FAKE_VENV_PY": str(venv_py),
        "REAL_PYTHON": sys.executable,
    })
    env.pop("FAKE_NO_ENSUREPIP", None)

    def run(*args, **extra_env):
        proc = subprocess.run(
            ["bash", str(app / "setup_and_run.sh"), *args],
            cwd=app, env={**env, **extra_env}, capture_output=True, text=True, timeout=60,
        )
        return proc.returncode, proc.stdout + proc.stderr

    def make_venv(with_pip=True, ensurepip=False, uv=False):
        (app / ".venv" / "bin").mkdir(parents=True)
        shutil.copy(venv_py, app / ".venv" / "bin" / "python")
        if not with_pip:
            (app / ".venv" / "nopip").touch()
            if not ensurepip:
                (app / ".venv" / "noensurepip").touch()
        if uv:
            (app / ".venv" / "pyvenv.cfg").write_text("home = /usr/bin\nuv = 0.5.0\n", encoding="utf-8")

    return SimpleNamespace(app=app, run=run, make_venv=make_venv)


def _launch_line(output):
    return next(line for line in output.splitlines() if line.startswith("LAUNCH "))


def test_sh_recreates_a_venv_without_pip_and_installs(sh_sandbox):
    sh_sandbox.make_venv(with_pip=False)  # lo que deja Debian/Kali sin python3-venv
    rc, out = sh_sandbox.run("--cli")
    assert rc == 0, out
    assert "Recreándolo" in out
    assert not (sh_sandbox.app / ".venv" / "nopip").exists()
    launch = _launch_line(out)
    assert "--install" in launch and launch.endswith("--cli")


def test_sh_keeps_a_venv_without_pip_when_ensurepip_recovers_it(sh_sandbox):
    sh_sandbox.make_venv(with_pip=False, ensurepip=True)
    rc, out = sh_sandbox.run("--cli")
    assert rc == 0, out
    assert "Recreándolo" not in out and "Creando entorno" not in out
    assert "--install" in _launch_line(out)


def test_sh_never_deletes_a_uv_venv_without_pip(sh_sandbox):
    """`uv venv` no instala pip a propósito: borrarlo tiraba todo lo instalado con uv."""
    sh_sandbox.make_venv(with_pip=False, uv=True)
    rc, out = sh_sandbox.run("--cli")
    assert rc == 0, out
    assert "Recreándolo" not in out and "Creando entorno" not in out
    assert (sh_sandbox.app / ".venv" / "pyvenv.cfg").exists()
    launch = _launch_line(out)
    assert "--install" not in launch and launch.endswith("--cli")


def test_sh_removes_half_created_venv_and_explains_python3_venv(sh_sandbox):
    rc, out = sh_sandbox.run(FAKE_NO_ENSUREPIP="1")
    assert rc == 1
    assert "python3-venv" in out
    assert not (sh_sandbox.app / ".venv").exists()  # el próximo intento empieza limpio


def test_sh_reuses_a_healthy_venv_and_lets_setup_finish_the_install(sh_sandbox):
    sh_sandbox.make_venv(with_pip=True)
    rc, out = sh_sandbox.run("--check")
    assert rc == 0, out
    assert "Recreándolo" not in out and "Creando entorno" not in out
    launch = _launch_line(out)
    assert "setup_and_run.py --install --check" in launch


# --- Lanzador de Windows (MUL-4/V1_-2) -----------------------------------------------------

def test_ps1_is_ascii_or_has_utf8_bom():
    """Windows PowerShell 5.1 lee un .ps1 sin BOM como ANSI: los acentos se corrompen."""
    raw = PS1.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") or raw.isascii()


def test_ps1_never_runs_a_command_line_stored_in_one_string():
    """`$PythonExe = "py -3.12"` y luego `& $PythonExe` buscaba un comando con ese nombre literal."""
    code = "\n".join(line.split("#", 1)[0] for line in PS1.read_text(encoding="utf-8-sig").splitlines())
    assert not re.search(r"=\s*[\"']py\s+-", code)
    assert "@RunArgs" in code and "-m venv" in code


def _powershell():
    custom = os.environ.get("ZARA_TEST_PWSH")
    if custom:
        return custom
    names = ("powershell", "pwsh") if sys.platform == "win32" else ("pwsh",)
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    pytest.skip("PowerShell no está instalado")


def _run_ps(code, env=None):
    proc = subprocess.run(
        [_powershell(), "-NoProfile", "-NonInteractive", "-Command", code],
        capture_output=True, text=True, timeout=120, env=env,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def _load_functions(extra):
    path = str(PS1).replace("'", "''")
    return (
        f"$ast = [System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$null); "
        "$fs = $ast.FindAll({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $false); "
        "foreach ($f in $fs) { . ([ScriptBlock]::Create($f.Extent.Text)) }; " + extra
    )


def test_ps1_parses_without_errors():
    path = str(PS1).replace("'", "''")
    out = _run_ps(
        "$e = $null; "
        f"[System.Management.Automation.Language.Parser]::ParseFile('{path}', [ref]$null, [ref]$e) | Out-Null; "
        "if ($e) { $e | ForEach-Object { $_.ToString() } } else { 'SIN-ERRORES' }"
    )
    assert "SIN-ERRORES" in out, out


def test_ps1_test_python_accepts_real_interpreter_and_rejects_missing_one(tmp_path):
    exe = sys.executable.replace("'", "''")
    missing = str(tmp_path / "no-existe" / "python.exe").replace("'", "''")
    out = _run_ps(_load_functions(
        f"'REAL=' + (Test-Python -Exe '{exe}'); 'FALTA=' + (Test-Python -Exe '{missing}')"
    ))
    assert "REAL=True" in out and "FALTA=False" in out, out


@pytest.mark.skipif(sys.platform == "win32", reason="los dobles de python.exe son scripts POSIX")
def test_ps1_skips_store_stub_and_uses_py_launcher_with_separate_args(tmp_path):
    """Solo el alias de la Store (sale con 9009) y el lanzador py con 3.12: se elige `py -3.12`."""
    stub, launcher = tmp_path / "stub", tmp_path / "py"
    stub.mkdir()
    launcher.mkdir()
    (stub / "python.exe").write_text(
        "#!/bin/sh\necho 'Python was not found; run without arguments to install from the Microsoft Store' >&2\n"
        "exit 9009\n", encoding="utf-8")
    py_script = f'#!/bin/sh\nif [ "$1" = "-3.12" ]; then shift; exec "{sys.executable}" "$@"; fi\nexit 103\n'
    for name in ("py.exe", "py"):
        (launcher / name).write_text(py_script, encoding="utf-8")
    for exe in (stub / "python.exe", launcher / "py.exe", launcher / "py"):
        exe.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(stub), str(launcher), "/usr/bin", "/bin"])
    out = _run_ps(_load_functions(
        "$r = Find-BasePython; if ($r) { @{ exe = $r.Exe; pre = @($r.PreArgs) } | ConvertTo-Json -Compress } "
        "else { 'NINGUNO' }"
    ), env=env)
    found = json.loads(out.strip().splitlines()[-1])
    assert found["exe"] == "py"
    assert found["pre"] in (["-3.12"], "-3.12")  # ConvertTo-Json aplana los arrays de un elemento


@pytest.mark.parametrize("cmd", [ZARA_CMD, ROOT / "zara-chat.cmd"], ids=lambda p: p.name)
def test_zara_cmd_pauses_on_error_so_double_click_shows_it(cmd):
    raw = cmd.read_bytes()
    assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b"")  # cmd.exe espera CRLF
    text = raw.decode("ascii")  # cmd.exe no lee UTF-8 por defecto
    lowered = text.lower()
    assert "setup_and_run.ps1" in lowered
    assert "pause" in lowered and "errorlevel" in lowered and "exit /b" in lowered
    block = lowered.split('if not "%zara_rc%"=="0" (', 1)[1].split("\n)", 1)[0]
    assert ")" not in block.replace("\r", "")  # un ")" sin escapar cerraría el bloque


def test_setup_and_run_propagates_the_exit_code_of_main(monkeypatch):
    """Sin propagar el código de main.py, zara.cmd nunca hacía pause ante un traceback."""
    calls = []

    def fake_run(cmd, *a, **k):
        calls.append(cmd)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(sr.subprocess, "run", fake_run)
    for name in ("print_banner", "check_python_version", "check_os_environment", "check_linux_system_packages",
                 "ensure_env_file", "check_dependencies", "check_audio_devices", "check_ollama_service"):
        monkeypatch.setattr(sr, name, lambda *a, **k: True)
    monkeypatch.setattr(sys, "argv", ["setup_and_run.py"])
    with pytest.raises(SystemExit) as exc:
        sr.main()
    assert exc.value.code == 1
    assert calls and any("main.py" in str(part) for part in calls[-1])


# --- CI (MUL-14/V1_-14) ---------------------------------------------------------------------

def test_ci_runs_on_windows_and_several_python_versions():
    text = CI.read_text(encoding="utf-8")
    assert "windows-latest" in text and "ubuntu-latest" in text
    for version in ('"3.10"', '"3.13"', '"3.14"'):
        assert version in text
    assert "tests/test_v1_instalacion.py" in text
    assert "--only-binary" in text  # requirements.txt resoluble con wheels en 3.10-3.14
