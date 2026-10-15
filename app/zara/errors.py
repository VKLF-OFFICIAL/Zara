class ZaraError(Exception):
    """Error base del dominio de Zara."""


class ZaraNotMountedError(ZaraError):
    """El almacén /mnt/almacen no está montado."""


class BackupError(ZaraError):
    """No se pudo crear la copia de seguridad."""


class ConfigError(ZaraError):
    """Configuración ausente o inválida."""


class VoiceError(ZaraError):
    """No se pudo sintetizar la voz con ningún motor."""


class BrainError(ZaraError):
    """El cerebro de IA falló o no está disponible."""
