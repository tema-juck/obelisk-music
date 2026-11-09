# Configuration

These commands are included in current source and development builds. Published
`v0.2.0-alpha.1` binaries predate configuration commands; those binaries continue
to use `SPOTIFY_CLIENT_ID`. A standalone release containing this change needs no
Python or uv installation.

## Set up Spotify

1. Sign in to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   and create your own app with Web API access. See Spotify's
   [app setup guide](https://developer.spotify.com/documentation/web-api/concepts/apps).
2. Register `http://127.0.0.1:8765/callback` exactly. Make sure the Spotify account
   you will authorize is permitted to use the app in Development Mode.
3. Copy the app's **Client ID**. Obelisk uses OAuth PKCE and does not need a client
   secret. Never put a client secret, access token, or refresh token in TOML.
4. Run:

   ```sh
   obelisk config init
   obelisk config edit
   obelisk config validate
   obelisk
   ```

   Set `spotify.client_id` to your Client ID in the editor, then press **L** in
   Obelisk to authorize. Spotify Premium and a Spotify Connect playback device
   are needed for playback control. Obelisk does not stream audio.

For source checkouts, prefix commands with `uv run`, for example
`uv run obelisk config init`.

## Location and file safety

The default is `~/.config/obelisk/config.toml` on both macOS and Linux. A nonempty
`XDG_CONFIG_HOME` changes the path to `$XDG_CONFIG_HOME/obelisk/config.toml`.
It must be absolute and contain no `..` components. Empty or unset
`XDG_CONFIG_HOME` uses the default. The home directory's OS aliases are resolved.

`obelisk config path` prints the effective path without creating directories or
loading settings. Only `init`, or `edit` when the file is missing, creates files.
New directories use mode `0700` and the new file uses `0600`; existing directory
permissions are left alone. `init` uses exclusive creation and never overwrites
an existing file. Symbolic links in the configuration path are refused, including
links to missing files. For custom XDG locations use canonical paths, for example
`/private/tmp/...` rather than the `/tmp` alias on macOS.

Configuration must be a regular UTF-8 TOML file of at most 64 KiB. Error messages
identify settings and corrective actions without printing their values or TOML
source lines. Client IDs are omitted from settings representations. Tokens remain
in the existing OS keychain; unknown keys, including token/secret fields, fail
validation.

## Supported settings

`config init` creates this schema with explanatory comments:

```toml
[spotify]
client_id = ""
refresh_interval = 8

[appearance]
theme = "midnight"

[interface]
vim_keys = true
```

| Setting | Validation and behavior |
| --- | --- |
| `spotify.client_id` | String, up to 256 ASCII letters/digits/underscores/hyphens; surrounding whitespace is trimmed. Empty means login is not configured. Spotify checks the actual ID during authorization. |
| `spotify.refresh_interval` | Integer from 1 to 3600 seconds; controls playback polling. Fractions, strings, and booleans are rejected. The default is 8 seconds; shorter intervals consume more API quota. Existing shared rate-limit cooldowns remain in force. |
| `appearance.theme` | Only `"midnight"` is supported; it applies Obelisk's existing dark palette through Textual's named theme system. Other names are errors. |
| `interface.vim_keys` | TOML boolean. `false` disables `j`/`k` navigation in playlists, tracks, devices, and help. Arrow keys remain available. Other playback/navigation shortcuts retain their behavior. |

All sections and keys are optional; omitted settings use defaults. Unknown keys
are rejected so misspellings do not silently do nothing. A missing file also uses
defaults. Missing Client ID leaves the TUI usable and displays the configuration
path and setup hint; **?** shows the full path in scrollable help on small terminals.
Restart Obelisk after editing: configuration is loaded once before TUI startup.

## Precedence and environment compatibility

Environment overrides have priority over the user file, then built-in defaults.
There are currently no per-setting CLI overrides. The configuration file must
always have valid syntax and schema, even when an environment variable would
override a setting; errors in the file are never hidden.

| Environment variable | Overrides |
| --- | --- |
| `SPOTIFY_CLIENT_ID` | `spotify.client_id` |
| `OBELISK_REFRESH_INTERVAL` | `spotify.refresh_interval` (decimal integer) |
| `OBELISK_THEME` | `appearance.theme` |
| `OBELISK_VIM_KEYS` | `interface.vim_keys` (`true` or `false`, case-insensitive) |

Existing nonempty `SPOTIFY_CLIENT_ID` setups continue to work without a file.
Explicitly empty/invalid overrides produce an error instead of falling back.
Use `unset SPOTIFY_CLIENT_ID` to switch from an environment override to the file.
`.env` files are not loaded automatically.

The existing `SPOTIFY_REDIRECT_URI` environment variable still configures the
OAuth callback; it is not a TOML setting. Its default remains
`http://127.0.0.1:8765/callback`, and existing callback validation still applies.

## CLI behavior

| Command | Behavior |
| --- | --- |
| `obelisk` | Loads validated effective settings, then launches the TUI. |
| `obelisk config init` | Creates the commented example; refuses existing files or links. |
| `obelisk config edit` | Creates the example if missing, then starts `$VISUAL`, `$EDITOR`, or `vi`, in that order. Can open invalid TOML for repair. |
| `obelisk config path` | Prints the effective path, even if the file is missing or invalid. |
| `obelisk config validate` | Requires an existing file and validates its syntax, schema, and environment overrides offline; does not print the Client ID or contact Spotify. An empty file Client ID is valid but login still needs setup. |

Editor commands accept quoted executable paths and arguments, for example
`VISUAL='code --wait' obelisk config edit`. Commands are split into arguments and
executed without a shell. The external editor controls its own saving behavior;
no settings are rewritten automatically after editing. An invalid/unavailable
chosen editor is reported rather than silently replaced with another editor.

Commands succeed with exit status 0; configuration errors use 2 and cancellation
uses 130. `--help`, `--version`, and `config --help` work without loading a broken
configuration. For setup errors, run `config path`, repair with `config edit`,
then run `config validate` and restart Obelisk.
