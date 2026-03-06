from .models import BuildRootCauseReportRequest, CollectContextRequest, CollectNodeContextRequest, CollectServiceContextRequest, RootCauseReport
from .synthesis import build_root_cause_report as build_root_cause_report_impl
from .tools import _scope_from_target, collect_node_context, collect_service_context, collect_workload_context


def build_root_cause_report(req: BuildRootCauseReportRequest) -> RootCauseReport:
    scope = _scope_from_target(req.target, req.profile)
    if scope == "node":
        node_name = req.target.split("/", 1)[1]
        context = collect_node_context(
            CollectNodeContextRequest(
                node_name=node_name,
                lookback_minutes=req.lookback_minutes,
            )
        )
    elif scope == "service":
        if not req.namespace:
            raise ValueError("namespace is required for service root cause reports")
        service_name = req.service_name or req.target.split("/", 1)[1]
        context = collect_service_context(
            CollectServiceContextRequest(
                namespace=req.namespace,
                service_name=service_name,
                target=req.target,
                lookback_minutes=req.lookback_minutes,
            )
        )
    else:
        context = collect_workload_context(
            CollectContextRequest(
                namespace=req.namespace,
                target=req.target,
                profile=req.profile,
                service_name=req.service_name,
                lookback_minutes=req.lookback_minutes,
            )
        )

    return build_root_cause_report_impl(
        context,
        CollectContextRequest(
            namespace=req.namespace,
            target=req.target,
            profile=req.profile,
            service_name=req.service_name,
            lookback_minutes=req.lookback_minutes,
        ),
    )
