"""Pruebas unitarias para config.py (ZARA v2.0)."""

import os
import re
from pathlib import Path
import pytest

import config


def test_base_dir_exists():
    assert config.BASE_DIR.is_dir()
    assert (config.BASE_DIR / "config.py").is_file()


def test_data_and_cache_directories():
    assert config.DATA_DIR.is_dir()
    assert config.CACHE_DIR.is_dir()
    assert config.MODELS_DIR.is_dir()


def test_system_prompt_dynamic_date():
    prompt = config.get_system_prompt()
    assert "Eres ZARA" in prompt
    assert "Señor VK" in prompt or "VK" in prompt
    # Verificar que contiene la fecha actual inyectada
    assert "FECHA Y HORA ACTUAL:" in prompt
    assert "2026" in prompt
    assert "Europa/Madrid" in prompt


def test_audio_and_vad_defaults():
    assert config.SAMPLE_RATE == 16000
    assert config.FRAME_SIZE == 512
    assert config.VAD_MIN_SILENCE_MS >= 500
    assert config.VAD_SPEECH_PAD_MS >= 200
    assert 0.4 <= config.VAD_THRESHOLD <= 0.8  # Threshold neuronal óptimo anti-ruido


def test_forbidden_commands_regex():
    # Comandos destructivos deben ser bloqueados
    destructive_cmds = [
        "rm -rf /",
        "rm -rf / --no-preserve-root",
        "rm -rf /etc",
        "rm -rf /var/log",
        "mkfs.ext4 /dev/sda1",
        "mkfs -t vfat /dev/sdb",
        "dd if=/dev/zero of=/dev/sda",
        "Format-Volume -DriveLetter C",
        "diskpart clean",
    ]
    for cmd in destructive_cmds:
        blocked = any(re.search(pat, cmd, re.IGNORECASE) for pat in config.FORBIDDEN_COMMANDS_REGEX)
        assert blocked, f"El comando destructivo '{cmd}' debió ser bloqueado por los regex de seguridad"

    # Comandos seguros no deben ser bloqueados
    safe_cmds = [
        "ls -la",
        "dir C:\\",
        "Get-Process",
        "echo hola",
        "python --version",
        "git status",
        "rm archivo_temporal.txt",
        "docker ps",
    ]
    for cmd in safe_cmds:
        blocked = any(re.search(pat, cmd, re.IGNORECASE) for pat in config.FORBIDDEN_COMMANDS_REGEX)
        assert not blocked, f"El comando seguro '{cmd}' fue bloqueado erróneamente"


def test_version_is_prerelease_and_consistent():
    import re

    assert re.fullmatch(r"0\.\d+\.\d+", config.VERSION), "ZARA aún no es 1.0: debe ser 0.y.z"
    assert config.RELEASE_STAGE in ("alpha", "beta", "rc")
    assert config.VERSION_FULL == f"{config.VERSION}-{config.RELEASE_STAGE}"
    assert config.VERSION_FULL in config.APP_TITLE
    assert "v2" not in config.APP_TITLE


def test_resolve_whisper_model_depends_on_device():
    assert config.resolve_whisper_model("auto", "cuda") == config.WHISPER_MODEL_GPU
    assert config.resolve_whisper_model("auto", "cpu") == config.WHISPER_MODEL_CPU
    # en CPU nunca debe elegirse el modelo grande por defecto (era la causa de la lentitud)
    assert "large" not in config.resolve_whisper_model("auto", "cpu")
    # un valor explícito del usuario se respeta siempre
    assert config.resolve_whisper_model("base", "cuda") == "base"


def test_legacy_env_template_model_is_ignored_on_cpu(monkeypatch):
    """La plantilla antigua fijaba large-v3-turbo en todos los .env; en CPU no debe cargarse."""
    assert config.resolve_whisper_model(config.WHISPER_MODEL_GPU, "cpu") == config.WHISPER_MODEL_CPU
    assert config.resolve_whisper_model(config.WHISPER_MODEL_GPU, "cuda") == config.WHISPER_MODEL_GPU
    monkeypatch.setattr(config, "WHISPER_FORCE", True)
    assert config.resolve_whisper_model(config.WHISPER_MODEL_GPU, "cpu") == config.WHISPER_MODEL_GPU


