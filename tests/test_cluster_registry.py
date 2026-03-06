from investigation_service.cluster_registry import list_clusters, resolve_cluster


def test_resolve_cluster_uses_explicit_alias(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
default_cluster: kind-a
clusters:
  kind-a:
    kube_context: kind-investigation-a
    prometheus_url: http://prom-a:9090
  kind-b:
    kube_context: kind-investigation-b
    prometheus_url: http://prom-b:9090
    label_aliases:
      - remote-b
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))
    kubeconfig = tmp_path / "multi-kubeconfig"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n")
    monkeypatch.setenv("KUBECONFIG_PATH", str(kubeconfig))

    resolved = resolve_cluster("kind-b")

    assert resolved.alias == "kind-b"
    assert resolved.kube_context == "kind-investigation-b"
    assert resolved.prometheus_url == "http://prom-b:9090"
    assert resolved.kubeconfig_path == str(kubeconfig)
    assert resolved.source == "explicit"


def test_resolve_cluster_uses_label_alias(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
clusters:
  kind-a:
    kube_context: kind-investigation-a
    prometheus_url: http://prom-a:9090
    default: true
  kind-b:
    kube_context: kind-investigation-b
    prometheus_url: http://prom-b:9090
    label_aliases:
      - remote-b
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))

    resolved = resolve_cluster(None, {"cluster": "remote-b"})

    assert resolved.alias == "kind-b"
    assert resolved.source == "alert_label"


def test_resolve_cluster_requires_alias_when_registry_has_no_default(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
clusters:
  kind-a:
    kube_context: kind-investigation-a
  kind-b:
    kube_context: kind-investigation-b
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))

    try:
        resolve_cluster(None)
    except ValueError as exc:
        assert "cluster is required" in str(exc)
    else:
        raise AssertionError("expected resolve_cluster to reject ambiguous registry")


def test_list_clusters_reads_registry(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
clusters:
  kind-b:
    kube_context: kind-investigation-b
  kind-a:
    kube_context: kind-investigation-a
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))

    assert list_clusters() == ["kind-a", "kind-b"]


def test_resolve_cluster_ignores_missing_optional_kubeconfig(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
clusters:
  erauner-home:
    kube_context: erauner-home
    default: true
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))
    monkeypatch.setenv("KUBECONFIG_PATH", "/etc/investigation/kubeconfig/config")

    resolved = resolve_cluster(None)

    assert resolved.alias == "erauner-home"
    assert resolved.kubeconfig_path is None
    assert resolved.kube_context is None
