#!/bin/sh
# Drop from root to PUID:PGID (LinuxServer/hotio convention), fixing /config ownership first.
# The sandbox service runs with `user:` set in compose, so it never reaches this branch.
set -eu

if [ "$(id -u)" = "0" ]; then
  PUID="${PUID:-1000}"
  PGID="${PGID:-1000}"
  case "$PUID$PGID" in
    *[!0-9]*) echo "PUID/PGID must be numeric (got PUID=$PUID PGID=$PGID)" >&2; exit 1 ;;
  esac
  groupmod -o -g "$PGID" vibonomics >/dev/null
  usermod -o -u "$PUID" vibonomics >/dev/null
  mkdir -p "$VIBONOMICS_CONFIG_DIR"
  # Only re-own what needs it; never follow symlinks out of /config.
  find "$VIBONOMICS_CONFIG_DIR" -xdev \( ! -user vibonomics -o ! -group vibonomics \) -exec chown -h vibonomics:vibonomics {} + 2>/dev/null || true
  umask "${UMASK:-002}"
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups --inh-caps=-all --bounding-set=-all "$@"
fi

umask "${UMASK:-002}"
exec "$@"