def test_beam_is_capped_on_cpu(monkeypatch):
    assert config.resolve_beam_size(5, "cpu") == 1
    assert config.resolve_beam_size(5, "cuda") == 5
    monkeypatch.setattr(config, "WHISPER_FORCE", True)
    assert config.resolve_beam_size(5, "cpu") == 5


LEGACY_ENV = """# plantilla antigua
GEMINI_API_KEY="mi-clave-secreta"
ZARA_DEFAULT_BRAIN="auto"
ZARA_WHISPER_MODEL="deepdml/faster-whisper-large-v3-turbo-ct2"
ZARA_WHISPER_BEAM_SIZE=5
ZARA_VAD_MIN_SILENCE_MS=700
OLLAMA_MODEL="qwen2.5-coder:7b"
ZARA_EDGE_VOICE="es-ES-ElviraNeural"
ZARA_WS_PORT=8765"""


def test_legacy_env_is_migrated_once_keeping_secrets_and_choices(tmp_path):
    env = tmp_path / ".env"
    env.write_text(LEGACY_ENV, encoding="utf-8")  # sin salto final, como deja el Bloc de notas
    removed = config._migrate_legacy_env(env)
    assert set(removed) == {
        "ZARA_DEFAULT_BRAIN", "ZARA_WHISPER_MODEL", "ZARA_WHISPER_BEAM_SIZE",
        "ZARA_VAD_MIN_SILENCE_MS", "ZARA_WS_PORT",
    }
    text = env.read_text(encoding="utf-8")
    assert 'GEMINI_API_KEY="mi-clave-secreta"' in text          # las claves nunca se tocan
    assert 'OLLAMA_MODEL="qwen2.5-coder:7b"' in text            # ni lo que eligió el usuario
    assert 'ZARA_EDGE_VOICE="es-ES-ElviraNeural"' in text
    assert config.ENV_SCHEMA_MARKER in text
    assert (tmp_path / ".env.bak").read_text(encoding="utf-8") == LEGACY_ENV  # copia de seguridad

    # Segunda ejecución: no cambia nada, aunque ahora el usuario elija "auto" a propósito
    env.write_text(text + 'ZARA_DEFAULT_BRAIN="auto"\n', encoding="utf-8")
    assert config._migrate_legacy_env(env) == []
    assert 'ZARA_DEFAULT_BRAIN="auto"' in env.read_text(encoding="utf-8")


def test_settings_priority_env_over_dotenv_over_repo(tmp_path, monkeypatch):
    dotenv = tmp_path / ".env"
    ajustes = tmp_path / "ajustes.env"
    dotenv.write_text('ZARA_T_A="dotenv"\nZARA_T_B="dotenv"\n', encoding="utf-8")
    ajustes.write_text('ZARA_T_A="repo"\nZARA_T_B="repo"\nZARA_T_C="repo"\n', encoding="utf-8")
    for k in ("ZARA_T_A", "ZARA_T_B", "ZARA_T_C"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZARA_T_A", "sistema")
    config._load_env_file(dotenv)
    config._load_env_file(ajustes)
    import os
    assert os.environ["ZARA_T_A"] == "sistema"  # 1. entorno del sistema
    assert os.environ["ZARA_T_B"] == "dotenv"   # 2. .env local
    assert os.environ["ZARA_T_C"] == "repo"     # 3. config/ajustes.env


def test_repo_settings_file_has_no_secrets():
    """config/ajustes.env es público: no puede contener claves con valor."""
    from pathlib import Path

    text = (Path(config.BASE_DIR) / "config" / "ajustes.env").read_text(encoding="utf-8")
    for line in text.splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            if any(s in key.upper() for s in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
                assert value.strip().strip('"') == "", f"{key} no debe tener valor en el repo"
    assert 'ZARA_DEFAULT_BRAIN="antigravity"' in text


def test_persist_env_var_marks_file_as_current(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setenv("ZARA_DEFAULT_BRAIN", "")  # persist_env_var escribe en os.environ: restaurarlo
    (tmp_path / ".env").write_text('GEMINI_API_KEY="x"', encoding="utf-8")  # sin salto final
    config.persist_env_var("ZARA_DEFAULT_BRAIN", "auto")
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'GEMINI_API_KEY="x"\n' in text
    assert config.ENV_SCHEMA_MARKER in text
    assert 'ZARA_DEFAULT_BRAIN="auto"' in text
    # una elección hecha desde el panel no la borra la migración
    assert config._migrate_legacy_env(tmp_path / ".env") == []
