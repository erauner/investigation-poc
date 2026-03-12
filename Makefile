.PHONY: install test run run-mcp validate-loki-phase1 validate-loki-phase1-deterministic kind-build-investigation-image kind-load-investigation-image kind-build-shadow-image kind-load-shadow-image kind-sync-shadow-runtime kind-build-metrics-smoke-image kind-load-metrics-smoke-image kind-build-loki-mcp-image kind-load-loki-mcp-image kind-build-alertmanager-mcp-image kind-load-alertmanager-mcp-image kind-enable-http-debug kind-enable-loki-debug kind-enable-alertmanager-debug kind-enable-slack-a2a kind-enable-slack-mcp kind-enable-slack kind-preflight-clean kagent-smoke-apply kagent-smoke-test kagent-shadow-test kagent-smoke-clean kagent-smoke-loop metrics-smoke-apply metrics-smoke-clean kind-up kind-install-kagent kind-install-kagent-shadow kind-install-operator kind-setup kind-smoke-loop operator-smoke-apply operator-smoke-clean operator-metrics-smoke-apply operator-metrics-smoke-clean kind-validate kind-validate-shadow kind-validate-metrics kind-validate-service-metrics kind-validate-service-scout kind-validate-service-scout-debug kind-validate-loki-complementary kind-validate-node kind-validate-operator kind-validate-alert-entry kind-validate-operator-service-metrics kind-validate-multi kind-down

PYTHON ?= python3
KIND_CLUSTER_NAME ?= investigation
KIND_CONTEXT ?= kind-$(KIND_CLUSTER_NAME)
KAGENT_NAMESPACE ?= kagent
KAGENT_VERSION ?= 0.7.23
K8S_OVERLAY ?= k8s-overlays/local-kind
HOST_PROMETHEUS_OVERLAY ?= k8s-overlays/local-kind-host-prometheus
HTTP_DEBUG_OVERLAY ?= k8s-overlays/local-kind-optional-http
INVESTIGATION_IMAGE ?= investigation-poc:local
SHADOW_IMAGE ?= investigation-shadow-runtime:local
METRICS_SMOKE_IMAGE ?= metrics-smoke-app:local
LOKI_MCP_IMAGE ?= loki-mcp-server:local
ALERTMANAGER_MCP_IMAGE ?= alertmanager-mcp-server:local
HOMELAB_OPERATOR_DIR ?= ../homelab-operator
OPERATOR_IMAGE ?= homelab-operator:local

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

validate-loki-phase1: validate-loki-phase1-deterministic

validate-loki-phase1-deterministic:
	@./scripts/validate-loki-phase1.sh

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

kind-build-investigation-image:
	@docker build -t "$(INVESTIGATION_IMAGE)" .

kind-load-investigation-image:
	@kind load docker-image "$(INVESTIGATION_IMAGE)" --name "$(KIND_CLUSTER_NAME)"

kind-build-shadow-image:
	@docker build -f Dockerfile.shadow -t "$(SHADOW_IMAGE)" .

kind-load-shadow-image:
	@kind load docker-image "$(SHADOW_IMAGE)" --name "$(KIND_CLUSTER_NAME)"

kind-sync-shadow-runtime:
	@kubectl -n "$(KAGENT_NAMESPACE)" wait --for=jsonpath='{.status.conditions[?(@.type=="Ready")].status}'=True agent/incident-triage-shadow --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/incident-triage-shadow --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" set image deploy/incident-triage-shadow kagent="$(SHADOW_IMAGE)"
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout restart deploy/incident-triage-shadow
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/incident-triage-shadow --timeout=180s
	@actual_image="$$(kubectl -n "$(KAGENT_NAMESPACE)" get deploy/incident-triage-shadow -o jsonpath='{.spec.template.spec.containers[0].image}')"; \
	if [ "$$actual_image" != "$(SHADOW_IMAGE)" ]; then \
		echo "Shadow deployment image mismatch: expected $(SHADOW_IMAGE), got $$actual_image"; \
		exit 1; \
	fi

kind-build-metrics-smoke-image:
	@docker build -t "$(METRICS_SMOKE_IMAGE)" testapps/metrics_smoke

kind-load-metrics-smoke-image:
	@kind load docker-image "$(METRICS_SMOKE_IMAGE)" --name "$(KIND_CLUSTER_NAME)"

