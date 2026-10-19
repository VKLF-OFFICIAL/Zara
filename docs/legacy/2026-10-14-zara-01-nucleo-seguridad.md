# Zara · Plan 1 — Núcleo de seguridad (Guardián) · Plan de implementación

> **Para ejecutores:** SUB-SKILL REQUERIDA: usar superpowers:subagent-driven-development (recomendada) o superpowers:executing-plans para implementar tarea a tarea. Los pasos usan casillas (`- [ ]`) para seguimiento.

**Goal:** Construir la base de seguridad de Zara: un paquete Python aislado que resuelve rutas, carga configuración, clasifica cualquier acción sobre el sistema en cuatro niveles, hace copias de seguridad y audita todo — probado por completo sin audio, UI, red ni modelos.

**Architecture:** Paquete `zara` puro (sin dependencias pesadas). `paths` localiza el almacén (NTFS) y el SSD y verifica el montaje; `config` carga TOML; `audit` escribe JSONL append-only; `backup` copia a `copias/`; `guardian` clasifica una `Action` en SAFE/CONFIRM/CRITICAL/BLOCKED mediante reglas de `guardian.toml` + alcance de rutas, y prepara copias antes de actuar. Todo es sincrónico y testeable en aislamiento.

**Tech Stack:** Python 3.12 (gestionado con `uv`), `tomllib` (stdlib), `pytest`. Sin dependencias de terceros en runtime en este plan.

**Spec:** `/mnt/almacen/ZARA/docs/2026-10-14-zara-diseno.md`

## Global Constraints

- **Python 3.12 exacto**, entorno aislado con `uv` en `~/.local/share/zara/venv` (el sistema usa 3.14, incompatible). El código del proyecto vive en `/mnt/almacen/ZARA/app/`.
- **Raíz de datos de Zara:** `/mnt/almacen/ZARA` (constante `ZARA_ROOT`). Enlace `~/Zara` ya existe.
- **Raíz en SSD:** `~/.local/share/zara` para venv y datos sensibles (huella de voz) con permisos `600`/`700`; NO en NTFS.
- **El disco NTFS (`/mnt/almacen`) no soporta permisos POSIX;** nunca depender de `chmod` sobre rutas bajo `ZARA_ROOT`. Los permisos restrictivos solo se aplican bajo el SSD.
- **Niveles del Guardián (verbatim):** `SAFE` (🟢 ejecuta directo), `CONFIRM` (🟡 explica + confirmación, 30 s sin respuesta → cancela), `CRITICAL` (🔴 verificación de estado real + copia + doble confirmación), `BLOCKED` (⛔ nunca).
- **Regla de alcance:** dentro de `~/Zara/espacio/` las escrituras son SAFE; cualquier escritura/borrado/movimiento fuera de `ZARA_ROOT` es al menos CONFIRM.
- **Garantía crítica (requisito explícito del usuario):** ninguna acción CRITICAL se declara ejecutable sin (1) verificación del estado real, (2) copia de seguridad realizada, (3) doble confirmación pendiente. Ante ambigüedad → no actúa.
- **Auditoría:** toda clasificación y preparación se registra en `registros/acciones-AAAA-MM-DD.jsonl` (una línea JSON por evento, append-only).
- **Git:** el proyecto NO es aún un repo git y la norma permanente del usuario es no usar git salvo que lo pida. Cada paso «Commit» es un **punto de control** (checkpoint): ejecutar los tests y verificar que pasan. Solo inicializar git y commitear cuando el usuario lo autorice.

## Review Focus

- **Almacén NTFS desmontado:** si `/mnt/almacen` no está montado, `paths` debe lanzar `ZaraNotMountedError` con mensaje claro, nunca crear `/mnt/almacen/ZARA` como carpeta vacía en el SSD. (Cubierto en Task 1.)
- **`guardian.toml` ausente o corrupto:** el Guardián debe caer a **reglas por defecto seguras** (clasificar lo desconocido como al menos CONFIRM), nunca abrirse a SAFE por falta de reglas. (Cubierto en Task 5.)
- **Ruta con symlink que escapa del espacio:** una ruta bajo `espacio/` que es symlink apuntando fuera de `ZARA_ROOT` NO debe tratarse como SAFE; se resuelve a ruta real antes de decidir alcance. (Cubierto en Task 6.)
- **Patrón de regla que coincide en varios niveles:** si una acción casa con BLOCKED y con CONFIRM, gana el más restrictivo (BLOCKED > CRITICAL > CONFIRM > SAFE). (Cubierto en Task 6.)
- **Copia de seguridad de ruta inexistente o sin permiso de lectura:** `backup` debe fallar de forma explícita (`BackupError`) y el Guardián debe marcar la acción como NO preparada, no «preparada» en falso. (Cubierto en Task 4 y Task 7.)

---

## File Structure

