import math

import numpy as np
import pytest

from obelisk_radio.audio_analysis import (
    SAMPLE_BYTES,
    SAMPLE_RATE,
    WINDOW,
    Analyzer,
    PcmRing,
)
from obelisk_radio.config import ConfigError, Settings, load_settings


def pcm(samples):
    mono = np.clip(np.asarray(samples) * 32768, -32768, 32767).astype(np.int16)
    return np.repeat(mono[:, None], 2, axis=1).tobytes()


def tone(frequency, amplitude=0.5):
    return pcm(
        amplitude * np.sin(2 * np.pi * frequency * np.arange(WINDOW) / SAMPLE_RATE)
    )


def test_silence_has_no_energy_flux_or_pulse():
    analyzer = Analyzer()
    for i in range(100):
        frame = analyzer.analyze(bytes(WINDOW * SAMPLE_BYTES), i / 30)
        assert (
            frame.rms
            == frame.low
            == frame.mid
            == frame.high
            == frame.flux
            == frame.pulse
            == 0
        )
        assert not frame.onset
        assert not any(frame.spectrum) and not any(frame.waveform)


@pytest.mark.parametrize(
    "frequency,region", [(93.75, "low"), (1500, "mid"), (9000, "high")]
)
def test_normalized_rms_and_frequency_regions(frequency, region):
    frame = Analyzer().analyze(tone(frequency), 0)
    assert frame.rms == pytest.approx(0.5 / math.sqrt(2), abs=5e-5)
    assert getattr(frame, region) == pytest.approx(0.5 * math.sqrt(1.5), abs=0.002)
    for other in {"low", "mid", "high"} - {region}:
        assert getattr(frame, region) > 100 * getattr(frame, other)
    assert max(frame.spectrum) > 0.5
    assert max(frame.waveform) <= 0.5 and min(frame.waveform) >= -0.5


def test_constant_tone_settles_and_abrupt_change_has_flux():
    analyzer = Analyzer()
    for i in range(60):
        steady = analyzer.analyze(tone(1500), i / 30)
    assert steady.flux == 0 and not steady.onset
    changed = analyzer.analyze(tone(9000), 2)
    assert changed.flux > 0.5 and changed.onset


def test_impulse_positive_flux_attack_decay_and_refractory():
    analyzer = Analyzer()
    silence = bytes(WINDOW * SAMPLE_BYTES)
    analyzer.analyze(silence, 0)
    signal = np.zeros(WINDOW)
    signal[WINDOW // 2] = 0.9
    impulse = pcm(signal)
    hit = analyzer.analyze(impulse, 0.04)
    assert hit.onset and hit.pulse == 1 and hit.flux > 0
    release = analyzer.analyze(silence, 0.08)
    assert 0 < release.pulse < hit.pulse
    blocked = analyzer.analyze(impulse, 0.12)
    assert not blocked.onset
    for i in range(1, 31):
        quiet = analyzer.analyze(silence, 0.12 + i / 30)
    assert quiet.pulse < 0.01


def test_periodic_rhythmic_pulses_are_onsets_not_bpm():
    analyzer = Analyzer()
    onsets = []
    for i in range(150):
        hit = i % 15 == 0
        frame = analyzer.analyze(
            tone(93.75) if hit else bytes(WINDOW * SAMPLE_BYTES), i / 30
        )
        if frame.onset:
            onsets.append(i)
    assert onsets == list(range(0, 150, 15))
    assert not hasattr(frame, "bpm")


def test_white_noise_floor_adapts_and_all_regions_have_energy():
    rng = np.random.default_rng(17)
    analyzer = Analyzer()
    detections = []
    for i in range(300):
        frame = analyzer.analyze(pcm(rng.normal(0, 0.02, WINDOW)), i / 30)
        detections.append(frame.onset)
    assert frame.low > 0 and frame.mid > 0 and frame.high > 0
    assert analyzer.noise > 0.01
    assert sum(detections[-100:]) <= 2


def test_spectrum_attack_decay_and_sensitivity():
    quiet = bytes(WINDOW * SAMPLE_BYTES)
    analyzer = Analyzer()
    first = analyzer.analyze(tone(1500), 0)
    second = analyzer.analyze(tone(1500), 1 / 30)
    decay = analyzer.analyze(quiet, 2 / 30)
    assert max(first.spectrum) < max(second.spectrum)
    assert 0 < max(decay.spectrum) < max(second.spectrum)
    low = Analyzer(0.25).analyze(tone(1500, 0.02), 0)
    high = Analyzer(4).analyze(tone(1500, 0.02), 0)
    assert max(high.spectrum) > max(low.spectrum)


def test_ring_bounds_absolute_timestamps_and_invalid_framing():
    ring = PcmRing()
    assert ring.window(WINDOW) is None
    block = tone(1500)
    for _ in range(200):
        ring.append(block)
        assert len(ring.data) <= ring.capacity
    assert ring.end == 200 * WINDOW
    assert ring.window(ring.end) == block
    assert ring.window(WINDOW) is None
    assert ring.window(ring.end + 1) is None
    with pytest.raises(ValueError, match="complete"):
        ring.append(b"x")


@pytest.mark.parametrize("value", [b"x", bytes(WINDOW * SAMPLE_BYTES - 4)])
def test_analyzer_rejects_incomplete_windows(value):
    with pytest.raises(ValueError):
        Analyzer().analyze(value, 0)


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf")])
def test_analyzer_rejects_nonfinite_timestamps(timestamp):
    with pytest.raises(ValueError):
        Analyzer().analyze(tone(1500), timestamp)


@pytest.mark.parametrize(
    "data",
    [
        {"mode": "random"},
        {"fps": 9},
        {"fps": 61},
        {"fps": True},
        {"sensitivity": 0.2},
        {"sensitivity": 4.1},
        {"sensitivity": float("nan")},
        {"enabled": "yes"},
        {"unknown": 1},
    ],
)
def test_invalid_visualizer_configuration(tmp_path, data):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings.model_validate({"visualizer": data})
    path = tmp_path / "config.toml"
    path.write_text('[visualizer]\nmode="private-invalid-value"')
    with pytest.raises(ConfigError, match="visualizer.mode") as error:
        load_settings(path)
    assert "private-invalid-value" not in str(error.value)


@pytest.mark.parametrize("mode", ["spectrum", "beat-pulse", "waveform", "off"])
def test_visualizer_defaults_and_valid_modes(mode):
    assert not Settings().visualizer.enabled
    settings = Settings.model_validate(
        {"visualizer": {"enabled": True, "mode": mode, "fps": 60, "sensitivity": 4.0}}
    )
    assert settings.visualizer.mode == mode
