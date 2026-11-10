---
version: alpha
name: Obelisk Music
description: A quiet keyboard-first Spotify Connect terminal controller.
colors:
  primary: "#91b7ff"
  background: "#0c1118"
  foreground: "#dbe5f1"
  muted: "#9aabbe"
typography:
  mono:
    fontFamily: "monospace"
omitted:
  - section: rounded
    reason: Terminal cells have square edges; there are no cards or buttons.
  - section: spacing
    reason: Layout uses terminal cells and lines rather than CSS pixel units.
  - section: components
    reason: Shared Textual component owners and states are documented below.
---

# Obelisk Music design

## Overview

A terminal-native music browser for people who keep their hands on the keyboard.
Cliamp informs the restraint and compact density; no code or design assets are
copied. The persistent progress strip is the visual signature. Avoid dashboard
cards, heavy borders, animations, and a permanent shortcut sidebar. The UI is
English; Spotify names remain Unicode and use the user's terminal font.

## Colors

Runtime `Theme` definitions in `app.py` are canonical (existing-runtime ownership).
`obelisk.tcss` consumes semantic `$background`, `$foreground`, `$accent`, `$panel`,
`$muted`, `$selection`, and `$on-selection` variables throughout all screens.
Midnight retains its blue accent. Mono uses a neutral background `#101010`,
foreground `#dddddd`, accent `#eeeeee`, muted text `#aaaaaa`, selection `#383838`,
and selection text `#ffffff`. Themes change color, never navigation or geometry.
Headless tests check both themes and prohibit fixed colors in the stylesheet.

## Typography

One terminal cell grid, bold titles and selected rows, ordinary weight elsewhere.
Names are plain Rich Text, never markup. Truncation uses an ellipsis; full item
metadata is available in album and artist views. Terminal/font choice owns glyph
rendering and script coverage.

## Layout

At 70 columns and above, library playlists and tracks share two panes. Narrower
terminals show one pane; Tab and Escape retain navigation. At less than 55 columns
or 16 lines, margins shrink. Catalog views retain the bottom five lines for Now
Playing and feedback. Search input occupies one line. Verify 120×35, 80×24,
60×20, and 40×12, with at least one navigable result visible.

## Elevation & Depth

Tonal selection provides hierarchy without shadows or heavy borders. Help and
device overlays cover the browsing area; catalog views preserve Now Playing.

## Shapes

Square terminal cells, thin separator rules, no decorative containers.

## Components

`NavigationTable` owns arrow/j/k navigation, truncation, and selected row styling.
`CatalogTable` reuses it for tracks, albums and artists. A row highlight indicates
selection; ♫ independently identifies matching playing URIs. Unavailable items
use text, not color alone. `PlainText` owns safe metadata rendering. Textual Input
owns editing; screen shortcuts yield to typing. `FeedbackBar` owns timed command
feedback; connection state remains separate. No animated loading indicators.

## Do's and Don'ts

- Keep the current route, loading/error state, and next keyboard action legible.
- Preserve focus and row selection when returning from a nested view.
- Do not introduce a second palette in screen-specific styles.
- Do not execute commands merely because a search character matches a shortcut.