```
/mnt/almacen/ZARA/app/
├── pyproject.toml
├── zara/
│   ├── __init__.py
│   ├── errors.py            # excepciones del dominio
│   ├── paths.py             # ZaraPaths: raíces, verificación de montaje, subdirs
│   ├── config.py            # carga de TOML con defaults
│   ├── audit.py             # AuditLog: JSONL append-only
│   ├── backup.py            # backup_path(): copia a copias/
│   └── guardian/
│       ├── __init__.py
│       ├── action.py        # Action, Level, Decision (modelos de datos)
│       ├── rules.py         # RuleSet: carga guardian.toml, clasifica por patrón+alcance
│       └── engine.py        # Guardian: classify() + prepare()
└── tests/
    ├── conftest.py
    ├── test_paths.py
    ├── test_config.py
    ├── test_audit.py
    ├── test_backup.py
    └── guardian/
        ├── test_action.py
        ├── test_rules.py
        └── test_engine.py
```

---

### Task 1: Entorno, paquete y resolución de rutas

**Files:**
- Create: `/mnt/almacen/ZARA/app/pyproject.toml`
- Create: `/mnt/almacen/ZARA/app/zara/__init__.py`
- Create: `/mnt/almacen/ZARA/app/zara/errors.py`
- Create: `/mnt/almacen/ZARA/app/zara/paths.py`
- Test: `/mnt/almacen/ZARA/app/tests/test_paths.py`
- Test: `/mnt/almacen/ZARA/app/tests/conftest.py`

**Interfaces:**
- Produces:
  - `errors.ZaraError(Exception)`, `errors.ZaraNotMountedError(ZaraError)`, `errors.BackupError(ZaraError)`, `errors.ConfigError(ZaraError)`
  - `paths.ZaraPaths` con `__init__(self, root: Path, ssd: Path)`, propiedades `root, config, memoria, espacio, tareas, registros, copias, voz` (todas `Path` bajo `root`) y `ssd_voz: Path` (bajo `ssd`).
  - `paths.ZaraPaths.resolve() -> ZaraPaths` (classmethod): usa `ZARA_ROOT=/mnt/almacen/ZARA`, `SSD=~/.local/share/zara`; verifica montaje; crea subdirs que falten bajo `root` y `ssd`.
  - `paths.MOUNT_SENTINEL: Path` = `/mnt/almacen/.zara_mounted` (fichero centinela que prueba que el NTFS está montado).

- [ ] **Step 1: Crear el entorno aislado con uv**

Run:
```bash
cd /mnt/almacen/ZARA/app
uv venv --python 3.12 ~/.local/share/zara/venv
uv pip install --python ~/.local/share/zara/venv/bin/python pytest
```
Expected: venv creado con Python 3.12 y pytest instalado. (Si `uv` no está: `curl -LsSf https://astral.sh/uv/install.sh | sh` — pedir al usuario que lo ejecute si requiere confirmación.)

- [ ] **Step 2: Escribir `pyproject.toml`**

```toml
[project]
name = "zara"
version = "0.1.0"
requires-python = ">=3.12,<3.13"

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]

[tool.setuptools]
packages = ["zara", "zara.guardian"]
```

- [ ] **Step 3: Escribir `zara/errors.py`**

```python
class ZaraError(Exception):
    """Error base del dominio de Zara."""

class ZaraNotMountedError(ZaraError):
    """El almacén /mnt/almacen no está montado."""

class BackupError(ZaraError):
    """No se pudo crear la copia de seguridad."""

class ConfigError(ZaraError):
    """Configuración ausente o inválida."""
```

- [ ] **Step 4: Escribir el test de montaje (falla primero)**

`tests/conftest.py`:
```python
from pathlib import Path
import pytest


@pytest.fixture
def fake_env(tmp_path):
    """Crea un root y un ssd falsos con el centinela de montaje presente."""
    root = tmp_path / "almacen" / "ZARA"
    ssd = tmp_path / "ssd"
    sentinel = tmp_path / "almacen" / ".zara_mounted"
    root.parent.mkdir(parents=True)
    sentinel.write_text("ok")
    ssd.mkdir()
    return {"root": root, "ssd": ssd, "sentinel": sentinel}
```

`tests/test_paths.py`:
```python
import pytest
from zara.paths import ZaraPaths
from zara.errors import ZaraNotMountedError


def test_resolve_creates_subdirs(fake_env):
    p = ZaraPaths.resolve(root=fake_env["root"], ssd=fake_env["ssd"],
                          sentinel=fake_env["sentinel"])
    for d in (p.config, p.memoria, p.espacio, p.tareas, p.registros,
              p.copias, p.voz):
        assert d.is_dir()
    assert p.ssd_voz.is_dir()


def test_resolve_raises_when_not_mounted(fake_env):
    fake_env["sentinel"].unlink()
    with pytest.raises(ZaraNotMountedError):
        ZaraPaths.resolve(root=fake_env["root"], ssd=fake_env["ssd"],
                          sentinel=fake_env["sentinel"])
    # Nunca debe crear el root si no hay montaje
    assert not fake_env["root"].exists()
```

- [ ] **Step 5: Ejecutar y verificar que falla**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_paths.py -v`
Expected: FAIL (`ModuleNotFoundError: zara.paths`).

- [ ] **Step 6: Escribir `zara/paths.py`**

```python
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from .errors import ZaraNotMountedError

