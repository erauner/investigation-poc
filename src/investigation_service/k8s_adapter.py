import json
import subprocess

from .cluster_registry import ResolvedCluster
from .models import TargetRef, UnhealthyWorkloadCandidate, UnhealthyWorkloadsResponse


def _call_with_optional_cluster(func, *args, cluster: ResolvedCluster | None = None):
    if cluster is None:
        return func(*args)
    try:
        return func(*args, cluster=cluster)
    except TypeError:
        return func(*args)


def resolve_target(namespace: str, target: str, cluster: ResolvedCluster | None = None) -> TargetRef:
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

    normalized_namespace = namespace or None
    if normalized_namespace:
        if _call_with_optional_cluster(_resource_exists, normalized_namespace, "pod", target, cluster=cluster):
            return TargetRef(namespace=normalized_namespace, kind="pod", name=target)
        if _call_with_optional_cluster(_resource_exists, normalized_namespace, "deployment", target, cluster=cluster):
            return TargetRef(namespace=normalized_namespace, kind="deployment", name=target)
        if _call_with_optional_cluster(_resource_exists, normalized_namespace, "service", target, cluster=cluster):
            return TargetRef(namespace=normalized_namespace, kind="service", name=target)

    return TargetRef(namespace=normalized_namespace, kind="pod", name=target)


def _cluster_args(cluster: ResolvedCluster | None) -> list[str]:
    args: list[str] = []
    if not cluster:
        return args
    if cluster.kubeconfig_path:
        args.extend(["--kubeconfig", cluster.kubeconfig_path])
    if cluster.kube_context:
        args.extend(["--context", cluster.kube_context])
    return args


def _run_kubectl(args: list[str], cluster: ResolvedCluster | None = None) -> tuple[bool, str]:
    cmd = ["kubectl", *_cluster_args(cluster), *args]
    try:
        completed = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=10)
    except Exception as exc:
        return False, str(exc)

    if completed.returncode != 0:
        return False, completed.stderr.strip() or completed.stdout.strip()
    return True, completed.stdout


def _resource_exists(namespace: str, kind: str, name: str, cluster: ResolvedCluster | None = None) -> bool:
    if kind == "node":
        ok, _ = _call_with_optional_cluster(_run_kubectl, ["get", kind, name, "-o", "name"], cluster=cluster)
        return ok
    ok, _ = _call_with_optional_cluster(_run_kubectl, ["-n", namespace, "get", kind, name, "-o", "name"], cluster=cluster)
    return ok


def _first_pod_with_prefix(namespace: str, prefix: str, cluster: ResolvedCluster | None = None) -> str | None:
    ok, pods_json = _call_with_optional_cluster(_run_kubectl, ["-n", namespace, "get", "pods", "-o", "json"], cluster=cluster)
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

    candidates.sort(key=lambda i: i.get("metadata", {}).get("creationTimestamp", ""), reverse=True)
    return candidates[0].get("metadata", {}).get("name")


def resolve_runtime_target(target: TargetRef, cluster: ResolvedCluster | None = None) -> TargetRef:
    if target.kind != "pod":
        return target

    if _call_with_optional_cluster(_resource_exists, target.namespace, "pod", target.name, cluster=cluster):
        return target
    if _call_with_optional_cluster(_resource_exists, target.namespace, "deployment", target.name, cluster=cluster):
        return TargetRef(namespace=target.namespace, kind="deployment", name=target.name)

    matched_pod = _call_with_optional_cluster(_first_pod_with_prefix, target.namespace, target.name, cluster=cluster)
    if matched_pod:
        return TargetRef(namespace=target.namespace, kind="pod", name=matched_pod)

    return target


def get_k8s_object(target: TargetRef, cluster: ResolvedCluster | None = None) -> dict:
    args = ["get", target.kind, target.name, "-o", "json"]
    if target.namespace:
        args = ["-n", target.namespace, *args]
    ok, output = _call_with_optional_cluster(_run_kubectl, args, cluster=cluster)
    if not ok:
        return {"error": output, "namespace": target.namespace, "kind": target.kind, "name": target.name}

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return {"error": "invalid kubectl json", "raw": output[:400]}

    metadata = parsed.get("metadata", {})
    status = parsed.get("status", {})
    spec = parsed.get("spec", {})
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
    if target.kind == "pod":
        container_details: list[dict] = []
        status_by_name = {
            item.get("name"): item for item in status.get("containerStatuses", []) if item.get("name")
        }
        for container in spec.get("containers", []):
            name = container.get("name")
            container_status = status_by_name.get(name, {})
            last_terminated = container_status.get("lastState", {}).get("terminated", {}) or {}
            waiting_state = container_status.get("state", {}).get("waiting", {}) or {}
            running_state = container_status.get("state", {}).get("running", {}) or {}
            container_details.append(
                {
                    "name": name,
                    "ready": container_status.get("ready"),
                    "restartCount": container_status.get("restartCount", 0),
                    "image": container.get("image"),
                    "command": container.get("command", []),
                    "args": container.get("args", []),
                    "waitingReason": waiting_state.get("reason"),
                    "lastTerminationReason": last_terminated.get("reason"),
                    "lastTerminationExitCode": last_terminated.get("exitCode"),
                    "lastTerminationMessage": last_terminated.get("message"),
                    "startedAt": running_state.get("startedAt"),
                }
            )
        response["containers"] = container_details
    if target.kind == "node":
        response["conditions"] = status.get("conditions", [])
        response["allocatable"] = status.get("allocatable", {})
        response["capacity"] = status.get("capacity", {})
        response["top_pods_by_memory_request"] = _top_pods_for_node(target.name, cluster=cluster)
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


