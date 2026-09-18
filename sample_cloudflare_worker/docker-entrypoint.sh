#!/bin/sh
set -eu
# wrangler --local reads non-[vars] secrets from .dev.vars
{
  echo "ISSUER=${ISSUER:-http://127.0.0.1:8787}"
  [ -n "${GITHUB_CLIENT_ID:-}" ] && echo "GITHUB_CLIENT_ID=${GITHUB_CLIENT_ID}"
  [ -n "${GITHUB_CLIENT_SECRET:-}" ] && echo "GITHUB_CLIENT_SECRET=${GITHUB_CLIENT_SECRET}"
  [ -n "${GOOGLE_CLIENT_ID:-}" ] && echo "GOOGLE_CLIENT_ID=${GOOGLE_CLIENT_ID}"
  [ -n "${GOOGLE_CLIENT_SECRET:-}" ] && echo "GOOGLE_CLIENT_SECRET=${GOOGLE_CLIENT_SECRET}"
  [ -n "${JWT_SIGNING_JWK:-}" ] && echo "JWT_SIGNING_JWK=${JWT_SIGNING_JWK}"
  [ -n "${OAUTH_SUCCESS_REDIRECT:-}" ] && echo "OAUTH_SUCCESS_REDIRECT=${OAUTH_SUCCESS_REDIRECT}"
} > /app/.dev.vars
exec "$@"