ZARA_ROOT = Path("/mnt/almacen/ZARA")
SSD = Path.home() / ".local/share/zara"
MOUNT_SENTINEL = Path("/mnt/almacen/.zara_mounted")

_SUBDIRS = ("config", "memoria", "espacio", "tareas", "registros", "copias", "voz")


@dataclass
class ZaraPaths:
    root: Path
    ssd: Path

    @classmethod
    def resolve(cls, root: Path = ZARA_ROOT, ssd: Path = SSD,
                sentinel: Path = MOUNT_SENTINEL) -> "ZaraPaths":
        if not sentinel.exists():
            raise ZaraNotMountedError(
                f"El almacén no está montado (falta {sentinel}). "
                "Monta /mnt/almacen antes de iniciar Zara."
            )
        self = cls(root=root, ssd=ssd)
        for d in _SUBDIRS:
            (root / d).mkdir(parents=True, exist_ok=True)
        self.ssd_voz.mkdir(parents=True, exist_ok=True)
        return self

    @property
    def config(self) -> Path: return self.root / "config"
    @property
    def memoria(self) -> Path: return self.root / "memoria"
    @property
    def espacio(self) -> Path: return self.root / "espacio"
    @property
    def tareas(self) -> Path: return self.root / "tareas"
    @property
    def registros(self) -> Path: return self.root / "registros"
    @property
    def copias(self) -> Path: return self.root / "copias"
    @property
    def voz(self) -> Path: return self.root / "voz"
    @property
    def ssd_voz(self) -> Path: return self.ssd / "voz"
```

Nota: el centinela `/mnt/almacen/.zara_mounted` lo creará el instalador (fuera de este plan). En tests se inyecta.

- [ ] **Step 7: Ejecutar y verificar que pasa**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_paths.py -v`
Expected: PASS (2 tests).

- [ ] **Step 8: Punto de control (checkpoint)**

Run: `~/.local/share/zara/venv/bin/pytest -v`
Expected: PASS. (Git diferido; no commitear salvo autorización del usuario.)

---

### Task 2: Carga de configuración (TOML con defaults)

**Files:**
- Create: `/mnt/almacen/ZARA/app/zara/config.py`
- Test: `/mnt/almacen/ZARA/app/tests/test_config.py`

**Interfaces:**
- Consumes: `errors.ConfigError`.
- Produces: `config.load_toml(path: Path, defaults: dict) -> dict`. Si `path` no existe → devuelve copia de `defaults`. Si existe pero es TOML inválido → lanza `ConfigError`. Fusiona superficialmente (claves de nivel superior) sobre `defaults`.

- [ ] **Step 1: Escribir el test (falla primero)**

```python
import pytest
from zara.config import load_toml
from zara.errors import ConfigError

DEFAULTS = {"umbral_voz": 0.65, "esquina": "arriba-derecha"}


def test_missing_file_returns_defaults(tmp_path):
    out = load_toml(tmp_path / "no.toml", DEFAULTS)
    assert out == DEFAULTS
    out["umbral_voz"] = 0.9
    assert DEFAULTS["umbral_voz"] == 0.65  # no muta los defaults


def test_file_overrides_defaults(tmp_path):
    f = tmp_path / "z.toml"
    f.write_text('umbral_voz = 0.8\n')
    out = load_toml(f, DEFAULTS)
    assert out["umbral_voz"] == 0.8
    assert out["esquina"] == "arriba-derecha"


def test_invalid_toml_raises(tmp_path):
    f = tmp_path / "bad.toml"
    f.write_text('umbral_voz = = =\n')
    with pytest.raises(ConfigError):
        load_toml(f, DEFAULTS)
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: zara.config`).

- [ ] **Step 3: Escribir `zara/config.py`**

```python
from __future__ import annotations
import tomllib
from copy import deepcopy
from pathlib import Path
from .errors import ConfigError


def load_toml(path: Path, defaults: dict) -> dict:
    result = deepcopy(defaults)
    if not path.exists():
        return result
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"TOML inválido en {path}: {exc}") from exc
    result.update(data)
    return result
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_config.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Punto de control**

Run: `~/.local/share/zara/venv/bin/pytest -v`
Expected: PASS.

---

### Task 3: Auditoría append-only (JSONL)

**Files:**
- Create: `/mnt/almacen/ZARA/app/zara/audit.py`
- Test: `/mnt/almacen/ZARA/app/tests/test_audit.py`

**Interfaces:**
- Produces: `audit.AuditLog(dir: Path, clock: Callable[[], datetime] = ...)`.
  - `record(self, event: str, data: dict) -> dict`: escribe una línea JSON `{ts, event, **data}` en `dir/acciones-AAAA-MM-DD.jsonl` (append), devuelve el dict escrito. `ts` en ISO-8601.
  - El fichero del día se elige por la fecha de `clock()`.

- [ ] **Step 1: Escribir el test (falla primero)**

```python
import json
from datetime import datetime
from zara.audit import AuditLog


