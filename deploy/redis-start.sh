#!/bin/sh
set -eu
cb_password=$(cat /run/cb-redis/password)
# A generated token has no whitespace or configuration metacharacters.
case "$cb_password" in ''|*[!A-Za-z0-9_-]*) exit 1;; esac
# No command-line password or on-disk generated configuration.
printf 'bind 0.0.0.0\nport 6379\nprotected-mode yes\nrequirepass %s\nmaxmemory 128mb\nmaxmemory-policy noeviction\nappendonly yes\nappendfsync everysec\nsave ""\ndir /data\n' "$cb_password" | redis-server - &
cb_redis_pid=$!
# PID 1 is this credential-loading shell; forward stop to Redis so it can
# flush AOF instead of waiting for Docker's forced-kill deadline.
trap 'kill -TERM "$cb_redis_pid" 2>/dev/null || true; wait "$cb_redis_pid" || true; exit 0' TERM INT
wait "$cb_redis_pid"
