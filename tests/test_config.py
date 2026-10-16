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
