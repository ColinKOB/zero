"""Makes Zero's message tones: short sounds a Zip's notifications play, one per Zip by default (Colin, Oct 11: "can
we make personalized message tones for each of the agents?"). Each is synthesized here, so there is nothing to
license, and each is under a second and a half so it reads as a message, not a ringtone.

Run: python3 ios/scripts/make-tones.py   (writes ios/Taffy/Resources/Tones/zero-<name>.caf; needs numpy and afconvert)
The names here are the ones the hub sends (hub/taffy_hub/tones.py) and the app lists (Views/MessageTones.swift).
"""

import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

RATE = 44100
OUT = Path(__file__).resolve().parents[1] / "Taffy" / "Resources" / "Tones"


def t(sec: float) -> np.ndarray:
    return np.arange(int(sec * RATE)) / RATE


def env(n: int, attack: float = 0.004, decay: float = 0.35) -> np.ndarray:
    """A quick rise, then an exponential fall (`decay` seconds to about a third)."""
    x = np.arange(n) / RATE
    rise = np.clip(x / attack, 0, 1)
    return rise * np.exp(-x / decay)


def note(freq: float, sec: float, partials: list[tuple[float, float, float]], attack: float = 0.004) -> np.ndarray:
    """A note from (ratio, level, decay) partials: bells have stretched ratios, mallets fast upper partials."""
    x = t(sec)
    out = np.zeros_like(x)
    for ratio, level, decay in partials:
        out += level * np.sin(2 * np.pi * freq * ratio * x) * env(len(x), attack, decay)
    return out


def at(total: float, *parts: tuple[float, np.ndarray]) -> np.ndarray:
    """Places sounds at start times (seconds) on one track."""
    out = np.zeros(int(total * RATE))
    for start, s in parts:
        i = int(start * RATE)
        n = min(len(s), len(out) - i)
        out[i:i + n] += s[:n]
    return out


def pluck(freq: float, sec: float, brightness: float = 0.5, seed: int = 1) -> np.ndarray:
    """Karplus-Strong string: a burst of noise through a delay line that loses its highs each pass."""
    rng = np.random.default_rng(seed)
    period = int(RATE / freq)
    buf = rng.uniform(-1, 1, period)
    out = np.zeros(int(sec * RATE))
    for i in range(len(out)):
        j = i % period
        out[i] = buf[j]
        buf[j] = 0.996 * (brightness * buf[j] + (1 - brightness) * buf[(j + 1) % period])
    return out * env(len(out), 0.001, sec / 2.5)


MARIMBA = [(1, 1.0, 0.28), (3.93, 0.25, 0.05), (9.2, 0.06, 0.02)]
BELL = [(1, 1.0, 0.7), (2.76, 0.45, 0.35), (5.4, 0.25, 0.18), (8.93, 0.1, 0.08)]
GLASS = [(1, 1.0, 0.6), (2.32, 0.3, 0.3), (4.25, 0.12, 0.15)]
SOFT = [(1, 1.0, 0.45), (2, 0.18, 0.2), (3, 0.06, 0.1)]


def chime() -> np.ndarray:
    return at(1.3, (0, note(1318.5, 1.0, BELL)), (0.16, note(987.8, 1.1, BELL)))


def marimba() -> np.ndarray:
    return at(0.9, (0, note(523.3, 0.6, MARIMBA)), (0.11, note(659.3, 0.7, MARIMBA)))


def bell() -> np.ndarray:
    return at(1.4, (0, note(880.0, 1.4, BELL)))


def pluckt() -> np.ndarray:
    return at(1.0, (0, pluck(440.0, 0.7, 0.5, 3)), (0.12, pluck(659.3, 0.85, 0.5, 4)))


def knock() -> np.ndarray:
    def one():
        x = t(0.18)
        body = np.sin(2 * np.pi * 720 * x) * env(len(x), 0.0008, 0.035)
        click = np.random.default_rng(7).uniform(-1, 1, len(x)) * env(len(x), 0.0003, 0.004)
        return body + 0.35 * click
    return at(0.5, (0, one()), (0.14, one() * 0.8))


def glass() -> np.ndarray:
    return at(1.3, (0, note(1760.0, 1.2, GLASS)), (0.09, note(2349.3, 1.1, GLASS) * 0.5))


def bubble() -> np.ndarray:
    def blip(f0, f1, sec=0.11):
        x = t(sec)
        f = np.linspace(f0, f1, len(x))
        return np.sin(2 * np.pi * np.cumsum(f) / RATE) * env(len(x), 0.002, 0.05)
    return at(0.4, (0, blip(500, 1100)), (0.12, blip(700, 1500)))


def harp() -> np.ndarray:
    notes = [523.3, 659.3, 784.0, 1046.5]
    return at(1.3, *[(i * 0.07, pluck(f, 1.0, 0.55, 10 + i) * (1 - i * 0.1)) for i, f in enumerate(notes)])


def pop() -> np.ndarray:
    return at(0.6, (0, note(784.0, 0.5, SOFT, attack=0.006)))


def ding() -> np.ndarray:
    return at(1.1, (0, note(659.3, 0.8, SOFT)), (0.18, note(830.6, 0.9, SOFT)))


TONES = {"chime": chime, "marimba": marimba, "bell": bell, "pluck": pluckt, "knock": knock, "glass": glass,
         "bubble": bubble, "harp": harp, "pop": pop, "ding": ding}


def write(name: str, samples: np.ndarray) -> Path:
    fade = min(len(samples), int(0.03 * RATE))
    samples = samples.copy()
    samples[-fade:] *= np.linspace(1, 0, fade)
    samples = samples / (np.max(np.abs(samples)) or 1) * 0.7
    pcm = (samples * 32767).astype(np.int16)
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as d:
        wav = Path(d) / f"{name}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes(pcm.tobytes())
        caf = OUT / f"zero-{name}.caf"
        subprocess.run(["afconvert", "-f", "caff", "-d", "LEI16", str(wav), str(caf)], check=True)
    return caf


def main() -> int:
    for name, make in TONES.items():
        path = write(name, make())
        print(f"{path.name}: {path.stat().st_size // 1024} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
