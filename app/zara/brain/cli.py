import os
import shutil
import subprocess
import sys
from pathlib import Path
from ..errors import BrainError

# Espacio de trabajo en el SSD nativo (rutas calientes, punto 6), no en NTFS.
if sys.platform == "win32":
    _DEFAULT_SANDBOX = str(Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "zara" / "espacio")
else:
    _DEFAULT_SANDBOX = str(Path(os.environ.get(
        "XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "zara" / "espacio")


class _CliBrain:
    """Base para cerebros que son un CLI en modo no interactivo (print)."""

    binary: str = ""
    name: str = ""

    def __init__(self, system: str = "", timeout: int = 300, cwd: str | None = None):
        self.system = system
        self.timeout = timeout
        self.cwd = cwd

    def available(self) -> bool:
        return shutil.which(self.binary) is not None

    def _build_cmd(self, prompt: str, context: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def ask(self, prompt: str, context: str = "") -> str:
        if not self.available():
            raise BrainError(f"{self.name}: '{self.binary}' no está instalado")
        cmd = self._build_cmd(prompt, context)
        workdir = self.cwd
        if workdir:
            import os
            os.makedirs(workdir, exist_ok=True)
        try:
            out = subprocess.run(cmd, capture_output=True, text=True,
                                 timeout=self.timeout, cwd=workdir)
        except subprocess.TimeoutExpired as exc:
            raise BrainError(f"{self.name} agotó el tiempo") from exc
        if out.returncode != 0:
            raise BrainError(f"{self.name} falló: {out.stderr.strip()[:300]}")
        return out.stdout.strip()


class ClaudeBrain(_CliBrain):
    binary = "claude"
    name = "claude"

    # Carpeta aislada del modo consulta (solo lectura ahí).
    SANDBOX = str(Path(_DEFAULT_SANDBOX) / "claude-sandbox")
    # Modo consulta: web + lectura, sin tocar nada.
    _CONSULTA_TOOLS = "WebFetch,WebSearch,Read"

    def __init__(self, system: str = "", timeout: int = 600, cwd: str | None = None,
                 model: str | None = None, mode: str = "consulta",
                 workdir: str | None = None, extra_dirs: list[str] | None = None,
                 mcp_config: str | None = None):
        self.mode = mode if mode in ("consulta", "agente") else "consulta"
        if self.mode == "consulta":
            wd = cwd or self.SANDBOX
        else:
            import os
            wd = workdir or cwd or os.path.expanduser("~")
        super().__init__(system=system, timeout=timeout, cwd=wd)
        self.model = model or None
        self.extra_dirs = extra_dirs or []
        # Hub MCP: un JSON con servidores (como el de Claude Code). Solo se
        # pasa si el archivo existe; da al agente herramientas extra (MCP).
        import os as _os
        self.mcp_config = mcp_config if mcp_config and _os.path.exists(mcp_config) else None

    def _build_cmd(self, prompt: str, context: str) -> list[str]:
        from .framing import frame_user
        # --setting-sources project: no carga plugins/hooks del usuario
        # (respuesta más rápida y sin interferencias).
        cmd = ["claude", "-p", frame_user(prompt, context),
               "--setting-sources", "project"]
        if self.mode == "consulta":
            cmd += ["--tools", self._CONSULTA_TOOLS,
                    "--allowedTools", self._CONSULTA_TOOLS]
        else:
            # Modo agente: todas las herramientas (editar, ejecutar, etc.) en la
            # carpeta de trabajo. Es el CLI de Claude del Señor VK en su equipo.
            cmd += ["--dangerously-skip-permissions"]
            for d in self.extra_dirs:
                cmd += ["--add-dir", d]
            if self.mcp_config:
                # --strict-mcp-config: usa solo estos servidores (no los del usuario).
                cmd += ["--mcp-config", self.mcp_config, "--strict-mcp-config"]
        if self.model:
            cmd += ["--model", self.model]
        if self.system:
            cmd += ["--append-system-prompt", self.system]
        return cmd


class GeminiBrain(_CliBrain):
    binary = "gemini"
    name = "gemini"

    # Carpeta aislada: Gemini CLI es agéntico; lo encerramos para que no toque
    # archivos del sistema. La seguridad real la impone el Guardián aparte.
    _SANDBOX = str(Path(_DEFAULT_SANDBOX) / "gemini-sandbox")

    def __init__(self, system: str = "", timeout: int = 300, cwd: str | None = None):
        super().__init__(system=system, timeout=timeout, cwd=cwd or self._SANDBOX)

    def available(self) -> bool:
        # El login personal del Gemini CLI fue retirado por Google (06/2026).
        # Solo lo ofrecemos si hay una API key configurada (vía que sí funciona).
        import os
        return shutil.which(self.binary) is not None and bool(
            os.environ.get("GEMINI_API_KEY"))

    def _build_cmd(self, prompt: str, context: str) -> list[str]:
        from .framing import frame_user
        full = frame_user(prompt, context)
        if self.system:
            full = self.system + "\n\n" + full
        # --approval-mode plan = solo lectura; no ejecuta ni edita nada.
        return ["gemini", "--approval-mode", "plan", "-p", full]
