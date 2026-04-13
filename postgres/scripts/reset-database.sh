#!/bin/bash

terminate_connections() {
  psql -U postgres -c "
    SELECT pg_terminate_backend(pg_stat_activity.pid)
    FROM pg_stat_activity
    WHERE pg_stat_activity.datname = '$1'
    AND pid <> pg_backend_pid();"
}

terminate_connections "backend"
psql -U postgres -c "DROP DATABASE IF EXISTS backend;"
psql -U postgres -c "CREATE DATABASE backend;"

terminate_connections "backend_auth"
psql -U postgres -c "DROP DATABASE IF EXISTS backend_auth;"
psql -U postgres -c "CREATE DATABASE backend_auth;"