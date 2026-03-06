import json
import subprocess

from .models import TargetRef


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
        else:
            kind = "pod"
        return TargetRef(namespace=namespace, kind=kind, name=name)

    return TargetRef(namespace=namespace, kind="pod", name=target)


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
    ok, output = _run_kubectl(["-n", target.namespace, "get", target.kind, target.name, "-o", "json"])
    if not ok:
        return {"error": output, "namespace": target.namespace, "kind": target.kind, "name": target.name}

    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return {"error": "invalid kubectl json", "raw": output[:400]}

    metadata = parsed.get("metadata", {})
    status = parsed.get("status", {})
    return {
        "namespace": target.namespace,
        "kind": target.kind,
        "name": target.name,
        "phase": status.get("phase"),
        "readyReplicas": status.get("readyReplicas"),
        "replicas": status.get("replicas"),
        "observedGeneration": status.get("observedGeneration"),
        "creationTimestamp": metadata.get("creationTimestamp"),
    }


def get_related_events(target: TargetRef, limit: int = 20) -> list[str]:
    names = [target.name]
    if target.kind == "deployment":
        pod_name = _first_pod_for_deployment(target.namespace, target.name)
        if pod_name:
            names.append(pod_name)

    lines: list[str] = []
    for name in names:
        ok, output = _run_kubectl(
            [
                "-n",
                target.namespace,
                "get",
                "events",
                "--sort-by=.lastTimestamp",
                "--field-selector",
                f"involvedObject.name={name}",
                "-o",
                "custom-columns=TYPE:.type,REASON:.reason,MESSAGE:.message",
                "--no-headers",
            ]
        )
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