def test_record_appends_jsonl(tmp_path):
    clock = lambda: datetime(2026, 10, 14, 12, 0, 0)
    log = AuditLog(tmp_path, clock=clock)
    log.record("classify", {"level": "CRITICAL", "cmd": "rm x"})
    log.record("prepare", {"backup": "/c/1"})
    f = tmp_path / "acciones-2026-10-14.jsonl"
    lines = f.read_text().strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "classify"
    assert first["level"] == "CRITICAL"
    assert first["ts"].startswith("2026-10-14T12:00:00")
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_audit.py -v`
Expected: FAIL (`ModuleNotFoundError: zara.audit`).

- [ ] **Step 3: Escribir `zara/audit.py`**

```python
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from typing import Callable


class AuditLog:
    def __init__(self, dir: Path, clock: Callable[[], datetime] = datetime.now):
        self._dir = dir
        self._clock = clock

    def record(self, event: str, data: dict) -> dict:
        now = self._clock()
        entry = {"ts": now.isoformat(), "event": event, **data}
        self._dir.mkdir(parents=True, exist_ok=True)
        f = self._dir / f"acciones-{now:%Y-%m-%d}.jsonl"
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_audit.py -v`
Expected: PASS.

- [ ] **Step 5: Punto de control**

Run: `~/.local/share/zara/venv/bin/pytest -v`
Expected: PASS.

---

### Task 4: Copias de seguridad

**Files:**
- Create: `/mnt/almacen/ZARA/app/zara/backup.py`
- Test: `/mnt/almacen/ZARA/app/tests/test_backup.py`

**Interfaces:**
- Consumes: `errors.BackupError`.
- Produces: `backup.backup_path(src: Path, copias_dir: Path, clock=datetime.now) -> Path`.
  - Copia `src` (archivo o carpeta) a `copias_dir/AAAA-MM-DD_HHMMSS/<nombre>` preservando contenido.
  - Si `src` no existe o no es legible → lanza `BackupError`.
  - Devuelve la ruta de destino creada.

- [ ] **Step 1: Escribir el test (falla primero)**

```python
import pytest
from datetime import datetime
from zara.backup import backup_path
from zara.errors import BackupError


def test_backup_file(tmp_path):
    src = tmp_path / "dato.txt"
    src.write_text("hola")
    copias = tmp_path / "copias"
    clock = lambda: datetime(2026, 10, 14, 9, 30, 15)
    dest = backup_path(src, copias, clock=clock)
    assert dest == copias / "2026-10-14_093015" / "dato.txt"
    assert dest.read_text() == "hola"


def test_backup_dir(tmp_path):
    src = tmp_path / "carpeta"
    (src).mkdir()
    (src / "a.txt").write_text("a")
    copias = tmp_path / "copias"
    dest = backup_path(src, copias, clock=lambda: datetime(2026, 1, 1, 0, 0, 0))
    assert (dest / "a.txt").read_text() == "a"


def test_backup_missing_source_raises(tmp_path):
    with pytest.raises(BackupError):
        backup_path(tmp_path / "noexiste", tmp_path / "copias")
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_backup.py -v`
Expected: FAIL (`ModuleNotFoundError: zara.backup`).

- [ ] **Step 3: Escribir `zara/backup.py`**

```python
from __future__ import annotations
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable
from .errors import BackupError


def backup_path(src: Path, copias_dir: Path,
                clock: Callable[[], datetime] = datetime.now) -> Path:
    if not src.exists():
        raise BackupError(f"No existe la ruta a copiar: {src}")
    stamp = f"{clock():%Y-%m-%d_%H%M%S}"
    dest = copias_dir / stamp / src.name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy2(src, dest)
    except OSError as exc:
        raise BackupError(f"No se pudo copiar {src}: {exc}") from exc
    return dest
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

Run: `~/.local/share/zara/venv/bin/pytest tests/test_backup.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Punto de control**

Run: `~/.local/share/zara/venv/bin/pytest -v`
Expected: PASS.

---

### Task 5: Modelos del Guardián (Action, Level, Decision)

**Files:**
- Create: `/mnt/almacen/ZARA/app/zara/guardian/__init__.py`
- Create: `/mnt/almacen/ZARA/app/zara/guardian/action.py`
- Test: `/mnt/almacen/ZARA/app/tests/guardian/test_action.py`

**Interfaces:**
- Produces:
  - `Level(IntEnum)`: `SAFE=0, CONFIRM=1, CRITICAL=2, BLOCKED=3` (orden = severidad; mayor gana).
  - `Action` dataclass: `kind: str` (uno de `"shell","file_write","file_delete","file_move","network","process"`), `description: str`, `command: str | None = None`, `paths: tuple[Path, ...] = ()`, `url: str | None = None`.
  - `Decision` dataclass: `action: Action`, `level: Level`, `reason: str`, `matched_rule: str | None`, `needs_backup: bool`, `prepared: bool = False`, `backup_at: Path | None = None`.
  - `Decision.requires_confirmation -> bool` (True si level ∈ {CONFIRM, CRITICAL}).
  - `Decision.is_blocked -> bool`.

- [ ] **Step 1: Escribir el test (falla primero)**

`tests/guardian/test_action.py`:
```python
from pathlib import Path
from zara.guardian.action import Level, Action, Decision