kind-build-loki-mcp-image:
	@docker build -f Dockerfile.loki-mcp -t "$(LOKI_MCP_IMAGE)" .

kind-load-loki-mcp-image:
	@kind load docker-image "$(LOKI_MCP_IMAGE)" --name "$(KIND_CLUSTER_NAME)"

kind-build-alertmanager-mcp-image:
	@docker build -f Dockerfile.alertmanager-mcp -t "$(ALERTMANAGER_MCP_IMAGE)" .

kind-load-alertmanager-mcp-image:
	@kind load docker-image "$(ALERTMANAGER_MCP_IMAGE)" --name "$(KIND_CLUSTER_NAME)"

kagent-smoke-apply:
	@./scripts/smoke-workload.sh apply

kagent-smoke-test:
	@TASK="$${TASK:-List pods in namespace kagent-smoke and tell me which one is unhealthy.}"; \
	./scripts/invoke-local.sh "$$TASK"

kagent-shadow-test:
	@TASK="$${TASK:-Investigate the unhealthy pod in namespace kagent-smoke. Return Diagnosis, Evidence, Related Data, Limitations, and Recommended next step.}"; \
	./scripts/invoke-shadow-local.sh "$$TASK"

kagent-smoke-clean:
	@./scripts/smoke-workload.sh delete

kagent-smoke-loop:
	@$(MAKE) kagent-smoke-apply
	@$(MAKE) kagent-smoke-test
	@$(MAKE) kagent-smoke-clean

metrics-smoke-apply:
	@./scripts/metrics-smoke.sh apply

metrics-smoke-clean:
	@./scripts/metrics-smoke.sh delete

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
	@$(MAKE) kind-build-investigation-image
	@$(MAKE) kind-load-investigation-image
	@kubectl create namespace "$(KAGENT_NAMESPACE)" --dry-run=client -o yaml | kubectl apply -f -
	@helm upgrade --install kagent-crds oci://ghcr.io/kagent-dev/kagent/helm/kagent-crds --version "$(KAGENT_VERSION)" -n "$(KAGENT_NAMESPACE)"
	@helm upgrade --install kagent oci://ghcr.io/kagent-dev/kagent/helm/kagent --version "$(KAGENT_VERSION)" -n "$(KAGENT_NAMESPACE)"
	@kubectl -n "$(KAGENT_NAMESPACE)" create secret generic kagent-openai \
		--from-literal=OPENAI_API_KEY="$$OPENAI_API_KEY" \
		--dry-run=client -o yaml | kubectl apply -f -
	@kubectl apply -k "$(K8S_OVERLAY)"
	@kubectl apply -f k8s/modelconfig.yaml
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout restart deploy/investigation-mcp-server >/dev/null 2>&1 || true
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/kagent-controller --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/kagent-ui --timeout=180s
	@if kubectl -n "$(KAGENT_NAMESPACE)" get deploy/prometheus >/dev/null 2>&1; then \
		kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/prometheus --timeout=180s; \
	fi
	@if kubectl -n "$(KAGENT_NAMESPACE)" get deploy/kube-state-metrics >/dev/null 2>&1; then \
		kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/kube-state-metrics --timeout=180s; \
	fi
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/investigation-mcp-server --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" wait --for=jsonpath='{.status.conditions[?(@.type=="Ready")].status}'=True agent/incident-triage --timeout=180s
	@if [ -n "$$SLACK_BOT_TOKEN" ] && [ -n "$$SLACK_APP_TOKEN" ]; then \
		$(MAKE) kind-enable-slack-a2a; \
	else \
		echo "Slack A2A bot not enabled. Set SLACK_BOT_TOKEN and SLACK_APP_TOKEN before running kind-install-kagent to enable it."; \
	fi
	@if [ -n "$$SLACK_BOT_TOKEN" ] && [ -n "$$SLACK_APP_TOKEN" ] && [ -n "$$SLACK_TEAM_ID" ] && [ -n "$$SLACK_CHANNEL_IDS" ]; then \
		$(MAKE) kind-enable-slack-mcp; \
	else \
		echo "Slack MCP send-message path not enabled. Set SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_TEAM_ID, and SLACK_CHANNEL_IDS to enable it."; \
	fi

