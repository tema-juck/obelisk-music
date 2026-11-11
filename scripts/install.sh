#!/bin/sh
# Install verified release bytes; never run the downloaded executable.
set -eu
umask 077
LC_ALL=C
export LC_ALL
unset CDPATH TAR_OPTIONS GZIP GZIP_OPT

die() { printf 'obelisk installer: %s\n' "$*" >&2; exit 1; }
usage() {
    printf '%s\n' 'Usage: sh install.sh [--prerelease | --version TAG] [--bin-dir DIRECTORY]' \
        'Default: latest stable release; directory: XDG_BIN_HOME or HOME/.local/bin.'
}
version=
prerelease=0
bin_dir=
while [ "$#" -gt 0 ]; do
    case "$1" in
        --version|--bin-dir)
            [ "$#" -ge 2 ] || die "$1 needs a value"
            [ -n "$2" ] || die "$1 needs a nonempty value"
            case "$1" in --version) version=$2 ;; --bin-dir) bin_dir=$2 ;; esac
            shift 2 ;;
        --prerelease) prerelease=1; shift ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; die "Unknown option: $1" ;;
    esac
done
[ -z "$version" ] || [ "$prerelease" -eq 0 ] || die 'Choose --version or --prerelease'
for tool in curl uname mktemp gzip tar awk sed grep wc; do
    command -v "$tool" >/dev/null 2>&1 || die "Required tool is missing: $tool"
done
if command -v sha256sum >/dev/null 2>&1; then
    checksum_tool=sha256sum
elif command -v shasum >/dev/null 2>&1; then
    checksum_tool=shasum
else
    die 'Install sha256sum or shasum before continuing'
fi
hash_file() {
    if [ "$checksum_tool" = sha256sum ]; then
        hash_output=$(sha256sum < "$1") || return 1
    else
        hash_output=$(shasum -a 256 < "$1") || return 1
    fi
    printf '%s\n' "$hash_output" | awk '{print $1}'
}
system=$(uname -s) || die 'Cannot detect operating system'
machine=$(uname -m) || die 'Cannot detect architecture'
case "$system/$machine" in
    Darwin/arm64|Darwin/aarch64) target=darwin-arm64 ;;
    Darwin/x86_64) target=darwin-amd64 ;;
    Linux/x86_64) target=linux-amd64 ;;
    *) die "Unsupported platform: $system/$machine (supported: macOS ARM64/Intel, Linux x86_64 glibc)" ;;
