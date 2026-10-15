FROM mysql:8.4
# A Windows bind mount may mark .sh executable; the official entrypoint must
# source this file so docker_process_sql remains available in its shell.
COPY --chmod=0644 deploy/mysql-init.sh /docker-entrypoint-initdb.d/10-campusbridge.sh
USER mysql
