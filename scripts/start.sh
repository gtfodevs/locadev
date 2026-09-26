#!/usr/bin/env bash
# Interactive checkbox launcher for locadev profiles (pure bash, no dialog/fzf/gum).
# Non-interactive: scripts/start.sh teams aws

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROFILES=(teams aws cosmos search kv ollama mail slack discord functions sample cloudflare oauth gigchain sms geo supabase)
DESCRIPTIONS=(
  "Teams channel + bot (:3979/:3978)"
  "MiniStack AWS gateway S3 (:4566)"
  "Azure Cosmos DB emulator (:8081)"
  "Qdrant + AI Search emulator (:6333/:8800)"
  "Key Vault lowkey-vault (:8443)"
  "Dockerized Ollama for bridge"
  "Fake SendGrid capture (:8095)"
  "Fake Slack Web API + message UI (:8096)"
  "Fake Discord REST + message UI (:8097)"
  "Azure Functions runtime + sample (Azurite storage) (:7071)"
  "Sample FastAPI consumer (:18080)"
  "Cloudflare Workers (wrangler --local) (:8787)"
  "Fake OAuth + TOTP + soft passkeys (:8098)"
  "GigChain Cosmos SDK localnet (:26657/:1317)"
  "Fake Twilio SMS capture (:8099)"
  "Fake Geocodio geocoding (:8100)"
  "Supabase local stack via Supabase CLI (:54321-54324; needs SUPABASE_PROJECT_DIR)"
)
# parallel array of 0/1 selected
SELECTED=()
for _ in "${PROFILES[@]}"; do SELECTED+=(0); done

usage() {
  echo "Usage: $0 [profile ...]"
  echo "  Interactive (TTY): checkbox UI for optional profiles; core always starts."
  echo "  Non-interactive: $0 teams aws"
  echo "  Known profiles: ${PROFILES[*]}"
}

is_known() {
  local p=$1
  for k in "${PROFILES[@]}"; do
    [[ "$k" == "$p" ]] && return 0
  done
  return 1
}

launch() {
  # Compose v2.2.x only accepts --profile before the subcommand (or via COMPOSE_PROFILES).
  local args=(compose -p locadev)
  local selected_names=()
  for i in "${!PROFILES[@]}"; do
    if [[ "${SELECTED[$i]}" == "1" ]]; then
      args+=(--profile "${PROFILES[$i]}")
      selected_names+=("${PROFILES[$i]}")
    fi
  done
  args+=(up -d --build)
  echo "Running: docker ${args[*]}"
  if [[ ${#selected_names[@]} -gt 0 ]]; then
    echo "Profiles: ${selected_names[*]}"
  else
    echo "Profiles: (core only)"
  fi
  docker "${args[@]}"
  # "supabase" is not a compose profile: the Supabase CLI owns that stack.
  for n in ${selected_names[@]+"${selected_names[@]}"}; do
    if [[ "$n" == "supabase" ]]; then
      bash "$ROOT/scripts/supabase.sh" start
    fi
  done
}

# Non-interactive path: args are profile names
if [[ $# -gt 0 ]]; then
  for p in "$@"; do
    if ! is_known "$p"; then
      echo "Unknown profile: $p" >&2
      usage >&2
      exit 2
    fi
    for i in "${!PROFILES[@]}"; do
      if [[ "${PROFILES[$i]}" == "$p" ]]; then
        SELECTED[$i]=1
      fi
    done
  done
  launch
  exit 0
fi

# No TTY — refuse to hang
if [[ ! -t 0 || ! -t 1 ]]; then
  usage >&2
  exit 2
fi

# --- interactive UI ---
cursor=0
restore() {
  tput cnorm 2>/dev/null || true
  stty echo 2>/dev/null || true
}
trap restore EXIT
tput civis 2>/dev/null || true

draw() {
  # clear screen and redraw
  printf '\033[H\033[J'
  echo "locadev launcher — core services always start"
  echo "  azurite, mssql, servicebus, bridge, topaz, pglite, redis"
  echo ""
  echo "Optional profiles (space toggle, j/k or arrows, a=all, n=none, enter=launch, q=quit)"
  echo ""
  for i in "${!PROFILES[@]}"; do
    local mark=" "
    [[ "${SELECTED[$i]}" == "1" ]] && mark="x"
    local prefix="  "
    [[ "$i" -eq "$cursor" ]] && prefix="> "
    printf '%s[%s] %-10s %s\n' "$prefix" "$mark" "${PROFILES[$i]}" "${DESCRIPTIONS[$i]}"
  done
}

# raw single-key read
read_key() {
  local k
  # shellcheck disable=SC2162
  IFS= read -rsn1 k
  if [[ "$k" == $'\x1b' ]]; then
    local k2 k3
    IFS= read -rsn1 -t 0.1 k2 || true
    IFS= read -rsn1 -t 0.1 k3 || true
    if [[ "$k2" == "[" ]]; then
      case "$k3" in
        A) echo up ;;
        B) echo down ;;
        *) echo esc ;;
      esac
      return
    fi
    echo esc
    return
  fi
  echo "$k"
}

draw
while true; do
  key=$(read_key)
  case "$key" in
    up|k)
      cursor=$(( (cursor - 1 + ${#PROFILES[@]}) % ${#PROFILES[@]} ))
      ;;
    down|j)
      cursor=$(( (cursor + 1) % ${#PROFILES[@]} ))
      ;;
    " "|space)
      if [[ "${SELECTED[$cursor]}" == "1" ]]; then
        SELECTED[$cursor]=0
      else
        SELECTED[$cursor]=1
      fi
      ;;
    a)
      for i in "${!SELECTED[@]}"; do SELECTED[$i]=1; done
      ;;
    n)
      for i in "${!SELECTED[@]}"; do SELECTED[$i]=0; done
      ;;
    q|esc)
      echo "Aborted — nothing started."
      exit 0
      ;;
    "")
      # enter often reads as empty
      launch
      exit 0
      ;;
    $'\n'|$'\r')
      launch
      exit 0
      ;;
  esac
  draw
done
