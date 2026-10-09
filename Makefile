COMPOSE := docker compose -p locadev

.PHONY: start up teams down verify test logs harness playground site pre-decision post-ready boards supabase supabase-down supabase-env

start:
	bash scripts/start.sh

up:
	$(COMPOSE) up -d --build

teams:
	$(COMPOSE) --profile teams up -d --build

down:
	$(COMPOSE) down $(ARGS)

verify:
	bash scripts/verify.sh

test:
	@if [ ! -d .venv ]; then python3 -m venv .venv; fi
	. .venv/bin/activate && pip -q install -r tests/requirements.txt && pytest -q tests

logs:
	$(COMPOSE) logs -f

harness:
	. .venv/bin/activate 2>/dev/null; python3 bridge/harness.py

# Grounding gates (see hooks/README.md) — set LOCADEV_* env vars
pre-decision:
	bash hooks/pre-decision.sh

post-ready:
	bash hooks/post-ready.sh

# Work boards (Jira + Azure DevOps) — see boards/README.md
# Example: make boards ARGS='get PROJ-123'
#          make boards ARGS='providers'
boards:
	bash boards/board.sh $(ARGS)

# Static GitHub Pages site → http://127.0.0.1:8088
site:
	@echo "Open http://127.0.0.1:8088/"
	python3 -m http.server 8088 --directory docs

# DaisyUI web playground (host-side) → http://127.0.0.1:19191
playground:
	@if [ ! -d demos/playground/.venv ]; then python3 -m venv demos/playground/.venv; fi
	. demos/playground/.venv/bin/activate && pip -q install -r demos/playground/requirements.txt
	cd demos/playground && . .venv/bin/activate && uvicorn app:app --host 127.0.0.1 --port 19191

# Supabase local stack via Supabase CLI (profile "supabase"; see scripts/supabase.sh)
# Example: make supabase SUPABASE_PROJECT_DIR=../myapp/db
supabase:
	bash scripts/supabase.sh start

supabase-down:
	bash scripts/supabase.sh stop

supabase-env:
	@bash scripts/supabase.sh env
