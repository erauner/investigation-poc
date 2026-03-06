.PHONY: install test run run-mcp kind-enable-http-debug kagent-smoke-apply kagent-smoke-test kagent-smoke-clean kagent-smoke-loop kind-up kind-install-kagent kind-setup kind-smoke-loop kind-validate kind-down

PYTHON ?= python3
KIND_CLUSTER_NAME ?= investigation
KIND_CONTEXT ?= kind-$(KIND_CLUSTER_NAME)
KAGENT_NAMESPACE ?= kagent
KAGENT_VERSION ?= 0.7.22
K8S_OVERLAY ?= k8s-overlays/local-kind

install:
	@if command -v uv >/dev/null 2>&1; then \
		uv sync --extra dev; \
	else \
		$(PYTHON) -m pip install --upgrade pip; \
		$(PYTHON) -m pip install -e .[dev]; \
	fi

test:
	@if command -v uv >/dev/null 2>&1; then \
		uv run --extra dev pytest -q; \
	else \
		$(PYTHON) -m pytest -q; \
	fi

run:
	@if command -v uv >/dev/null 2>&1; then \
		uv run uvicorn investigation_service.main:app --host 0.0.0.0 --port 8080 --reload; \
	else \
		$(PYTHON) -m uvicorn investigation_service.main:app --host 0.0.0.0 --port 8080 --reload; \
	fi

run-mcp:
	@if command -v uv >/dev/null 2>&1; then \
		MCP_HOST=0.0.0.0 MCP_PORT=8001 MCP_PATH=/mcp uv run python -m investigation_service.mcp_server; \
	else \
		MCP_HOST=0.0.0.0 MCP_PORT=8001 MCP_PATH=/mcp $(PYTHON) -m investigation_service.mcp_server; \
	fi

kagent-smoke-apply:
	@./scripts/smoke-workload.sh apply

kagent-smoke-test:
	@TASK="$${TASK:-List pods in namespace kagent-smoke and tell me which one is unhealthy.}"; \
	./scripts/invoke-local.sh "$$TASK"

kagent-smoke-clean:
	@./scripts/smoke-workload.sh delete

kagent-smoke-loop:
	@$(MAKE) kagent-smoke-apply
	@$(MAKE) kagent-smoke-test
	@$(MAKE) kagent-smoke-clean

kind-up:
	@if ! command -v kind >/dev/null 2>&1; then \
		echo "kind not installed"; \
		exit 1; \
	fi
	@if kind get clusters | grep -qx "$(KIND_CLUSTER_NAME)"; then \
		echo "kind cluster $(KIND_CLUSTER_NAME) already exists"; \
	else \
		kind create cluster --name "$(KIND_CLUSTER_NAME)" --wait 120s; \
	fi
	@kubectl config use-context "$(KIND_CONTEXT)" >/dev/null
	@kubectl get nodes

kind-install-kagent:
	@if [ "$$(kubectl config current-context)" != "$(KIND_CONTEXT)" ]; then \
		echo "Current context is '$$(kubectl config current-context)'; expected '$(KIND_CONTEXT)'"; \
		echo "Run: make kind-up"; \
		exit 1; \
	fi
	@if ! command -v helm >/dev/null 2>&1; then \
		echo "helm not installed"; \
		exit 1; \
	fi
	@if [ -z "$$OPENAI_API_KEY" ]; then \
		echo "OPENAI_API_KEY is required"; \
		echo "Example: OPENAI_API_KEY=sk-... make kind-install-kagent"; \
		exit 1; \
	fi
	@kubectl create namespace "$(KAGENT_NAMESPACE)" --dry-run=client -o yaml | kubectl apply -f -
	@helm upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds --version "$(KAGENT_VERSION)" -n "$(KAGENT_NAMESPACE)"
	@helm upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version "$(KAGENT_VERSION)" -n "$(KAGENT_NAMESPACE)"
	@kubectl -n "$(KAGENT_NAMESPACE)" create secret generic kagent-openai \
		--from-literal=OPENAI_API_KEY="$$OPENAI_API_KEY" \
		--dry-run=client -o yaml | kubectl apply -f -
	@kubectl apply -k "$(K8S_OVERLAY)"
	@kubectl apply -f k8s/modelconfig.yaml
	@kubectl apply -f k8s/agent.yaml
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/kagent-controller --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/kagent-ui --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/investigation-mcp-server --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" wait --for=jsonpath='{.status.conditions[?(@.type=="Ready")].status}'=True agent/investigation-agent --timeout=180s

kind-enable-http-debug:
	@kubectl apply -k k8s/optional-http
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/investigation-service --timeout=180s

kind-setup:
	@$(MAKE) kind-up
	@$(MAKE) kind-install-kagent

kind-smoke-loop:
	@$(MAKE) kind-setup
	@$(MAKE) kagent-smoke-loop

kind-validate:
	@./scripts/kind-validate.sh

kind-down:
	@if ! command -v kind >/dev/null 2>&1; then \
		echo "kind not installed"; \
		exit 1; \
	fi
	@if kind get clusters | grep -qx "$(KIND_CLUSTER_NAME)"; then \
		kind delete cluster --name "$(KIND_CLUSTER_NAME)"; \
	else \
		echo "kind cluster $(KIND_CLUSTER_NAME) does not exist"; \
	fi
