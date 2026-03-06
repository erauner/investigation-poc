import json
import subprocess

from .models import TargetRef, UnhealthyWorkloadCandidate, UnhealthyWorkloadsResponse


def resolve_target(namespace: str, target: str) -> TargetRef:
    if "/" in target:
        kind, name = target.split("/", 1)
        normalized = kind.strip().lower()
        if normalized in ("pod", "pods"):
            kind = "pod"
        elif normalized in ("deploy", "deployment", "deployments"):
            kind = "deployment"
        elif normalized in ("svc", "service", "services"):
            kind = "service"
        elif normalized in ("node", "nodes"):
            kind = "node"
        else:
            kind = "pod"
        return TargetRef(namespace=namespace or None, kind=kind, name=name)

    return TargetRef(namespace=namespace or None, kind="pod", name=target)


def _run_kubectl(args: list[str]) -> tuple[bool, str]:
    cmd = ["kubectl", *args]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return False, str(exc)

    if completed.returncode != 0:
        return False, completed.stderr.strip() or completed.stdout.strip()
    return True, completed.stdout


def _resource_exists(namespace: str, kind: str, name: str) -> bool:
    if kind == "node":
        ok, _ = _run_kubectl(["get", kind, name, "-o", "name"])
        return ok
    ok, _ = _run_kubectl(["-n", namespace, "get", kind, name, "-o", "name"])
    return ok


def _first_pod_with_prefix(namespace: str, prefix: str) -> str | None:
    ok, pods_json = _run_kubectl(["-n", namespace, "get", "pods", "-o", "json"])
    if not ok:
        return None
    try:
        items = json.loads(pods_json).get("items", [])
    except json.JSONDecodeError:
        return None

    candidates: list[dict] = []
    for item in items:
        name = item.get("metadata", {}).get("name", "")
        if name == prefix or name.startswith(f"{prefix}-"):
            candidates.append(item)

    if not candidates:
        return None

    # Pick most recent pod to match current workload instance.
    candidates.sort(key=lambda i: i.get("metadata", {}).get("creationTimestamp", ""), reverse=True)
    return candidates[0].get("metadata", {}).get("name")


def resolve_runtime_target(target: TargetRef) -> TargetRef:
    if target.kind != "pod":
        return target

    if _resource_exists(target.namespace, "pod", target.name):
        return target

    # Common user intent: pod/<workload-name>. Prefer deployment when it exists.
    if _resource_exists(target.namespace, "deployment", target.name):
        return TargetRef(namespace=target.namespace, kind="deployment", name=target.name)

    # Fallback to best-effort pod prefix match.
    matched_pod = _first_pod_with_prefix(target.namespace, target.name)
    if matched_pod:
        return TargetRef(namespace=target.namespace, kind="pod", name=matched_pod)

    return target


def get_k8s_object(target: TargetRef) -> dict:
    args = ["get", target.kind, target.name, "-o", "json"]
    if target.namespace:
        args = ["-n", target.namespace, *args]
    ok, output = _run_kubectl(args)
    if not ok:
        return {"error": output, "namespace": target.namespace, "kind": target.kind, "name": target.name}

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return {"error": "invalid kubectl json", "raw": output[:400]}

    metadata = parsed.get("metadata", {})
    status = parsed.get("status", {})
    response = {
        "namespace": target.namespace,
        "kind": target.kind,
        "name": target.name,
        "phase": status.get("phase"),
        "readyReplicas": status.get("readyReplicas"),
        "replicas": status.get("replicas"),
        "observedGeneration": status.get("observedGeneration"),
        "creationTimestamp": metadata.get("creationTimestamp"),
    }
    if target.kind == "node":
        response["conditions"] = status.get("conditions", [])
        response["allocatable"] = status.get("allocatable", {})
        response["capacity"] = status.get("capacity", {})
        response["top_pods_by_memory_request"] = _top_pods_for_node(target.name)
    return response


def _parse_memory_to_bytes(raw: str | None) -> int:
    if not raw:
        return 0
    value = raw.strip()
    suffixes = {
        "Ki": 1024,
        "Mi": 1024**2,
        "Gi": 1024**3,
        "Ti": 1024**4,
        "Pi": 1024**5,
        "Ei": 1024**6,
        "K": 1000,
        "M": 1000**2,
        "G": 1000**3,
        "T": 1000**4,
        "P": 1000**5,
        "E": 1000**6,
    }
    for suffix, multiplier in suffixes.items():
        if value.endswith(suffix):
            number = value[: -len(suffix)] or "0"
            return int(float(number) * multiplier)
    try:
        return int(value)
    except ValueError:
        return 0


def _top_pods_for_node(node_name: str, limit: int = 5) -> list[dict]:
    ok, pods_json = _run_kubectl(["get", "pods", "-A", "--field-selector", f"spec.nodeName={node_name}", "-o", "json"])
    if not ok:
        return []
    try:
        items = json.loads(pods_json).get("items", [])
    except json.JSONDecodeError:
        return []

    pods: list[dict] = []
    for item in items:
        metadata = item.get("metadata", {})
        spec = item.get("spec", {})
        total_bytes = 0
        for container in spec.get("containers", []):
            total_bytes += _parse_memory_to_bytes(
                container.get("resources", {}).get("requests", {}).get("memory")
            )
        if total_bytes <= 0:
            continue
        pods.append(
            {
                "namespace": metadata.get("namespace"),
                "name": metadata.get("name"),
                "memory_request_bytes": total_bytes,
            }
        )

    pods.sort(key=lambda item: item["memory_request_bytes"], reverse=True)
    return pods[:limit]


