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
