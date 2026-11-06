# Convenience targets. Docker commands use your active context (`docker context show`).
SANDBOX_IMAGE ?= evalharness-sandbox:0.1.0

.PHONY: install sandbox-image lint format test test-unit test-gvisor validate demo

install:            ## create .venv and install the package + dev tools
	uv sync

sandbox-image:      ## build the image untrusted code runs in
	docker build -t $(SANDBOX_IMAGE) sandbox/

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

test-unit:          ## fast tests, no Docker
	uv run pytest -q -m "not docker"

test:               ## everything, including adversarial sandbox tests (needs the image)
	uv run pytest -q

test-gvisor:        ## the same suite with every sandbox under gVisor (runsc must be registered)
	EVALHARNESS_SANDBOX_RUNTIME=runsc uv run pytest -q

validate:           ## check every task's tests accept the reference and reject incorrect ones
	uv run evalharness validate

demo:               ## one end-to-end run with the mixed mock model
	uv run evalharness run --model mock-mixed --samples 2