esac
bin_dir=${bin_dir:-${XDG_BIN_HOME:-${HOME:?HOME or --bin-dir is required}/.local/bin}}
case "$bin_dir" in /*) ;; *) bin_dir=$(pwd -P)/$bin_dir ;; esac
case "$bin_dir/" in //|/bin/*|/sbin/*|/usr/*|/opt/*|/etc/*|/lib/*|/lib64/*|/boot/*|/System/*|/Library/*|/Applications/*)
    die 'Choose a user-owned directory, not a system installation path' ;;
esac
mkdir -p "$bin_dir" || die "Cannot create directory: $bin_dir"
bin_dir=$(cd "$bin_dir" && pwd -P) || die 'Cannot resolve installation directory'
case "$bin_dir/" in //|/bin/*|/sbin/*|/usr/*|/opt/*|/etc/*|/lib/*|/lib64/*|/boot/*|/System/*|/Library/*|/Applications/*)
    die 'Choose a user-owned directory, not a system installation path' ;;
esac
binary=$bin_dir/obelisk
receipt=$bin_dir/.obelisk-managed.sha256
lock=$bin_dir/.obelisk-install.lock
locked=0
temporary=
staged=
cleanup() {
    [ -z "$temporary" ] || rm -rf "$temporary"
    [ -z "$staged" ] || rm -rf "$staged"
    if [ "$locked" -eq 1 ]; then rmdir "$lock" 2>/dev/null || :; fi
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
mkdir "$lock" 2>/dev/null || die "Installation locked: $lock; check for another installer or inspect a stale lock"
locked=1
check_existing() {
    installed_hash=
    if [ -e "$binary" ] || [ -L "$binary" ] || [ -e "$receipt" ] || [ -L "$receipt" ]; then
        if [ ! -f "$binary" ] || [ -L "$binary" ] || [ ! -f "$receipt" ] || [ -L "$receipt" ]; then
            die "Refusing unmanaged installation at $binary; move existing files aside after inspection"
        fi
        installed_hash=$(hash_file "$binary") || die 'Cannot hash existing executable'
        expected_receipt=$(printf 'obelisk-music-installer-v1\n%s' "$installed_hash")
        [ "$(cat "$receipt")" = "$expected_receipt" ] \
            || die "Existing executable or receipt was changed: $binary; inspect and move it aside before installing"
    fi
}
check_existing
original_hash=$installed_hash
case "${TMPDIR:-/tmp}" in /*) ;; *) die 'TMPDIR must be an absolute directory' ;; esac
temporary=$(mktemp -d "${TMPDIR:-/tmp}/obelisk-install.XXXXXXXX") || die 'Cannot create private temporary directory'
fetch() {
    curl -q --fail --location --silent --show-error --proto '=https' --proto-redir '=https' \
        --connect-timeout 15 --max-time 120 --max-filesize 134217728 --output "$2" "$1"
}
api=https://api.github.com/repos/tema-juck/obelisk-player/releases
tag_pattern='^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-(alpha|beta|rc)\.(0|[1-9][0-9]*))?$'
valid_tag() {
    case "$1" in ''|*[!a-zA-Z0-9.-]*) return 1 ;; esac
    [ "${#1}" -le 128 ] || return 1
    printf '%s\n' "$1" | grep -Eq "$tag_pattern"
}
# Tokenize strings and nesting; text inside release bodies is never a field.
select_release() {
    awk -v pattern="$tag_pattern" -v pre="$1" '
    function atom_value() {
        if (depth == release_depth && expect[depth] == "value") {
            if (key[depth] == "draft") draft=atom
            if (key[depth] == "prerelease") prerelease=atom
            if (key[depth] == "tag_name") bad=1
        }
        expect[depth]="done"; atom=""
    }
    function field_string() {
        if (expect[depth] == "key") {key[depth]=token; expect[depth]="colon"}
        else if (expect[depth] == "value") {
            if (depth == release_depth && key[depth] == "tag_name") {
                if (++tags != 1) bad=1
                tag=token
            }
            expect[depth]="done"
        } else bad=1
    }
    {
        for (i=1; i<=length($0); i++) {
            c=substr($0,i,1)
            if (quoted) {
                if (escaped) {if (length(token)<4096) token=token "\\" c; escaped=0}
                else if (c == "\\") escaped=1
                else if (c == "\"") {quoted=0; field_string()}
                else if (length(token)<4096) token=token c
                continue
            }
            if (c ~ /[[:space:],:{}\[\]"]/) {
                if (atom != "") atom_value()
            } else {atom=atom c; continue}
            if (c == "\"") {quoted=1; token=""}
            else if (c == "{" || c == "[") {
                depth++; stack[depth]=c; expect[depth]=(c == "{" ? "key" : "value")
                if (c == "{" && (depth == 1 || (depth == 2 && stack[1] == "["))) {
                    release_depth=depth; tag=""; draft=""; prerelease=""; tags=0
                }
            } else if (c == "}" || c == "]") {
                if ((c == "}" && stack[depth] != "{") || (c == "]" && stack[depth] != "[")) bad=1
                if (c == "}" && depth == release_depth) {
                    if (chosen == "" && tag ~ pattern && draft == "false" && (pre || prerelease == "false")) chosen=tag
                    release_depth=0
                }
                depth--; if (depth < 0) bad=1; expect[depth]="done"
            } else if (c == ":") {
                if (expect[depth] != "colon") bad=1
                expect[depth]="value"
            } else if (c == ",") expect[depth]=(stack[depth] == "{" ? "key" : "value")
        }
        if (quoted) bad=1
    }
    END {if (bad || depth || quoted || escaped || atom != "") exit 1; if (chosen != "") print chosen}
    ' "$temporary/release.json"
}
if [ -n "$version" ]; then
    version=v${version#v}
    valid_tag "$version" || die 'Version must be vX.Y.Z or vX.Y.Z-alpha.N / beta.N / rc.N'
    fetch "$api/tags/$version" "$temporary/release.json" || die 'Cannot fetch the requested published release'
    tag=$(select_release 1) || die 'Invalid GitHub release response'
    [ "$tag" = "$version" ] || die 'GitHub returned a different, draft, or unsupported release'
else
    tag=
    page=1
    while [ "$page" -le 10 ]; do
        fetch "$api?per_page=100&page=$page" "$temporary/release.json" \
            || die 'Cannot fetch releases; check network/GitHub rate limits or use --version TAG'
        tag=$(select_release "$prerelease") || die 'Invalid GitHub releases response'
        [ -z "$tag" ] || break
        page=$((page + 1))
    done
    [ -n "$tag" ] || die 'No matching published release in the first 1000 entries; use --prerelease or --version TAG'
fi
valid_tag "$tag" || die 'GitHub returned no supported published release tag'
if [ -z "$version" ] && [ "$prerelease" -eq 0 ]; then
    case "$tag" in *-*) die 'Stable selection returned a prerelease; use --prerelease explicitly' ;; esac
fi
asset=obelisk-$tag-$target.tar.gz
base=https://github.com/tema-juck/obelisk-player/releases/download/$tag
fetch "$base/SHA256SUMS" "$temporary/SHA256SUMS" || die 'Checksum download failed; nothing installed'
expected_hash=$(awk -v tag="$tag" -v selected="$asset" '
    BEGIN {
        names["obelisk-" tag "-darwin-arm64.tar.gz"]=1
        names["obelisk-" tag "-darwin-amd64.tar.gz"]=1
        names["obelisk-" tag "-linux-amd64.tar.gz"]=1
    }
    NF != 2 || length($1) != 64 || $1 ~ /[^0-9a-f]/ || !($2 in names) {bad=1}
    {if (++seen[$2] != 1) bad=1; if ($2 == selected) digest=$1}
    END {if (bad || NR != 3 || digest == "") exit 1; print digest}
' "$temporary/SHA256SUMS") || die 'Invalid SHA256SUMS (missing, duplicate, malformed, or unexpected entries)'
fetch "$base/$asset" "$temporary/archive.gz" || die 'Archive download failed; nothing installed'
[ "$(wc -c < "$temporary/archive.gz")" -le 134217728 ] || die 'Archive exceeds 128 MiB limit'
actual_hash=$(hash_file "$temporary/archive.gz") || die 'Cannot calculate archive checksum'
[ "$actual_hash" = "$expected_hash" ] || die 'SHA256 checksum mismatch; nothing installed'
# Bound decompression before tar parsing; GNU and BSD shells use different block sizes.
(ulimit -f 262144 && gzip -dc "$temporary/archive.gz" > "$temporary/archive.tar") \
    || die 'Corrupt archive or decompression size limit exceeded'
[ "$(wc -c < "$temporary/archive.tar")" -le 134217728 ] || die 'Expanded archive exceeds 128 MiB limit'
tar -tf "$temporary/archive.tar" > "$temporary/names" || die 'Invalid tar archive'
awk '
    $0 != "obelisk" && $0 != "LICENSE" && $0 != "build-info.json" {bad=1}
    {if (++seen[$0] != 1) bad=1}
    END {if (bad || NR != 3) exit 1}
' "$temporary/names" || die 'Unsafe archive paths, duplicate members, or missing runtime files'
tar -tvf "$temporary/archive.tar" > "$temporary/types" || die 'Cannot inspect archive member types'
awk 'substr($0,1,1) != "-" {bad=1} END {if (bad || NR != 3) exit 1}' "$temporary/types" \
    || die 'Archive must contain only regular files; links and special files are forbidden'
tar -xOf "$temporary/archive.tar" obelisk > "$temporary/obelisk" || die 'Cannot read executable from archive'
[ -s "$temporary/obelisk" ] || die 'Archive executable is empty'
binary_hash=$(hash_file "$temporary/obelisk") || die 'Cannot hash executable'
staged=$(mktemp -d "$bin_dir/.obelisk-stage.XXXXXXXX") || die 'Cannot stage installation in target directory'
cp "$temporary/obelisk" "$staged/obelisk"
chmod 755 "$staged/obelisk"
printf 'obelisk-music-installer-v1\n%s\n' "$binary_hash" > "$staged/receipt"
check_existing
[ "$installed_hash" = "$original_hash" ] || die 'Installation changed while downloading; refusing replacement'
# Atomic executable replacement; interrupted receipt updates fail closed on the next run.
mv -f "$staged/obelisk" "$binary"
mv -f "$staged/receipt" "$receipt" || die 'Executable installed but receipt update failed; inspect installation before retrying'
printf 'Installed Obelisk Player %s (%s) to %s\n' "$tag" "$target" "$binary"
case ":${PATH:-}:" in
    *":$bin_dir:"*) ;;
    *) printf 'PATH notice: add %s to your shell PATH explicitly, or invoke the executable by its full path. No profile files were modified.\n' "$bin_dir" >&2 ;;
esac
