#!/bin/sh
# Remove only an unchanged executable managed by install.sh and its receipt.
set -eu
umask 077
unset CDPATH
die() { printf 'obelisk uninstaller: %s\n' "$*" >&2; exit 1; }
bin_dir=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --bin-dir)
            [ "$#" -ge 2 ] || die '--bin-dir needs a value'
            [ -n "$2" ] || die '--bin-dir needs a nonempty value'
            bin_dir=$2; shift 2 ;;
        --help|-h) printf '%s\n' 'Usage: sh uninstall.sh [--bin-dir DIRECTORY]'; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
done
bin_dir=${bin_dir:-${XDG_BIN_HOME:-${HOME:?HOME or --bin-dir is required}/.local/bin}}
[ -d "$bin_dir" ] || { printf '%s\n' 'No managed Obelisk Player installation found.'; exit 0; }
bin_dir=$(cd "$bin_dir" && pwd -P) || die 'Cannot resolve installation directory'
case "$bin_dir/" in //|/bin/*|/sbin/*|/usr/*|/opt/*|/etc/*|/lib/*|/lib64/*|/boot/*|/System/*|/Library/*|/Applications/*)
    die 'Choose a user-owned directory, not a system installation path' ;;
esac
binary=$bin_dir/obelisk
receipt=$bin_dir/.obelisk-managed.sha256
lock=$bin_dir/.obelisk-install.lock
mkdir "$lock" 2>/dev/null || die "Installation locked: $lock; inspect active installers or a stale lock"
trap 'rmdir "$lock" 2>/dev/null || :' 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
if [ ! -e "$binary" ] && [ ! -L "$binary" ] && [ ! -e "$receipt" ] && [ ! -L "$receipt" ]; then
    printf '%s\n' 'No managed Obelisk Player installation found.'
    exit 0
fi
if [ ! -f "$binary" ] || [ -L "$binary" ] || [ ! -f "$receipt" ] || [ -L "$receipt" ]; then
    die "Refusing unmanaged files at $binary; nothing removed"
fi
if command -v sha256sum >/dev/null 2>&1; then
    hash_output=$(sha256sum < "$binary") || die 'Cannot hash executable'
elif command -v shasum >/dev/null 2>&1; then
    hash_output=$(shasum -a 256 < "$binary") || die 'Cannot hash executable'
else
    die 'Install sha256sum or shasum to verify the managed executable'
fi
digest=$(printf '%s\n' "$hash_output" | awk '{print $1}')
expected=$(printf 'obelisk-music-installer-v1\n%s' "$digest")
[ "$(cat "$receipt")" = "$expected" ] || die 'Executable or receipt changed; nothing removed'
rm "$binary" "$receipt" || die 'Could not remove the managed installation'
printf 'Removed managed executable: %s\n' "$binary"