def _top_pods_for_node(node_name: str, limit: int = 5, cluster: ResolvedCluster | None = None) -> list[dict]:
    ok, pods_json = _call_with_optional_cluster(
        _run_kubectl,
        ["get", "pods", "-A", "--field-selector", f"spec.nodeName={node_name}", "-o", "json"],
        cluster=cluster,
    )
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


def get_top_pods_for_node(node_name: str, limit: int = 5, cluster: ResolvedCluster | None = None) -> list[dict]:
    return _top_pods_for_node(node_name=node_name, limit=limit, cluster=cluster)


def get_pods_for_node(node_name: str, limit: int = 10, cluster: ResolvedCluster | None = None) -> list[dict]:
    ok, pods_json = _call_with_optional_cluster(
        _run_kubectl,
        ["get", "pods", "-A", "--field-selector", f"spec.nodeName={node_name}", "-o", "json"],
        cluster=cluster,
    )
    if not ok:
        return []
    try:
        items = json.loads(pods_json).get("items", [])
    except json.JSONDecodeError:
        return []

    pods = [
        {
            "namespace": item.get("metadata", {}).get("namespace"),
            "name": item.get("metadata", {}).get("name"),
            "creationTimestamp": item.get("metadata", {}).get("creationTimestamp"),
        }
        for item in items
    ]
    pods.sort(key=lambda item: item.get("creationTimestamp") or "", reverse=True)
    return pods[:limit]


def get_events(
    namespace: str | None,
    involved_kind: str | None = None,
    involved_name: str | None = None,
    limit: int = 20,
    cluster: ResolvedCluster | None = None,
) -> list[dict]:
    args = ["get", "events", "-o", "json"]
    if namespace:
        args = ["-n", namespace, *args]
    else:
        args = ["-A", *args]
    if involved_kind or involved_name:
        selectors: list[str] = []
        if involved_kind:
            selectors.append(f"involvedObject.kind={involved_kind}")
        if involved_name:
            selectors.append(f"involvedObject.name={involved_name}")
        args.extend(["--field-selector", ",".join(selectors)])
    ok, output = _call_with_optional_cluster(_run_kubectl, args, cluster=cluster)
    if not ok:
        return []
    try:
        items = json.loads(output).get("items", [])
    except json.JSONDecodeError:
        return []

    def timestamp(item: dict) -> str:
        return (
            item.get("eventTime")
            or item.get("lastTimestamp")
            or item.get("firstTimestamp")
            or item.get("metadata", {}).get("creationTimestamp")
            or ""
        )

    items.sort(key=timestamp, reverse=True)
    return items[:limit]


def get_service_related_deployments(
    namespace: str,
    service_name: str,
    limit: int = 5,
    cluster: ResolvedCluster | None = None,
) -> list[dict]:
    ok, service_json = _call_with_optional_cluster(
        _run_kubectl,
        ["-n", namespace, "get", "service", service_name, "-o", "json"],
        cluster=cluster,
    )
    if not ok:
        return []
    try:
        service = json.loads(service_json)
    except json.JSONDecodeError:
        return []

    selector = service.get("spec", {}).get("selector", {}) or {}
    if not selector:
        return []

    selector_items = set(selector.items())
    ok, deployments_json = _call_with_optional_cluster(
        _run_kubectl,
        ["-n", namespace, "get", "deployments", "-o", "json"],
        cluster=cluster,
    )
    if not ok:
        return []
    try:
        deployments = json.loads(deployments_json).get("items", [])
    except json.JSONDecodeError:
        return []

    related: list[dict] = []
    for item in deployments:
        template_labels = item.get("spec", {}).get("template", {}).get("metadata", {}).get("labels", {}) or {}
        match_labels = item.get("spec", {}).get("selector", {}).get("matchLabels", {}) or {}
        label_pool = dict(template_labels)
        label_pool.update(match_labels)
        if not selector_items.issubset(set(label_pool.items())):
            continue
        images = [
            container.get("image")
            for container in item.get("spec", {}).get("template", {}).get("spec", {}).get("containers", [])
            if container.get("image")
        ]
        related.append(
            {
                "kind": "deployment",
                "namespace": namespace,
                "name": item.get("metadata", {}).get("name"),
                "timestamp": item.get("metadata", {}).get("creationTimestamp")
                or item.get("status", {}).get("conditions", [{}])[-1].get("lastUpdateTime"),
                "images": images,
            }
        )

    related.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return related[:limit]


