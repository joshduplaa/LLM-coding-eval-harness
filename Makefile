# Convenience targets. Docker commands use your active context (`docker context show`).
SANDBOX_IMAGE ?= evalharness-sandbox:0.2.0
SERVICE_IMAGE ?= evalharness-service:0.4.0

.PHONY: cluster-up cluster-down evaluator-image k8s-images deploy k8s-eval dashboard test-k8s install sandbox-image service-image images smoke ci lint lint-workflows format test test-unit test-gvisor validate demo

install:            ## create .venv and install the package + dev tools
	uv sync

sandbox-image:      ## build the image untrusted code runs in
	docker build -t $(SANDBOX_IMAGE) sandbox/

service-image:      ## build the read-only API image
	docker build --target service -t $(SERVICE_IMAGE) .

images: sandbox-image service-image evaluator-image

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

# ---- Milestone 4: local Kubernetes (kind) ----
CLUSTER ?= evalharness
EVALUATOR_IMAGE ?= evalharness-evaluator:0.4.0
KCTX := --context kind-$(CLUSTER)

cluster-up:         ## create the local kind cluster (no cloud resources)
	kind get clusters | grep -qx $(CLUSTER) || kind create cluster --config deploy/kind/cluster.yaml

cluster-down:       ## delete the local kind cluster and everything in it
	kind delete cluster --name $(CLUSTER)

evaluator-image:    ## service image + kubectl; runs evaluations inside the cluster
	docker build --target evaluator -t $(EVALUATOR_IMAGE) .

k8s-images: images ## build all three images and load them into kind
	kind load docker-image --name $(CLUSTER) $(SANDBOX_IMAGE) $(SERVICE_IMAGE) $(EVALUATOR_IMAGE)

deploy: cluster-up k8s-images ## apply deploy/k8s: API, Prometheus, Grafana, sandbox namespace
	kubectl $(KCTX) apply -k deploy/k8s
	kubectl $(KCTX) -n evalharness rollout status deploy/evalharness-service deploy/prometheus deploy/grafana --timeout=180s

k8s-eval:           ## one MOCK evaluation run as a Job; each sample in its own sandbox pod
	@job=$$(kubectl $(KCTX) create -f deploy/k8s/eval-job.yaml -o name) && echo "$$job" && \
	kubectl $(KCTX) -n evalharness wait --for=condition=complete --timeout=900s "$$job" ; \
	kubectl $(KCTX) -n evalharness logs "$$job" --tail=40

dashboard:          ## Grafana on http://127.0.0.1:3000 (anonymous, read-only); Ctrl-C to stop
	kubectl $(KCTX) -n evalharness port-forward --address 127.0.0.1 svc/grafana 3000:3000

test-k8s:           ## the adversarial sandbox suite with every sample in a kind pod
	EVALHARNESS_SANDBOX_BACKEND=kubernetes uv run pytest -q tests/test_sandbox_security.py tests/test_k8s_sandbox.py