def get_top_pods_for_node(node_name: str, limit: int = 5) -> list[dict]:
    return _top_pods_for_node(node_name=node_name, limit=limit)


def find_unhealthy_workloads(namespace: str, limit: int = 5) -> UnhealthyWorkloadsResponse:
    ok, pods_json = _run_kubectl(["-n", namespace, "get", "pods", "-o", "json"])
    if not ok:
        return UnhealthyWorkloadsResponse(
            namespace=namespace,
            candidates=[],
            limitations=[f"pod query failed: {pods_json}"],
        )

    try:
        items = json.loads(pods_json).get("items", [])
    except json.JSONDecodeError:
        return UnhealthyWorkloadsResponse(
            namespace=namespace,
            candidates=[],
            limitations=["pod query returned invalid json"],
        )

    candidates: list[tuple[int, UnhealthyWorkloadCandidate]] = []
    for item in items:
        metadata = item.get("metadata", {})
        status = item.get("status", {})
        container_statuses = status.get("containerStatuses", []) or []
        total_restarts = sum(container.get("restartCount", 0) for container in container_statuses)
        ready = all(container.get("ready", False) for container in container_statuses) if container_statuses else False
        waiting_reasons = [
            container.get("state", {}).get("waiting", {}).get("reason")
            for container in container_statuses
            if container.get("state", {}).get("waiting", {}).get("reason")
        ]
        terminated_reasons = [
            container.get("lastState", {}).get("terminated", {}).get("reason")
            for container in container_statuses
            if container.get("lastState", {}).get("terminated", {}).get("reason")
        ]
        phase = status.get("phase")
        reason = status.get("reason") or next(iter(waiting_reasons), None) or next(iter(terminated_reasons), None)

        unhealthy_score = 0
        if "CrashLoopBackOff" in waiting_reasons:
            unhealthy_score = 100
        elif phase in {"Failed", "Pending"}:
            unhealthy_score = 80
        elif not ready and container_statuses:
            unhealthy_score = 60
        elif total_restarts > 0:
            unhealthy_score = 40

        if unhealthy_score == 0:
            continue

        name = metadata.get("name", "")
        summary = reason or phase or "unhealthy"
        if total_restarts > 0:
            summary = f"{summary}; restarts={total_restarts}"
        candidates.append(
            (
                unhealthy_score,
                UnhealthyWorkloadCandidate(
                    target=f"pod/{name}",
                    namespace=namespace,
                    kind="pod",
                    name=name,
                    phase=phase,
                    reason=reason,
                    restart_count=total_restarts,
                    ready=ready,
                    summary=summary,
                ),
            )
        )

    candidates.sort(key=lambda item: (item[0], item[1].restart_count), reverse=True)
    return UnhealthyWorkloadsResponse(
        namespace=namespace,
        candidates=[candidate for _, candidate in candidates[:limit]],
    )


def get_related_events(target: TargetRef, limit: int = 20) -> list[str]:
    names = [target.name]
    if target.kind == "deployment":
        pod_name = _first_pod_for_deployment(target.namespace, target.name)
        if pod_name:
            names.append(pod_name)

    lines: list[str] = []
    for name in names:
        args = [
            "get",
            "events",
            "--sort-by=.lastTimestamp",
            "--field-selector",
            f"involvedObject.name={name}",
            "-o",
            "custom-columns=TYPE:.type,REASON:.reason,MESSAGE:.message",
            "--no-headers",
        ]
        if target.namespace:
            args = ["-n", target.namespace, *args]
        else:
            args = ["-A", *args]
        ok, output = _run_kubectl(args)
        if not ok:
            continue
        lines.extend([line.strip() for line in output.splitlines() if line.strip()])

    # Preserve order but remove duplicate lines.
    deduped = list(dict.fromkeys(lines))
    lines = deduped[-limit:]
    if not lines:
        return ["no related events"]
    return lines


def _first_pod_for_deployment(namespace: str, deployment_name: str) -> str | None:
    ok, deploy_json = _run_kubectl(["-n", namespace, "get", "deployment", deployment_name, "-o", "json"])
    if not ok:
        return None

    try:
        parsed = json.loads(deploy_json)
    except json.JSONDecodeError:
        return None

    labels = parsed.get("spec", {}).get("selector", {}).get("matchLabels", {})
    if not labels:
        return None

    selector = ",".join([f"{k}={v}" for k, v in labels.items()])
    ok, pods_json = _run_kubectl(["-n", namespace, "get", "pods", "-l", selector, "-o", "json"])
    if not ok:
        return None

    try:
        pod_list = json.loads(pods_json).get("items", [])
    except json.JSONDecodeError:
        return None

    if not pod_list:
        return None
    return pod_list[0].get("metadata", {}).get("name")


def get_pod_logs(target: TargetRef, tail: int = 200) -> str:
    if target.kind == "node":
        return "logs unavailable for node targets"
    pod_name = target.name
    if target.kind == "deployment":
        resolved = _first_pod_for_deployment(target.namespace, target.name)
        if not resolved:
            return "no pod found for deployment"
        pod_name = resolved

    if target.kind not in ("pod", "deployment"):
        return "logs only supported for pod or deployment targets"

    ok, output = _run_kubectl(
        ["-n", target.namespace, "logs", pod_name, "--tail", str(tail), "--timestamps=true"]
    )
    if not ok:
        return f"log query failed: {output}"

    return output.strip()