kind-install-kagent-shadow:
	@if [ "$$(kubectl config current-context)" != "$(KIND_CONTEXT)" ]; then \
		echo "Current context is '$$(kubectl config current-context)'; expected '$(KIND_CONTEXT)'"; \
		echo "Run: make kind-up"; \
		exit 1; \
	fi
	@$(MAKE) kind-build-shadow-image
	@$(MAKE) kind-load-shadow-image
	@kubectl apply -k k8s-overlays/local-kind-shadow
	@$(MAKE) kind-sync-shadow-runtime

kind-install-operator:
	@KIND_CLUSTER_NAME="$(KIND_CLUSTER_NAME)" KIND_CONTEXT="$(KIND_CONTEXT)" HOMELAB_OPERATOR_DIR="$(HOMELAB_OPERATOR_DIR)" OPERATOR_IMAGE="$(OPERATOR_IMAGE)" ./scripts/operator-install.sh

kind-enable-http-debug:
	@$(MAKE) kind-build-investigation-image
	@$(MAKE) kind-load-investigation-image
	@kubectl apply -k "$(HTTP_DEBUG_OVERLAY)"
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/investigation-service --timeout=180s

kind-enable-loki-debug:
	@$(MAKE) kind-build-loki-mcp-image
	@$(MAKE) kind-load-loki-mcp-image
	@kubectl apply -k k8s-overlays/local-kind-optional-loki
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/loki --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status daemonset/promtail --timeout=240s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout restart deploy/loki-mcp-server >/dev/null 2>&1 || true
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/loki-mcp-server --timeout=240s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout restart deploy/investigation-service >/dev/null 2>&1 || true
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/investigation-service --timeout=180s

kind-enable-alertmanager-debug:
	@$(MAKE) kind-build-alertmanager-mcp-image
	@$(MAKE) kind-load-alertmanager-mcp-image
	@kubectl apply -k k8s-overlays/local-kind-optional-alertmanager
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/alertmanager --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout restart deploy/alertmanager-mcp-server >/dev/null 2>&1 || true
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/alertmanager-mcp-server --timeout=180s
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout restart deploy/investigation-service >/dev/null 2>&1 || true
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/investigation-service --timeout=180s

kind-enable-slack-a2a:
	@if [ "$$(kubectl config current-context)" != "$(KIND_CONTEXT)" ]; then \
		echo "Current context is '$$(kubectl config current-context)'; expected '$(KIND_CONTEXT)'"; \
		echo "Run: make kind-up"; \
		exit 1; \
	fi
	@if [ -z "$$SLACK_BOT_TOKEN" ] || [ -z "$$SLACK_APP_TOKEN" ]; then \
		echo "SLACK_BOT_TOKEN and SLACK_APP_TOKEN are required"; \
		echo "Example: SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... make kind-enable-slack-a2a"; \
		exit 1; \
	fi
	@kubectl -n "$(KAGENT_NAMESPACE)" create secret generic slack-credentials \
		--from-literal=SLACK_BOT_TOKEN="$$SLACK_BOT_TOKEN" \
		--from-literal=SLACK_APP_TOKEN="$$SLACK_APP_TOKEN" \
		$$(if [ -n "$$SLACK_USER_TOKEN" ]; then printf '%s' "--from-literal=SLACK_USER_TOKEN=$$SLACK_USER_TOKEN "; fi) \
		$$(if [ -n "$$SLACK_TEAM_ID" ]; then printf '%s' "--from-literal=SLACK_TEAM_ID=$$SLACK_TEAM_ID "; fi) \
		$$(if [ -n "$$SLACK_CHANNEL_IDS" ]; then printf '%s' "--from-literal=SLACK_CHANNEL_IDS=$$SLACK_CHANNEL_IDS "; fi) \
		--dry-run=client -o yaml | kubectl apply -f -
	@kubectl apply -k k8s/optional-slack-a2a
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/kagent-slack-bot --timeout=240s

