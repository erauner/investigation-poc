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
    assert resolved.loki_url is None
    assert resolved.kubeconfig_path == str(kubeconfig)
    assert resolved.use_in_cluster is False
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
    kubeconfig = tmp_path / "multi-kubeconfig"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n")
    monkeypatch.setenv("KUBECONFIG_PATH", str(kubeconfig))

    resolved = resolve_cluster(None, {"cluster": "remote-b"})

    assert resolved.alias == "kind-b"
    assert resolved.source == "alert_label"


def test_resolve_cluster_carries_optional_loki_url(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
clusters:
  kind-a:
    kube_context: kind-investigation-a
    prometheus_url: http://prom-a:9090
    loki_url: http://loki-a:3100
    default: true
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))
    kubeconfig = tmp_path / "multi-kubeconfig"
    kubeconfig.write_text("apiVersion: v1\nkind: Config\n")
    monkeypatch.setenv("KUBECONFIG_PATH", str(kubeconfig))

    resolved = resolve_cluster(None)

    assert resolved.alias == "kind-a"
    assert resolved.loki_url == "http://loki-a:3100"


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
    monkeypatch.setenv("CLUSTER_NAME", "erauner-home")

    resolved = resolve_cluster(None)

    assert resolved.alias == "erauner-home"
    assert resolved.kubeconfig_path is None
    assert resolved.kube_context is None
    assert resolved.use_in_cluster is True


def test_resolve_cluster_rejects_remote_alias_when_kubeconfig_is_missing(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
clusters:
  erauner-home:
    kube_context: erauner-home
    default: true
  remote-a:
    kube_context: remote-a
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))
    monkeypatch.setenv("KUBECONFIG_PATH", "/etc/investigation/kubeconfig/config")
    monkeypatch.setenv("CLUSTER_NAME", "erauner-home")

    try:
        resolve_cluster("remote-a")
    except ValueError as exc:
        assert "requires kubeconfig context" in str(exc)
    else:
        raise AssertionError("expected remote alias to fail when kubeconfig is missing")


def test_resolve_cluster_accepts_explicit_current_context_in_legacy_mode(monkeypatch) -> None:
    monkeypatch.delenv("CLUSTER_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("DEFAULT_CLUSTER_ALIAS", raising=False)
    monkeypatch.delenv("CLUSTER_NAME", raising=False)

    resolved = resolve_cluster("current-context")

    assert resolved.alias == "current-context"
    assert resolved.use_in_cluster is True
    assert resolved.source == "legacy_current_context"


def test_resolve_cluster_accepts_explicit_cluster_name_in_legacy_mode(monkeypatch) -> None:
    monkeypatch.delenv("CLUSTER_REGISTRY_PATH", raising=False)
    monkeypatch.delenv("DEFAULT_CLUSTER_ALIAS", raising=False)
    monkeypatch.setenv("CLUSTER_NAME", "kind-investigation")

    resolved = resolve_cluster("kind-investigation")

    assert resolved.alias == "kind-investigation"
    assert resolved.use_in_cluster is True
    assert resolved.source == "legacy_current_context"


def test_resolve_cluster_accepts_current_context_when_registry_is_configured(monkeypatch, tmp_path) -> None:
    path = tmp_path / "clusters.yaml"
    path.write_text(
        """
clusters:
  kind-a:
    kube_context: kind-investigation-a
"""
    )
    monkeypatch.setenv("CLUSTER_REGISTRY_PATH", str(path))

    resolved = resolve_cluster("current-context")

    assert resolved.alias == "current-context"
    assert resolved.use_in_cluster is True
    assert resolved.source == "legacy_current_context"
