#!/bin/sh
set -eu
REDISCLI_AUTH=$(cat /run/cb-redis/password)
export REDISCLI_AUTH
test "$(redis-cli --no-auth-warning ping)" = PONG