kind-enable-slack-mcp:
	@if [ "$$(kubectl config current-context)" != "$(KIND_CONTEXT)" ]; then \
		echo "Current context is '$$(kubectl config current-context)'; expected '$(KIND_CONTEXT)'"; \
		echo "Run: make kind-up"; \
		exit 1; \
	fi
	@if [ -z "$$SLACK_BOT_TOKEN" ] || [ -z "$$SLACK_TEAM_ID" ] || [ -z "$$SLACK_CHANNEL_IDS" ]; then \
		echo "SLACK_BOT_TOKEN, SLACK_TEAM_ID, and SLACK_CHANNEL_IDS are required"; \
		echo "Example: SLACK_BOT_TOKEN=xoxb-... SLACK_TEAM_ID=T... SLACK_CHANNEL_IDS=C... make kind-enable-slack-mcp"; \
		exit 1; \
	fi
	@kubectl -n "$(KAGENT_NAMESPACE)" create secret generic slack-credentials \
		--from-literal=SLACK_BOT_TOKEN="$$SLACK_BOT_TOKEN" \
		$$(if [ -n "$$SLACK_APP_TOKEN" ]; then printf '%s' "--from-literal=SLACK_APP_TOKEN=$$SLACK_APP_TOKEN "; fi) \
		$$(if [ -n "$$SLACK_USER_TOKEN" ]; then printf '%s' "--from-literal=SLACK_USER_TOKEN=$$SLACK_USER_TOKEN "; fi) \
		--from-literal=SLACK_TEAM_ID="$$SLACK_TEAM_ID" \
		--from-literal=SLACK_CHANNEL_IDS="$$SLACK_CHANNEL_IDS" \
		--dry-run=client -o yaml | kubectl apply -f -
	@kubectl apply -k k8s/optional-slack-mcp
	@kubectl -n "$(KAGENT_NAMESPACE)" rollout status deploy/slack-mcp --timeout=240s
	@kubectl -n "$(KAGENT_NAMESPACE)" wait --for=jsonpath='{.status.conditions[?(@.type=="Ready")].status}'=True agent/slack-a2a-agent --timeout=240s

kind-enable-slack:
	@if [ -z "$$SLACK_BOT_TOKEN" ] || [ -z "$$SLACK_APP_TOKEN" ] || [ -z "$$SLACK_TEAM_ID" ] || [ -z "$$SLACK_CHANNEL_IDS" ]; then \
		echo "SLACK_BOT_TOKEN, SLACK_APP_TOKEN, SLACK_TEAM_ID, and SLACK_CHANNEL_IDS are required"; \
		echo "Example: SLACK_BOT_TOKEN=xoxb-... SLACK_APP_TOKEN=xapp-... SLACK_TEAM_ID=T... SLACK_CHANNEL_IDS=C... make kind-enable-slack"; \
		exit 1; \
	fi
	@$(MAKE) kind-enable-slack-a2a
	@$(MAKE) kind-enable-slack-mcp

kind-preflight-clean:
	@./scripts/kind-preflight-clean.sh

kind-setup:
	@$(MAKE) kind-up
	@$(MAKE) kind-install-kagent

kind-smoke-loop:
	@$(MAKE) kind-setup
	@$(MAKE) kagent-smoke-loop

operator-smoke-apply:
	@./scripts/operator-smoke.sh apply

operator-smoke-clean:
	@./scripts/operator-smoke.sh delete

operator-metrics-smoke-apply:
	@./scripts/operator-metrics-smoke.sh apply

operator-metrics-smoke-clean:
	@./scripts/operator-metrics-smoke.sh delete

kind-validate:
	@./scripts/kind-validate.sh

kind-validate-shadow:
	@./scripts/kind-validate-shadow.sh

kind-validate-metrics:
	@./scripts/kind-validate-metrics.sh

kind-validate-service-metrics:
	@./scripts/kind-validate-service-metrics.sh

kind-validate-service-scout:
	@./scripts/kind-validate-service-scout.sh

kind-validate-service-scout-debug:
	@KEEP_CLUSTER=1 KEEP_SMOKE=1 ./scripts/kind-validate-service-scout.sh

kind-validate-loki-complementary:
	@./scripts/kind-validate-loki-complementary.sh

kind-validate-node:
	@./scripts/kind-validate-node.sh

kind-validate-operator:
	@./scripts/kind-validate-operator.sh

kind-validate-alert-entry:
	@./scripts/kind-validate-alert-entry.sh

kind-validate-operator-service-metrics:
	@./scripts/kind-validate-operator-service-metrics.sh

kind-validate-multi:
	@./scripts/kind-validate-multi.sh

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
