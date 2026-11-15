#!/bin/sh
# Download only an explicitly trusted release and join without system changes.
set -eu
umask 077

fail() {
    printf '%s\n' "campus bootstrap: $1" >&2
    exit 1
}

usage() {
    printf '%s\n' 'Usage: sh join.sh --release-base-url HTTPS_URL --sha256 TRUSTED_SHA256 --invite INVITE [--cache-dir DIR] -- [campus join options]'
}

release_base=''
expected_sha=''
invite=''
cache_dir=''
temporary_file=''

while [ "$#" -gt 0 ]; do
    case "$1" in
        --release-base-url|--sha256|--invite|--cache-dir)
            [ "$#" -ge 2 ] || fail 'an explicit option value is missing'
            case "$1" in
                --release-base-url) release_base=$2 ;;
                --sha256) expected_sha=$2 ;;
                --invite) invite=$2 ;;
                --cache-dir) cache_dir=$2 ;;
            esac
            shift 2
            ;;
        --) shift; break ;;
        --help|-h) usage; exit 0 ;;
        *) fail 'unknown bootstrap option; use -- before campus join options' ;;
    esac
done

[ -n "$release_base" ] && [ -n "$expected_sha" ] && [ -n "$invite" ] || fail 'release base URL, trusted SHA256, and invite are required'
[ "${#expected_sha}" -eq 64 ] || fail 'trusted SHA256 must contain exactly 64 hexadecimal characters'
case "$expected_sha" in *[!0-9a-fA-F]*) fail 'trusted SHA256 is not hexadecimal' ;; esac
expected_sha=$(printf '%s' "$expected_sha" | tr '[:upper:]' '[:lower:]')
case "$release_base" in https://*) ;; *) fail 'release base URL must use HTTPS' ;; esac
case "$release_base" in *\?*|*\#*|*\\*) fail 'release base URL must not contain a query, fragment, or backslash' ;; esac
if printf '%s' "$release_base" | LC_ALL=C grep -q '[^!-~]'; then
    fail 'release base URL must contain only visible ASCII URL characters'
fi
release_authority=${release_base#https://}
release_authority=${release_authority%%/*}
case "$release_authority" in ''|*@*) fail 'release base URL needs a host and must not contain credentials' ;; esac
while [ "${release_base%/}" != "$release_base" ]; do release_base=${release_base%/}; done

case "$(uname -s)" in
    Linux) runner_os=linux; binary_suffix='' ;;
    Darwin) runner_os=darwin; binary_suffix='' ;;
    MINGW*|MSYS*|CYGWIN*) runner_os=windows; binary_suffix=.exe ;;
    *) fail 'unsupported operating system' ;;
esac
case "$(uname -m)" in
    x86_64|amd64) runner_arch=amd64 ;;
    aarch64|arm64) runner_arch=arm64 ;;
    *) fail 'unsupported architecture; native amd64 or arm64 is required' ;;
esac

command -v curl >/dev/null 2>&1 || fail 'curl is required'
if command -v sha256sum >/dev/null 2>&1; then
    sha_tool=sha256sum
elif command -v shasum >/dev/null 2>&1; then
    sha_tool=shasum
else
    fail 'sha256sum or shasum is required'
fi

hash_file() {
    if [ "$sha_tool" = sha256sum ]; then
        hash_output=$(sha256sum < "$1") || fail 'cannot hash runner binary'
    else
        hash_output=$(shasum -a 256 < "$1") || fail 'cannot hash runner binary'
    fi
    printf '%s' "${hash_output%% *}"
}

check_no_links() {
    checked_path=$1
    while [ -n "$checked_path" ]; do
        [ ! -L "$checked_path" ] || fail 'cache paths must not contain symlinks'
        checked_parent=$(dirname "$checked_path")
        [ "$checked_parent" != "$checked_path" ] || break
        checked_path=$checked_parent
    done
}

if [ -z "$cache_dir" ]; then
    [ -n "${HOME:-}" ] || fail 'HOME or an explicit cache directory is required'
    cache_dir=${XDG_CACHE_HOME:-"$HOME/.cache"}/campus/bin
fi
case "$cache_dir" in /*) ;; *) cache_dir=$(pwd -P)/$cache_dir ;; esac
check_no_links "$cache_dir"
mkdir -p "$cache_dir" || fail 'cannot create user cache'
check_no_links "$cache_dir"
[ -d "$cache_dir" ] || fail 'cache is not a directory'
cache_owner=$(LC_ALL=C ls -nd "$cache_dir" | awk '{print $3}')
[ "$cache_owner" = "$(id -u)" ] || fail 'cache must belong to the current user'
chmod 700 "$cache_dir" || fail 'cannot restrict cache access'

binary_name=campus-$runner_os-$runner_arch$binary_suffix
binary_path=$cache_dir/campus-$runner_os-$runner_arch-$expected_sha$binary_suffix
check_no_links "$binary_path"

cleanup() {
    if [ -n "$temporary_file" ] && [ -e "$temporary_file" ]; then
        rm -f "$temporary_file"
    fi
}
trap cleanup 0
trap 'exit 130' INT
trap 'exit 143' TERM

if [ -e "$binary_path" ]; then
    [ -f "$binary_path" ] || fail 'cached runner is not a regular file'
    [ "$(hash_file "$binary_path")" = "$expected_sha" ] || fail 'cached runner checksum mismatch; remove that cache entry and retry'
else
    temporary_file=$(mktemp "$cache_dir/.campus-download.XXXXXX") || fail 'cannot create private download'
    if ! http_status=$(curl --silent --show-error --fail --proto '=https' --proto-redir '=https' \
        --tlsv1.2 --connect-timeout 20 --max-time 120 --max-filesize 134217728 --max-redirs 0 \
        --output "$temporary_file" --write-out '%{http_code}' --url "$release_base/$binary_name"); then
        fail 'HTTPS runner download failed'
    fi
    [ "$http_status" = 200 ] || fail 'runner download must return HTTP 200; redirects are refused'
    [ "$(hash_file "$temporary_file")" = "$expected_sha" ] || fail 'downloaded runner checksum mismatch'
    chmod 700 "$temporary_file" || fail 'cannot make verified runner executable'
    check_no_links "$binary_path"
    [ ! -e "$binary_path" ] || fail 'cache entry appeared during download; retry to verify it'
    mv "$temporary_file" "$binary_path" || fail 'cannot publish verified runner'
    temporary_file=''
fi

chmod 700 "$binary_path" || fail 'cannot restrict verified runner access'
# Direct argv preserves spaces/Unicode and never evaluates invite or options.
exec "$binary_path" join "$invite" "$@"
