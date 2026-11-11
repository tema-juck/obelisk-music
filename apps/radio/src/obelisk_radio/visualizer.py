"""Terminal rendering of immutable DSP frames; never processes audio samples."""

from __future__ import annotations

from rich.text import Text
from textual.widgets import Static

from .audio_pipeline import VisualizerState


class Visualizer(Static):
    can_focus = False

    def __init__(self, state: VisualizerState, **kwargs):
        super().__init__(**kwargs)
        self.state = state
        self.set_class(not state.active, "visualizer-off")

    def on_mount(self) -> None:
        self._timer = self.set_interval(
            1 / self.state.settings.fps, self.refresh_frame, pause=not self.state.active
        )

    def sync_mode(self) -> None:
        self.set_class(not self.state.active, "visualizer-off")
        if self.state.active:
            self._timer.resume()
        else:
            self._timer.pause()
        self.refresh()

    def refresh_frame(self) -> None:
        self.set_class(not self.state.active, "visualizer-off")
        if self.state.active:
            self.refresh()

    def render(self) -> Text:
        width = max(1, self.size.width)
        height = max(1, self.size.height - 1)
        title = f"{self.state.mode.upper()} · z preset · t off"[:width]
        frame = self.state.current()
        if frame is None:
            return Text(title + "\n" + "Audio unavailable"[:width])
        if self.state.mode == "waveform":
            canvas = [[0 for _ in range(width)] for _ in range(height)]
            dots = ((0, 1, 2, 6), (3, 4, 5, 7))
            for x in range(width * 2):
                sample = frame.waveform[min(127, x * 128 // (width * 2))]
                amplitude = max(-1, min(1, sample * self.state.settings.sensitivity))
                y = round((1 - amplitude) * (height * 4 - 1) / 2)
                canvas[y // 4][x // 2] |= 1 << dots[x % 2][y % 4]
            lines = [
                "".join(chr(0x2800 | cell) if cell else " " for cell in row)
                for row in canvas
            ]
        elif self.state.mode == "beat-pulse":
            count = round(frame.pulse * max(1, width // 2))
            pulse = ("●" if frame.onset else "━") * min(width, count * 2)
            lines = [pulse.center(width)] * height
        else:
            blocks = " ▁▂▃▄▅▆▇█"
            levels = [frame.spectrum[min(23, x * 24 // width)] for x in range(width)]
            lines = [
                "".join(
                    blocks[max(0, min(8, round(level * height * 8 - row * 8)))]
                    for level in levels
                )
                for row in reversed(range(height))
            ]
        return Text("\n".join([title, *lines]))
