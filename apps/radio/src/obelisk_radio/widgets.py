"""Plain text is never interpreted as broadcaster-provided markup."""

from textual.widgets import Static


class PlainText(Static):
    def __init__(self, content: str = "", **kwargs):
        super().__init__(content, markup=False, **kwargs)
