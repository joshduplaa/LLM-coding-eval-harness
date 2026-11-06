# Convenience targets. Docker commands use your active context (`docker context show`).
SANDBOX_IMAGE ?= evalharness-sandbox:0.1.0
SERVICE_IMAGE ?= evalharness-service:0.1.0

.PHONY: install sandbox-image service-image images smoke ci lint lint-workflows format test test-unit test-gvisor validate demo

install:            ## create .venv and install the package + dev tools
	uv sync

sandbox-image:      ## build the image untrusted code runs in
	docker build -t $(SANDBOX_IMAGE) sandbox/

service-image:      ## build the read-only API image
	docker build -t $(SERVICE_IMAGE) .

images: sandbox-image service-image

smoke: service-image ## start the service image and check it from outside
	scripts/smoke_service_image.sh $(SERVICE_IMAGE)

ci: lint lint-workflows images test smoke ## everything the GitHub Actions workflow runs

lint-workflows:     ## check .github/workflows with actionlint (pinned image)
	docker run --rm -v "$$PWD:/repo:ro" -w /repo rhysd/actionlint:1.7.12 -color=false

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
