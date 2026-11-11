# UX contract

Obelisk is a Spotify Connect controller for keyboard users. The task brief,
AGENTS.md, README, and Spotify's current Web API contracts define product scope.
Spotify remains a Connect controller. The Issue #16 radio development feature
adds independent local MP3/AAC playback; no Spotify decoding, downloads, queue
editing, or lyrics are introduced. UI copy is
English; catalog metadata is Unicode. No locale-specific business flows exist.
Visual intent and runtime token ownership are documented in DESIGN.md.

## Canonical UI map

| Capability | Owner | Variants | Verification |
|---|---|---|---|
| Table selection | browser.NavigationTable | Library, catalog, devices; single selected row | Headless keyboard tests |
| Lists | NavigationTable / CatalogTable | Search, playlist/album tracks, artist albums | Pagination and null-row tests |
| Form | Textual Input + screens.CatalogScreen | Search only; 256 characters; 300 ms debounce | Input-focus/debounce tests |
| Scrollbars | obelisk.tcss | Quiet scrolling tables, scrollable help | Compact layout tests |
| Feedback | widgets.FeedbackBar; per-view PlainText status | Command feedback, loading, empty, restricted, error | Error and cooldown tests |

## Navigation

Library Tab/Shift+Tab changes panes; Escape returns to playlists. `/` opens search
with input focus. F1/F2/F3 select tracks/albums/artists. Down or Tab moves to
results; Tab/Shift+Tab cycles input and results. j/k navigate only where configured
and never while typing. Enter plays a track, opens an album, or opens an artist's
albums. a/A opens the selected track's album/first artist. Escape closes the top
view and restores the prior focus and selected row. Help and devices use the same
stack restoration. Search/category changes reset row selection; pagination keeps
it. On narrow screens, essential actions remain available by keyboard.

## Async and resilience

Screens emit messages; application-managed workers perform network reads through
SpotifyLibrary and SpotifyClient. Input edits invalidate results immediately and
cancel superseded reads. Screen revisions reject late responses, including after
close. Cache request tokens reject older same-key reads. Page offsets are API
positions, independent of selection. Caches live only in the active session.

Playback mutations remain serialized by the application's existing command gate;
closing or changing browsing views does not cancel them. Ambiguous network
failures never trigger automatic mutation retry or alternate playback behavior.
Album playback uses album context and original offsets; search-track playback
uses exactly the selected URI. Spotify permission/resource restrictions stay in
the browsing view; authorization failures direct users back to login. Shared
rate-limit cooldowns apply across all endpoints. Refresh retries only on user
action; it does not loop through restricted endpoints.

## Feedback and playback

Loading, empty, unavailable, and failed requests use explicit text with recovery
actions. Existing command feedback survives background polls. Now Playing keeps
the last observed state and interpolates known playing positions locally without
additional API requests. Pause freezes it; confirmed resume restarts it. Unknown
position/duration remains safe and elapsed time never exceeds duration. A later
API observation replaces interpolation, including seeks or track changes.

## Verification

Run Ruff, full pytest on Python 3.13/3.14, distribution builds, isolated wheel
smoke, and native binary smoke. Headless tests cover both themes and 120×35,
80×24, 60×20, and 40×12, nested focus restoration, stale reads, failure recovery,
and playback. Live Spotify/device behavior still requires a real account smoke
test; mocks cannot establish account-specific API access or hardware behavior.


## Radio contract (Issue #16)

Source: Issue #16 and the current P3 brief; API/security/policy evidence is linked
in docs/radio.md. RadioScreen reuses NavigationTable for selection, Input for
filters/custom entry, PlainText for status, and HelpScreen for shortcuts. Enter
in input never plays; delayed reads retain keyboard access and reject stale
results after edits, mode changes, and close. Favorites/custom deletion is
reversible by re-adding the displayed station; no confirmation blocks routine
list edits. RadioStore preserves corrupt bytes and refuses writes after failure.

R from the library enters Radio without Spotify authorization. Radio owns its
LIVE state and volume. It never invokes Spotify endpoints. Source switching does
not cancel Spotify mutations; pending mutations delay entry. Spotify reads/polls
are suspended; leaving Radio stops/reaps its child before returning. Known active
Spotify playback requires an explicit user pause first. Source isolation does
not establish policy compliance: Spotify §III.5 is a public-release blocker.