def find_unhealthy_workloads(
    namespace: str,
    limit: int = 5,
    cluster: ResolvedCluster | None = None,
) -> UnhealthyWorkloadsResponse:
    ok, pods_json = _call_with_optional_cluster(
        _run_kubectl,
        ["-n", namespace, "get", "pods", "-o", "json"],
        cluster=cluster,
    )
    cluster_alias = cluster.alias if cluster else "current-context"
    if not ok:
        return UnhealthyWorkloadsResponse(
            cluster=cluster_alias,
            namespace=namespace,
            candidates=[],
            limitations=[f"pod query failed: {pods_json}"],
        )

    try:
        items = json.loads(pods_json).get("items", [])
    except json.JSONDecodeError:
        return UnhealthyWorkloadsResponse(
            cluster=cluster_alias,
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
        cluster=cluster_alias,
        namespace=namespace,
        candidates=[candidate for _, candidate in candidates[:limit]],
    )


def get_related_events(target: TargetRef, limit: int = 20, cluster: ResolvedCluster | None = None) -> list[str]:
    event_targets: list[tuple[str | None, str]] = [(target.kind.capitalize(), target.name)]
    if target.kind == "deployment":
        pod_name = _call_with_optional_cluster(
            _first_pod_for_deployment,
            target.namespace,
            target.name,
            cluster=cluster,
        )
        if pod_name:
            event_targets.append(("Pod", pod_name))

    lines: list[str] = []
    for kind, name in event_targets:
        args = [
            "get",
            "events",
            "--sort-by=.lastTimestamp",
            "-o",
            "custom-columns=TYPE:.type,REASON:.reason,MESSAGE:.message",
            "--no-headers",
        ]
        selectors = [f"involvedObject.name={name}"]
        if kind:
            selectors.append(f"involvedObject.kind={kind}")
        args.extend(["--field-selector", ",".join(selectors)])
        if target.namespace:
            args = ["-n", target.namespace, *args]
        else:
            args = ["-A", *args]
        ok, output = _call_with_optional_cluster(_run_kubectl, args, cluster=cluster)
        if not ok:
            continue
        lines.extend([line.strip() for line in output.splitlines() if line.strip()])

    lines = list(dict.fromkeys(lines))
    lines = lines[-limit:]
    if not lines:
        return ["no related events"]
    return lines


def _first_pod_for_deployment(
    namespace: str,
    deployment_name: str,
    cluster: ResolvedCluster | None = None,
) -> str | None:
    ok, deploy_json = _call_with_optional_cluster(
        _run_kubectl,
        ["-n", namespace, "get", "deployment", deployment_name, "-o", "json"],
        cluster=cluster,
    )
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
    ok, pods_json = _call_with_optional_cluster(
        _run_kubectl,
        ["-n", namespace, "get", "pods", "-l", selector, "-o", "json"],
        cluster=cluster,
    )
    if not ok:
        return None

    try:
        pod_list = json.loads(pods_json).get("items", [])
    except json.JSONDecodeError:
        return None

    if not pod_list:
        return None
    return pod_list[0].get("metadata", {}).get("name")


def get_pod_logs(target: TargetRef, tail: int = 200, cluster: ResolvedCluster | None = None) -> str:
    if target.kind == "node":
        return "logs unavailable for node targets"
    pod_name = target.name
    if target.kind == "deployment":
        resolved = _call_with_optional_cluster(
            _first_pod_for_deployment,
            target.namespace,
            target.name,
            cluster=cluster,
        )
        if not resolved:
            return "no pod found for deployment"
        pod_name = resolved

    if target.kind not in ("pod", "deployment"):
        return "logs only supported for pod or deployment targets"

    ok, output = _call_with_optional_cluster(
        _run_kubectl,
        ["-n", target.namespace, "logs", pod_name, "--tail", str(tail), "--timestamps=true"],
        cluster=cluster,
    )
    current_logs = output.strip() if ok else ""

    previous_ok, previous_output = _call_with_optional_cluster(
        _run_kubectl,
        ["-n", target.namespace, "logs", pod_name, "--previous", "--tail", str(tail), "--timestamps=true"],
        cluster=cluster,
    )
    previous_logs = previous_output.strip() if previous_ok else ""

    if not ok and not previous_ok:
        return f"log query failed: {output}"

    if current_logs and previous_logs:
        return f"{current_logs}\n--- previous container logs ---\n{previous_logs}"
    if previous_logs:
        return previous_logs
    if current_logs:
        return current_logs

    return ""
