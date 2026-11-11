"""MIT-derived generic keyboard table; see PROVENANCE.json."""

from typing import ClassVar

from rich.text import Text
from textual.widgets import DataTable


class NavigationTable(DataTable):
    BINDINGS: ClassVar[list[tuple[str, str, str]]] = [
        ("j", "vim_down", "Next"),
        ("k", "vim_up", "Previous"),
    ]

    def __init__(self, *, vim_keys: bool = True, **kwargs) -> None:
        super().__init__(
            cursor_type="row", show_row_labels=False, cell_padding=0, **kwargs
        )
        self.vim_keys = vim_keys

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        if action in ("vim_down", "vim_up") and not self.vim_keys:
            return False
        return super().check_action(action, parameters)

    def action_vim_down(self) -> None:
        self.action_cursor_down()

    def action_vim_up(self) -> None:
        self.action_cursor_up()

    def fill(
        self, columns: list[tuple[str, int]], rows: list[tuple[str, list[str]]]
    ) -> None:
        cursor = self.cursor_row
        self.clear(columns=True)
        for index, (label, width) in enumerate(columns):
            heading = Text(label)
            heading.truncate(max(1, width - 1), overflow="ellipsis")
            self.add_column(heading, width=width, key=str(index))
        for key, values in rows:
            cells = []
            for value, (_, width) in zip(values, columns, strict=True):
                text = Text(value)
                text.truncate(max(1, width - 1), overflow="ellipsis")
                cells.append(text)
            self.add_row(*cells, key=key)
        if rows:
            self.move_cursor(row=min(cursor, len(rows) - 1))
