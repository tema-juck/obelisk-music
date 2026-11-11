"""Radio-only PCM measurements. No playback, UI, files or network access."""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

import numpy as np

SAMPLE_RATE = 48_000
WINDOW = 2048
CHANNELS = 2
SAMPLE_BYTES = 2 * CHANNELS
PCM_FORMAT = "s16le" if sys.byteorder == "little" else "s16be"
MODES = ("spectrum", "beat-pulse", "waveform", "off")


@dataclass(frozen=True)
class AudioFrame:
    timestamp: float
    rms: float
    low: float
    mid: float
    high: float
    spectrum: tuple[float, ...]
    waveform: tuple[float, ...]
    flux: float
    onset: bool
    pulse: float


class PcmRing:
    """Two seconds of interleaved PCM, addressed by absolute sample index."""

    capacity = 2 * SAMPLE_RATE * SAMPLE_BYTES

    def __init__(self):
        self.data = bytearray()
        self.end = 0

    def append(self, pcm: bytes) -> None:
        if len(pcm) % SAMPLE_BYTES:
            raise ValueError("PCM must contain complete stereo s16 frames")
        self.end += len(pcm) // SAMPLE_BYTES
        self.data.extend(pcm[-self.capacity :])
        if len(self.data) > self.capacity:
            del self.data[: len(self.data) - self.capacity]

    def window(self, end: int) -> bytes | None:
        start = end - WINDOW
        base = self.end - len(self.data) // SAMPLE_BYTES
        if start < base or end > self.end:
            return None
        offset = (start - base) * SAMPLE_BYTES
        return bytes(self.data[offset : offset + WINDOW * SAMPLE_BYTES])


class Analyzer:
    def __init__(self, sensitivity: float = 1.0):
        self.sensitivity = sensitivity
        self.window = np.hanning(WINDOW)
        frequencies = np.fft.rfftfreq(WINDOW, 1 / SAMPLE_RATE)
        edges = np.geomspace(30, 16_000, 25)
        self.bands = [
            (frequencies >= left) & (frequencies < right)
            for left, right in zip(edges[:-1], edges[1:], strict=True)
        ]
        self.regions = [
            (frequencies >= left) & (frequencies < right)
            for left, right in ((20, 250), (250, 4000), (4000, 20_000))
        ]
        self.previous = np.zeros(WINDOW // 2 + 1)
        self.smoothed = np.zeros(24)
        self.noise = 0.001
        self.flux_mean = 0.0
        self.flux_deviation = 0.0
        self.last_onset = -math.inf
        self.last_time: float | None = None
        self.pulse = 0.0

    def analyze(self, pcm: bytes, timestamp: float) -> AudioFrame:
        if len(pcm) != WINDOW * SAMPLE_BYTES or not math.isfinite(timestamp):
            raise ValueError("Expected one finite-timestamp PCM analysis window")
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        samples = samples.reshape(-1, CHANNELS).mean(axis=1) / 32768
        rms = float(np.sqrt(np.mean(samples * samples)))
        # Hann coherent-gain normalization: a bin-centred full-scale sine is 1.
        magnitude = np.abs(np.fft.rfft(samples * self.window)) * (2 / self.window.sum())
        magnitude[0] *= 0.5
        magnitude[-1] *= 0.5
        energy = [float(np.sqrt(np.sum(magnitude[mask] ** 2))) for mask in self.regions]
        levels = np.array(
            [float(magnitude[mask].max()) if mask.any() else 0 for mask in self.bands]
        )
        levels = np.clip(
            (20 * np.log10(np.maximum(levels * self.sensitivity, 1e-6)) + 60) / 60,
            0,
            1,
        )
        dt = (
            max(0.001, min(0.25, timestamp - self.last_time))
            if self.last_time is not None
            else WINDOW / SAMPLE_RATE
        )
        # Rapid rises, slower falls; coefficients are independent of display FPS.
        tau = np.where(levels > self.smoothed, 0.025, 0.18)
        self.smoothed += (levels - self.smoothed) * (1 - np.exp(-dt / tau))
        flux = float(np.maximum(magnitude - self.previous, 0).sum())
        threshold = max(
            0.02 / self.sensitivity, self.flux_mean + 2.5 * self.flux_deviation
        )
        onset = (
            flux > threshold
            and rms > max(0.003 / self.sensitivity, self.noise * 1.8)
            and timestamp - self.last_onset >= 0.18
        )
        if onset:
            self.last_onset = timestamp
        alpha = 1 - math.exp(-dt / 0.8)
        self.flux_deviation += alpha * (
            abs(flux - self.flux_mean) - self.flux_deviation
        )
        self.flux_mean += alpha * (flux - self.flux_mean)
        # Quiet passages lower the floor faster than loud passages raise it.
        self.noise += (rms - self.noise) * (
            1 - math.exp(-dt / (0.2 if rms < self.noise else 8))
        )
        target = min(1.0, energy[0] * self.sensitivity * 2)
        self.pulse = max(
            1.0 if onset else self.pulse * math.exp(-dt / 0.2),
            self.pulse + (target - self.pulse) * (1 - math.exp(-dt / 0.035)),
        )
        self.previous = magnitude
        self.last_time = timestamp
        # Preserve signed real amplitudes, including peaks between display columns.
        chunks = samples.reshape(128, -1)
        waveform = chunks[np.arange(128), np.abs(chunks).argmax(axis=1)]
        return AudioFrame(
            timestamp,
            rms,
            *energy,
            tuple(self.smoothed),
            tuple(waveform),
            flux,
            onset,
            self.pulse,
        )