def test_level_ordering():
    assert Level.BLOCKED > Level.CRITICAL > Level.CONFIRM > Level.SAFE
    assert max(Level.CONFIRM, Level.BLOCKED) is Level.BLOCKED


def test_decision_flags():
    a = Action(kind="shell", description="borrar", command="rm x",
               paths=(Path("/x"),))
    d = Decision(action=a, level=Level.CRITICAL, reason="sudo",
                 matched_rule="r1", needs_backup=True)
    assert d.requires_confirmation is True
    assert d.is_blocked is False

    d2 = Decision(action=a, level=Level.SAFE, reason="ok",
                  matched_rule=None, needs_backup=False)
    assert d2.requires_confirmation is False
```

Crear `tests/guardian/__init__.py` vacío si el runner lo necesita (no con pytest por defecto, pero crear la carpeta `tests/guardian/`).

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `~/.local/share/zara/venv/bin/pytest tests/guardian/test_action.py -v`
Expected: FAIL (`ModuleNotFoundError: zara.guardian.action`).

- [ ] **Step 3: Escribir `zara/guardian/__init__.py` y `zara/guardian/action.py`**

`__init__.py`:
```python
from .action import Level, Action, Decision  # noqa: F401
```

`action.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path


class Level(IntEnum):
    SAFE = 0
    CONFIRM = 1
    CRITICAL = 2
    BLOCKED = 3


@dataclass
class Action:
    kind: str
    description: str
    command: str | None = None
    paths: tuple[Path, ...] = ()
    url: str | None = None


@dataclass
class Decision:
    action: Action
    level: Level
    reason: str
    matched_rule: str | None
    needs_backup: bool
    prepared: bool = False
    backup_at: Path | None = None

    @property
    def requires_confirmation(self) -> bool:
        return self.level in (Level.CONFIRM, Level.CRITICAL)

    @property
    def is_blocked(self) -> bool:
        return self.level is Level.BLOCKED
```

- [ ] **Step 4: Ejecutar y verificar que pasa**

Run: `~/.local/share/zara/venv/bin/pytest tests/guardian/test_action.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Punto de control**

Run: `~/.local/share/zara/venv/bin/pytest -v`
Expected: PASS.

---

### Task 6: Reglas de clasificación (patrones + alcance + defaults seguros)

**Files:**
- Create: `/mnt/almacen/ZARA/app/zara/guardian/rules.py`
- Create: `/mnt/almacen/ZARA/config/guardian.toml`
- Test: `/mnt/almacen/ZARA/app/tests/guardian/test_rules.py`

**Interfaces:**
- Consumes: `Level`, `Action` (Task 5); `load_toml` (Task 2).
- Produces:
  - `RuleSet.from_toml(path: Path, zara_root: Path, espacio: Path) -> RuleSet`. Si falta/corrupto → usa DEFAULTS internos (fail-safe).
  - `RuleSet.classify(action: Action) -> tuple[Level, str, str | None]` → `(level, reason, matched_rule_name)`.
  - Lógica:
    1. Patrones `blocked` (regex sobre `command` o str(paths)) → BLOCKED (gana siempre).
    2. Patrones `critical` → CRITICAL.
    3. Para `file_write/file_delete/file_move`: resolver ruta real (`Path.resolve()`); si cae **dentro de `espacio`** → SAFE; si cae fuera de `zara_root` → al menos CONFIRM (CRITICAL si además casa patrón critical).
    4. Patrones `confirm` (p. ej. `pip install`, `systemctl`, kill) → CONFIRM.
    5. `network` con `url` → CONFIRM.
    6. Lecturas/`shell` de solo lectura que casan `safe` → SAFE.
    7. **Desconocido → CONFIRM** (nunca SAFE por defecto).
  - Si varias reglas casan, gana el Level más alto.

- [ ] **Step 1: Escribir `config/guardian.toml`**

```toml
# Reglas del Guardián de Zara. Orden de severidad: blocked > critical > confirm > safe.
# Los patrones son expresiones regulares (regex) aplicadas al comando o a las rutas.

blocked = [
  '\bmkfs\b', '\bdd\s+if=', '\brm\s+-rf\s+/(?!mnt/almacen/ZARA)',
  '/etc/fstab', '/boot(/|\b)', ':\(\)\s*\{', '\bshutdown\b', '\breboot\b',
]

critical = [
  '\bsudo\b', '\bsu\b', '\bchown\b\s+-R', '\bchmod\b\s+-R',
  'git\s+push\s+.*--force', '\bpasswd\b', '\buserdel\b', '\bvisudo\b',
  '\.ssh/', '\.gnupg/', 'credential', 'id_rsa', 'id_ed25519',
]

confirm = [
  '\bpip\s+install\b', '\buv\s+pip\s+install\b', '\bdnf\b', '\bapt\b',
  '\bsystemctl\b', '\bkill\b', '\bpkill\b', '\bcurl\b.*-o', '\bwget\b',
]

safe = [
  '^\s*ls\b', '^\s*cat\b', '^\s*grep\b', '^\s*find\b', '^\s*echo\b',
  '^\s*pactl\b', '^\s*playerctl\b', '^\s*xdg-open\b',
]
```

