# P2 music experience

These features are prepared for v0.3.0-alpha.1 (package version `0.3.0a1`),
not yet published. Published alpha.4 binaries do not include P2. See the
[prepared release notes](releases/v0.3.0-alpha.1.md) and [changelog](../CHANGELOG.md).

## Search and browsing

Press `/` after login to search Spotify. Input is debounced for 300 ms. F1 searches
tracks, F2 albums, and F3 artists. Down, Enter, or Tab moves from the input to the
results. Arrows and configured j/k navigate results; Tab/Shift+Tab switch focus.
Search characters never trigger playback, device, refresh, help, or quit actions.
`/` from results refocuses the input. Escape returns to the previous view and focus.

Enter plays exactly the selected search track, opens an album's metadata/tracks,
or opens an artist's available metadata and albums/singles. From a selected track,
`a` opens its album and `A` opens its first credited artist when Spotify supplies
those identifiers. Album rows also support `A` for their first credited artist.
Unavailable/local/restricted tracks stay visible but cannot be played.

Enter on **Load more…** fetches another page. Search and artist albums use pages
of 10; album tracks use pages of up to 50. Pages and metadata are cached for the
active session. Search categories have independent offsets/caches. Search stops
at Spotify's maximum offset of 1000. Refresh (`r` with results focused) clears the
current view's cache and reloads from the first page. New login clears all caches.
Closing or editing a view cancels reads and rejects late results; playback commands
continue independently. Changing selection never starts playback.

Album tracks play using Spotify album context and the original offset, including
positions occupied by null/unavailable items. Rejected context playback never
falls back to unrelated tracks. Search playback sends only the selected track URI.
Existing Space, n/p, d, ?, and q shortcuts work from catalog results. Help/device
dialogs restore the previous row and focus. ♫ marks matching playback URIs; row
highlighting marks selection. Duplicate URIs can share the playing indicator.
Catalog titles link to Spotify in terminals supporting OSC 8 hyperlinks.

## Now Playing and themes

Now Playing shows title, artists, album, active device, state, elapsed time,
duration, and a responsive progress strip. Narrow layouts combine artist/album
metadata. Known playing positions advance locally once per second, between the
existing configured Spotify polls. Successful pause freezes the clock; successful
resume restarts it. API updates correct seeks/track changes. Unknown position or
duration does not advance, and progress never passes track duration. Another client
can change playback between polls; local interpolation remains an estimate.

Set `[appearance] theme = "mono"` for monochrome, or keep `"midnight"` for dark
blue. Restart after editing config; hot reload is not implemented. Both themes
use the same navigation, feedback, and responsive layouts (120×35, 80×24, 60×20,
40×12). No extra polling, streaming, downloads, lyrics, or queue editing is added.

## Spotify contracts and restrictions

Supported endpoints: [Search](https://developer.spotify.com/documentation/web-api/reference/search),
[Get Album](https://developer.spotify.com/documentation/web-api/reference/get-an-album),
[Album Tracks](https://developer.spotify.com/documentation/web-api/reference/get-an-albums-tracks),
[Get Artist](https://developer.spotify.com/documentation/web-api/reference/get-an-artist),
and [Artist Albums](https://developer.spotify.com/documentation/web-api/reference/get-an-artists-albums).
The current search maximum is 10 per type and offset 1000; artist album pages are
also limited to 10. Account country determines catalog availability with user
tokens. Metadata removed in Spotify's [2026 API changes](https://developer.spotify.com/documentation/web-api/references/changes/february-2026),
such as popularity/follower counts, is not required. No related-artist, top-track,
recommendation, or bulk-resource endpoint is used.

Development Mode/account restrictions and market availability can prevent results,
album access or playback. 403/404 display a restricted/unavailable state without
forcing reauthorization. 401 uses the existing session recovery; repeated failure
directs users back to login. 429 respects the shared Retry-After cooldown. Network
and server failures remain recoverable via refresh. Mutations are never blindly
retried after an ambiguous network failure. Premium and a controllable Connect
device remain required for playback. Some followed playlists remain inaccessible.

## Live smoke checklist

Automated tests mock Spotify and prohibit real credentials/network/keychain access.
Before claiming live readiness:

1. Log in with an allowlisted Spotify account; search tracks, albums, artists,
   Unicode names, an empty result, and more than one page.
2. Open artist → album → track. Check durations, original track order, unavailable
   items, Enter playback, and Escape restoration at each level.
3. Play/pause, seek from another Spotify client, switch tracks/devices, and confirm
   interpolation reconciles with subsequent polls without extra API requests.
4. Open and close devices/help while searching; type shortcut characters and
   confirm no playback action fires. Change queries quickly on a slow connection.
5. Exercise account/market restrictions, a disappearing device, offline recovery,
   and rate-limit feedback if encountered; do not deliberately flood Spotify.
6. Restart with midnight and mono at all four terminal sizes. Confirm keyboard
   access, visible selected/playing indicators, progress, and graceful quit.
