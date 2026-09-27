#!/usr/bin/env bash
# locadev profile "supabase": local Supabase (Postgres + Auth/GoTrue + PostgREST +
# Realtime + Storage + Studio + Mailpit) for a consumer repo, via the Supabase CLI.
#
# Why the CLI and not a compose service: the CLI is Supabase's own local stack,
# it applies the consumer's migrations + seed.sql and honours its config.toml
# (auth test OTPs, redirect URLs, ports). locadev just standardises how it is
# started, what it excludes, and how health/env are reported.
#
# Usage:
#   SUPABASE_PROJECT_DIR=../myapp/db scripts/supabase.sh start|stop|status|env|reset
#   (SUPABASE_PROJECT_DIR = the directory that CONTAINS supabase/config.toml)
#
# Env:
#   SUPABASE_PROJECT_DIR   required for start/reset; default: ./supabase_sample
#   SUPABASE_EXCLUDE       containers to skip (default keeps the stack lean)
#   SUPABASE_STOP_ARGS     e.g. "--no-backup" to drop the DB volume on stop
#
# Containers run on the same Docker daemon as locadev (so on the external
# DockerData volume when start-docker is used), named supabase_*_<project_id>.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_DIR="${SUPABASE_PROJECT_DIR:-$ROOT/supabase_sample}"
EXCLUDE="${SUPABASE_EXCLUDE:-vector,logflare,edge-runtime,imgproxy,supavisor}"
CMD="${1:-status}"

if ! command -v supabase >/dev/null 2>&1; then
  echo "supabase CLI not found. Install: brew install supabase/tap/supabase" >&2
  exit 1
fi
if [[ ! -f "$PROJECT_DIR/supabase/config.toml" ]]; then
  echo "No supabase/config.toml under SUPABASE_PROJECT_DIR=$PROJECT_DIR" >&2
  echo "Point SUPABASE_PROJECT_DIR at the directory that contains supabase/config.toml." >&2
  exit 2
fi

sb() { supabase "$@" --workdir "$PROJECT_DIR"; }

case "$CMD" in
  start)
    echo "Supabase: starting for $PROJECT_DIR (excluding: ${EXCLUDE:-none})"
    if [[ -n "$EXCLUDE" ]]; then sb start -x "$EXCLUDE"; else sb start; fi
    ;;
  stop)
    # shellcheck disable=SC2086
    sb stop ${SUPABASE_STOP_ARGS:-}
    ;;
  status)
    sb status
    ;;
  env)
    # Print consumer env (URL + keys) in dotenv form. Local demo keys only.
    sb status -o env
    ;;
  reset)
    # Re-apply all migrations + seed.sql (destroys local data only).
    sb db reset
    ;;
  *)
    echo "Usage: $0 start|stop|status|env|reset" >&2
    exit 2
    ;;
esac