- [ ] **Step 2: Escribir el test (falla primero)**

```python
from pathlib import Path
import pytest
from zara.guardian.action import Action, Level
from zara.guardian.rules import RuleSet


@pytest.fixture
def rs(tmp_path):
    espacio = tmp_path / "ZARA" / "espacio"
    espacio.mkdir(parents=True)
    # usa los defaults internos (sin TOML) para fijar el comportamiento fail-safe
    return RuleSet.from_toml(tmp_path / "noexiste.toml",
                             zara_root=tmp_path / "ZARA", espacio=espacio), espacio


def test_blocked_wins(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="sudo dd if=/dev/zero of=/dev/sda"))
    assert lvl is Level.BLOCKED


def test_sudo_is_critical(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="sudo dnf update"))
    assert lvl is Level.CRITICAL  # sudo (critical) gana a dnf (confirm)


def test_write_inside_espacio_is_safe(rs):
    r, espacio = rs
    lvl, _, _ = r.classify(Action(kind="file_write", description="x",
                                  paths=(espacio / "nota.txt",)))
    assert lvl is Level.SAFE


def test_write_outside_root_is_at_least_confirm(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="file_write", description="x",
                                  paths=(Path("/home/vklf/Documentos/x.txt"),)))
    assert lvl >= Level.CONFIRM


def test_symlink_escaping_espacio_not_safe(rs, tmp_path):
    r, espacio = rs
    outside = tmp_path / "fuera"
    outside.mkdir()
    link = espacio / "escape"
    link.symlink_to(outside)
    lvl, _, _ = r.classify(Action(kind="file_write", description="x",
                                  paths=(link / "x.txt",)))
    assert lvl >= Level.CONFIRM


def test_unknown_command_defaults_to_confirm(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="algo-raro --flag"))
    assert lvl is Level.CONFIRM


def test_pip_install_is_confirm(rs):
    r, _ = rs
    lvl, _, _ = r.classify(Action(kind="shell", description="x",
                                  command="pip install requests"))
    assert lvl is Level.CONFIRM
```

- [ ] **Step 3: Ejecutar y verificar que falla**

Run: `~/.local/share/zara/venv/bin/pytest tests/guardian/test_rules.py -v`
Expected: FAIL (`ModuleNotFoundError: zara.guardian.rules`).

- [ ] **Step 4: Escribir `zara/guardian/rules.py`**

```python
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from .action import Action, Level
from ..config import load_toml

_DEFAULTS = {
    "blocked": [r'\bmkfs\b', r'\bdd\s+if=', r'\brm\s+-rf\s+/(?!mnt/almacen/ZARA)',
                r'/etc/fstab', r'/boot(/|\b)', r':\(\)\s*\{', r'\bshutdown\b',
                r'\breboot\b'],
    "critical": [r'\bsudo\b', r'\bsu\b', r'\bchown\b\s+-R', r'\bchmod\b\s+-R',
                 r'git\s+push\s+.*--force', r'\bpasswd\b', r'\buserdel\b',
                 r'\bvisudo\b', r'\.ssh/', r'\.gnupg/', r'credential',
                 r'id_rsa', r'id_ed25519'],
    "confirm": [r'\bpip\s+install\b', r'\buv\s+pip\s+install\b', r'\bdnf\b',
                r'\bapt\b', r'\bsystemctl\b', r'\bkill\b', r'\bpkill\b',
                r'\bcurl\b.*-o', r'\bwget\b'],
    "safe": [r'^\s*ls\b', r'^\s*cat\b', r'^\s*grep\b', r'^\s*find\b',
             r'^\s*echo\b', r'^\s*pactl\b', r'^\s*playerctl\b', r'^\s*xdg-open\b'],
}

_WRITE_KINDS = {"file_write", "file_delete", "file_move"}


def _compile(patterns: list[str]) -> list[re.Pattern]:
    return [re.compile(p) for p in patterns]


@dataclass
class RuleSet:
    zara_root: Path
    espacio: Path
    blocked: list[re.Pattern]
    critical: list[re.Pattern]
    confirm: list[re.Pattern]
    safe: list[re.Pattern]

    @classmethod
    def from_toml(cls, path: Path, zara_root: Path, espacio: Path) -> "RuleSet":
        cfg = load_toml(path, _DEFAULTS)
        return cls(
            zara_root=zara_root.resolve(), espacio=espacio.resolve(),
            blocked=_compile(cfg["blocked"]), critical=_compile(cfg["critical"]),
            confirm=_compile(cfg["confirm"]), safe=_compile(cfg["safe"]),
        )

    def _haystack(self, action: Action) -> str:
        parts = [action.command or "", action.url or ""]
        parts += [str(p) for p in action.paths]
        return " ".join(parts)

    def _match(self, patterns, text) -> str | None:
        for p in patterns:
            if p.search(text):
                return p.pattern
        return None

    def _path_scope_level(self, action: Action) -> tuple[Level, str, str | None]:
        # Resuelve symlinks para evitar fugas del espacio.
        for raw in action.paths:
            real = Path(raw).resolve()
            try:
                real.relative_to(self.espacio)
                inside_espacio = True
            except ValueError:
                inside_espacio = False
            if not inside_espacio:
                try:
                    real.relative_to(self.zara_root)
                    return Level.CONFIRM, "escritura dentro de ZARA (no espacio)", "scope:zara"
                except ValueError:
                    return Level.CONFIRM, "escritura fuera de ZARA", "scope:fuera"
        return Level.SAFE, "escritura dentro del espacio de Zara", "scope:espacio"

    def classify(self, action: Action) -> tuple[Level, str, str | None]:
        text = self._haystack(action)
        best = (Level.SAFE, "sin coincidencias", None)

        m = self._match(self.blocked, text)
        if m:
            return Level.BLOCKED, "acción prohibida", m
        m = self._match(self.critical, text)
        if m:
            best = max(best, (Level.CRITICAL, "patrón crítico", m), key=lambda t: t[0])
        m = self._match(self.confirm, text)
        if m:
            best = max(best, (Level.CONFIRM, "requiere confirmación", m), key=lambda t: t[0])

        if action.kind in _WRITE_KINDS:
            best = max(best, self._path_scope_level(action), key=lambda t: t[0])

        if best[0] is Level.SAFE:
            m = self._match(self.safe, text)
            if m:
                return Level.SAFE, "comando de solo lectura permitido", m
            # Desconocido → fail-safe
            return Level.CONFIRM, "acción no reconocida; se pide confirmación por seguridad", None
        return best
```

