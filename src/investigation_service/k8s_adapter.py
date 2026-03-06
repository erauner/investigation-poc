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
    ok, output = _run_kubectl(
        [
            "-n",
            target.namespace,
            "get",
            "events",
            "--sort-by=.lastTimestamp",
            "--field-selector",
            f"involvedObject.name={target.name}",
            "-o",
            "custom-columns=TYPE:.type,REASON:.reason,MESSAGE:.message",
            "--no-headers",
        ]
    )
    if not ok:
        return [f"event query failed: {output}"]

    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return ["no related events"]
    return lines[-limit:]


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
