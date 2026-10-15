#!/bin/bash
# Sourced by the official MySQL initialization entrypoint; no credentials in argv.
cb_app_password=$(cat /run/cb-app/password)
cb_migrator_password=$(cat /run/cb-migrator/password)
[[ "$cb_app_password" =~ ^[A-Za-z0-9_-]{32,128}$ ]] || return 1
[[ "$cb_migrator_password" =~ ^[A-Za-z0-9_-]{32,128}$ ]] || return 1
docker_process_sql --database=campusbridge <<EOSQL
CREATE USER 'campus_app'@'%' IDENTIFIED BY '${cb_app_password}';
GRANT SELECT, INSERT, UPDATE, DELETE ON campusbridge.* TO 'campus_app'@'%';
CREATE USER 'campus_migrator'@'%' IDENTIFIED BY '${cb_migrator_password}';
GRANT ALL PRIVILEGES ON campusbridge.* TO 'campus_migrator'@'%';
EOSQL
unset cb_app_password cb_migrator_password
