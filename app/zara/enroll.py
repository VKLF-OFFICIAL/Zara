from __future__ import annotations
import sys

from .paths import ZaraPaths
from .config import load_toml
from .audio.mic import Microphone
from .audio.identity import SpeakerVerifier

_FRASES = [
    "Hola Zara, soy el Señor VK.",
    "Zara, a partir de ahora solo me obedeces a mí.",
    "Enciende la música y baja el volumen, por favor.",
    "Zara, prepárate para asistirme en mis proyectos.",
]


def main() -> int:
    paths = ZaraPaths.resolve()
    cfg = load_toml(paths.config / "zara.toml", {"umbral_voz": 0.72})
    ref = paths.ssd_voz / "huella_vk.npy"
    verifier = SpeakerVerifier(ref, threshold=float(cfg["umbral_voz"]))
    mic = Microphone()

    print("=== Inscripción de voz del Señor VK ===")
    print("Lea en voz alta cada frase cuando se le indique (3 segundos cada una).\n")
    samples = []
    for i, frase in enumerate(_FRASES, 1):
        input(f"[{i}/{len(_FRASES)}] Pulse Enter y diga: «{frase}» ")
        print("  grabando…", flush=True)
        audio = mic.record_seconds(3.5)
        samples.append(audio)
        print("  ✓ capturada\n")

    verifier.enroll(samples)
    print(f"Huella de voz guardada en {ref}")
    # Verificación rápida con la última muestra
    ok, score = verifier.verify(samples[-1])
    print(f"Comprobación: aceptado={ok} (similitud {score:.3f}, umbral {verifier.threshold})")
    print("Listo, Señor VK. Zara ya solo le obedecerá a usted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