- [ ] **Step 5: Ejecutar y verificar que pasa**

Run: `~/.local/share/zara/venv/bin/pytest tests/guardian/test_rules.py -v`
Expected: PASS (7 tests).

- [ ] **Step 6: Punto de control**

Run: `~/.local/share/zara/venv/bin/pytest -v`
Expected: PASS.

---

### Task 7: Motor Guardian (classify + prepare, con auditoría y copia)

**Files:**
- Create: `/mnt/almacen/ZARA/app/zara/guardian/engine.py`
- Modify: `/mnt/almacen/ZARA/app/zara/guardian/__init__.py` (exportar `Guardian`)
- Test: `/mnt/almacen/ZARA/app/tests/guardian/test_engine.py`

**Interfaces:**
- Consumes: `RuleSet` (Task 6), `Action/Level/Decision` (Task 5), `AuditLog` (Task 3), `backup_path` (Task 4), `BackupError` (Task 1).
- Produces:
  - `Guardian(rules: RuleSet, audit: AuditLog, copias_dir: Path, clock=datetime.now)`.
  - `classify(action: Action) -> Decision`: clasifica, fija `needs_backup = level in {CRITICAL}` **o** (level == CONFIRM y kind in {file_delete, file_move, file_write} con rutas existentes fuera del espacio), registra evento `classify`. No ejecuta nada.
  - `prepare(decision: Decision) -> Decision`: si `is_blocked` → lanza `PermissionError`. Si `needs_backup`, copia cada ruta existente con `backup_path`; si falla → `prepared=False` y re-lanza `BackupError` tras auditar `prepare_failed`. Si OK → `prepared=True`, `backup_at=<ruta>`, audita `prepare`. Una CRITICAL nunca queda `prepared=True` sin copia.
  - `Guardian` NO ejecuta la acción: ejecutar es responsabilidad de capas superiores (planes posteriores) y solo tras confirmación del usuario.
- Produces: `errors`-nivel: reutiliza `BackupError`; `PermissionError` (builtin) para BLOCKED.

- [ ] **Step 1: Escribir el test (falla primero)**

```python
from datetime import datetime
from pathlib import Path
import pytest
from zara.guardian.action import Action, Level
from zara.guardian.rules import RuleSet
from zara.guardian.engine import Guardian
from zara.audit import AuditLog
from zara.errors import BackupError


@pytest.fixture
def guardian(tmp_path):
    espacio = tmp_path / "ZARA" / "espacio"
    espacio.mkdir(parents=True)
    rules = RuleSet.from_toml(tmp_path / "no.toml",
                              zara_root=tmp_path / "ZARA", espacio=espacio)
    audit = AuditLog(tmp_path / "reg", clock=lambda: datetime(2026, 10, 14))
    g = Guardian(rules, audit, copias_dir=tmp_path / "copias",
                 clock=lambda: datetime(2026, 10, 14, 1, 2, 3))
    return g, espacio, tmp_path


def test_classify_critical_sets_backup(guardian):
    g, _, _ = guardian
    d = g.classify(Action(kind="shell", description="x", command="sudo rm -rf /tmp/z"))
    assert d.level is Level.CRITICAL
    assert d.needs_backup is True
    assert d.prepared is False


def test_prepare_blocked_raises(guardian):
    g, _, _ = guardian
    d = g.classify(Action(kind="shell", description="x",
                          command="dd if=/dev/zero of=/dev/sda"))
    assert d.is_blocked
    with pytest.raises(PermissionError):
        g.prepare(d)


def test_prepare_makes_backup_for_critical(guardian):
    g, _, tmp = guardian
    victim = tmp / "Documentos"
    victim.mkdir()
    (victim / "importante.txt").write_text("datos")
    d = g.classify(Action(kind="file_delete", description="borrar docs",
                          command="sudo rm -rf " + str(victim),
                          paths=(victim,)))
    assert d.level is Level.CRITICAL and d.needs_backup
    d2 = g.prepare(d)
    assert d2.prepared is True
    assert d2.backup_at is not None
    assert (d2.backup_at / "importante.txt").read_text() == "datos"


def test_prepare_backup_failure_leaves_unprepared(guardian):
    g, _, tmp = guardian
    missing = tmp / "no-existe"
    d = g.classify(Action(kind="file_delete", description="x",
                          command="sudo rm -rf " + str(missing),
                          paths=(missing,)))
    assert d.needs_backup
    with pytest.raises(BackupError):
        g.prepare(d)
    assert d.prepared is False


def test_classify_is_audited(guardian):
    g, _, tmp = guardian
    g.classify(Action(kind="shell", description="x", command="ls"))
    reg = (tmp / "reg" / "acciones-2026-10-14.jsonl").read_text()
    assert '"event": "classify"' in reg
```

- [ ] **Step 2: Ejecutar y verificar que falla**

Run: `~/.local/share/zara/venv/bin/pytest tests/guardian/test_engine.py -v`
Expected: FAIL (`ModuleNotFoundError: zara.guardian.engine`).

- [ ] **Step 3: Escribir `zara/guardian/engine.py`**

```python
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from typing import Callable
from .action import Action, Level, Decision
from .rules import RuleSet
from ..audit import AuditLog
from ..backup import backup_path
from ..errors import BackupError

_BACKUP_CONFIRM_KINDS = {"file_write", "file_delete", "file_move"}


class Guardian:
    def __init__(self, rules: RuleSet, audit: AuditLog, copias_dir: Path,
                 clock: Callable[[], datetime] = datetime.now):
        self._rules = rules
        self._audit = audit
        self._copias = copias_dir
        self._clock = clock

    def classify(self, action: Action) -> Decision:
        level, reason, matched = self._rules.classify(action)
        needs_backup = level is Level.CRITICAL
        if level is Level.CONFIRM and action.kind in _BACKUP_CONFIRM_KINDS:
            needs_backup = any(Path(p).exists() for p in action.paths)
        decision = Decision(action=action, level=level, reason=reason,
                            matched_rule=matched, needs_backup=needs_backup)
        self._audit.record("classify", {
            "kind": action.kind, "command": action.command,
            "level": level.name, "reason": reason, "matched_rule": matched,
            "needs_backup": needs_backup,
        })
        return decision

    def prepare(self, decision: Decision) -> Decision:
        if decision.is_blocked:
            self._audit.record("blocked", {"command": decision.action.command,
                                           "reason": decision.reason})
            raise PermissionError(
                f"Acción prohibida por el Guardián: {decision.reason}")
        if not decision.needs_backup:
            decision.prepared = True
            self._audit.record("prepare", {"command": decision.action.command,
                                           "backup_at": None})
            return decision
        try:
            last = None
            for p in decision.action.paths:
                if Path(p).exists():
                    last = backup_path(Path(p), self._copias, clock=self._clock)
            if last is None:
                raise BackupError("No había rutas existentes que copiar")
        except BackupError as exc:
            decision.prepared = False
            self._audit.record("prepare_failed", {
                "command": decision.action.command, "error": str(exc)})
            raise
        decision.backup_at = last
        decision.prepared = True
        self._audit.record("prepare", {"command": decision.action.command,
                                        "backup_at": str(last)})
        return decision
```

- [ ] **Step 4: Actualizar `zara/guardian/__init__.py`**

```python
from .action import Level, Action, Decision  # noqa: F401
from .rules import RuleSet  # noqa: F401
from .engine import Guardian  # noqa: F401
```

- [ ] **Step 5: Ejecutar y verificar que pasa**

Run: `~/.local/share/zara/venv/bin/pytest tests/guardian/test_engine.py -v`
Expected: PASS (5 tests).

- [ ] **Step 6: Punto de control final**

Run: `~/.local/share/zara/venv/bin/pytest -v`
Expected: PASS (todos los tests del paquete). (Git diferido; no commitear salvo autorización del usuario.)

---

## Resultado del Plan 1

Al terminar, Zara tiene su **cerebro de seguridad** funcionando y probado: sabe
clasificar cualquier acción, bloquea lo prohibido, exige copia + confirmación en
lo crítico, cae a confirmación ante lo desconocido, y audita todo. Sin este
núcleo, ningún otro plan debe ejecutar acciones. Los planes 2–7 se apoyan en
`Guardian`, `ZaraPaths`, `AuditLog` y `load_toml`.
