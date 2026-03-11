from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from investigation_service.event_fingerprints import parse_compact_event_text

from .models import (
    EvidenceItem,
    InvestigationReport,
    InvestigationSubjectRef,
    PlannerSeedExecutionFocus,
    PresentationProfileType,
    ResolvedGuideline,
)


@dataclass(frozen=True)
class PresentationSection:
    key: str
    title: str
    lines: list[str]


@dataclass(frozen=True)
class PresentationDocument:
    profile: PresentationProfileType
    sections: list[PresentationSection]


def render_presentation_document(
    report: InvestigationReport,
    profile: PresentationProfileType = "operator_summary",
) -> PresentationDocument:
    renderer = _PROFILE_RENDERERS[profile]
    return PresentationDocument(profile=profile, sections=renderer(report))


def render_presentation_markdown(
    report: InvestigationReport,
    profile: PresentationProfileType = "operator_summary",
) -> str:
    document = render_presentation_document(report, profile)
    blocks = []
    for section in document.sections:
        body = "\n".join(section.lines).rstrip() if section.lines else "None."
        blocks.append(f"## {section.title}\n{body}".rstrip())
    return "\n\n".join(blocks).strip()


def _render_operator_summary(report: InvestigationReport) -> list[PresentationSection]:
    return [
        PresentationSection("diagnosis", "Diagnosis", [report.diagnosis]),
        PresentationSection("evidence", "Evidence", _bullet_lines(_summary_evidence_lines(report), fallback="None.")),
        PresentationSection("related_data", "Related Data", _related_data_lines(report)),
        PresentationSection("limitations", "Limitations", _bullet_lines(report.limitations, fallback="None reported.")),
        PresentationSection("next_step", "Recommended next step", [report.recommended_next_step]),
    ]


def _render_incident_report(report: InvestigationReport) -> list[PresentationSection]:
    summary = [
        f"Target: {report.target}",
        f"Diagnosis: {report.diagnosis}",
        f"Confidence: {report.confidence}",
    ]
    if report.likely_cause:
        summary.append(f"Likely cause: {report.likely_cause}")
    next_actions = [report.recommended_next_step, *_bullet_lines(report.suggested_follow_ups, fallback="").copy()]
    return [
        PresentationSection("summary", "Incident Summary", summary),
        PresentationSection("evidence", "Supporting Evidence", _bullet_lines(_verbose_evidence_lines(report), fallback="None.")),
        PresentationSection("related_context", "Related Context", _related_data_lines(report)),
        PresentationSection("limitations", "Limitations", _bullet_lines(report.limitations, fallback="None reported.")),
        PresentationSection("next_actions", "Next Actions", [line for line in next_actions if line]),
    ]


def _render_debug_trace(report: InvestigationReport) -> list[PresentationSection]:
    trace_lines = [
        f"Target: {report.target}",
        f"Diagnosis: {report.diagnosis}",
        f"Confidence: {report.confidence}",
    ]
    trace_lines.extend(_focus_debug_lines(report))
    if report.tool_path_trace is not None:
        trace_lines.extend(
            [
                f"Planner path used: {report.tool_path_trace.planner_path_used}",
                f"Trace source: {report.tool_path_trace.source}",
                f"Executed batches: {', '.join(report.tool_path_trace.executed_batch_ids) or 'none'}",
                f"Executed steps: {', '.join(report.tool_path_trace.executed_step_ids) or 'none'}",
            ]
        )
        for item in report.tool_path_trace.step_provenance:
            path = " > ".join(item.provenance.actual_route.tool_path) or "none"
            trace_lines.append(f"{item.step_id}: {path}")
    else:
        trace_lines.append("Trace: unavailable")

    guideline_lines = _guideline_lines(report.guidelines)
    if report.normalization_notes:
        guideline_lines.extend(f"- {item}" for item in report.normalization_notes)

    return [
        PresentationSection("diagnosis", "Diagnosis", [report.diagnosis]),
        PresentationSection("evidence", "Evidence", _bullet_lines(_verbose_evidence_lines(report), fallback="None.")),
        PresentationSection("related_data", "Related Data", _related_data_lines(report)),
        PresentationSection("limitations", "Limitations", _bullet_lines(report.limitations, fallback="None reported.")),
        PresentationSection("next_step", "Recommended next step", [report.recommended_next_step]),
        PresentationSection("trace", "Debug Trace", trace_lines),
        PresentationSection(
            "notes",
            "Notes",
            guideline_lines or ["No guidelines or normalization notes."],
        ),
    ]


def _focus_debug_lines(report: InvestigationReport) -> list[str]:
    focus = report.focus_provenance
    if focus is None:
        return []
    lines: list[str] = []
    if focus.requested_subject:
        lines.append(f"Requested subject: {focus.requested_subject}")
    if focus.soft_primary_focus is not None:
        lines.append(f"Soft primary focus: {_format_subject_ref(focus.soft_primary_focus)}")
    if focus.initial_bounded_execution_focus is not None:
        lines.append(f"Initial bounded focus: {_format_execution_focus(focus.initial_bounded_execution_focus)}")
    if focus.current_bounded_execution_focus is not None:
        lines.append(f"Current bounded focus: {_format_execution_focus(focus.current_bounded_execution_focus)}")
    if focus.initial_focus_reasons:
        lines.append(f"Initial focus reasons: {'; '.join(focus.initial_focus_reasons)}")
    if focus.latest_focus_change_reasons:
        source = f" ({focus.latest_focus_change_source_step_id})" if focus.latest_focus_change_source_step_id else ""
        lines.append(f"Latest focus change{source}: {'; '.join(focus.latest_focus_change_reasons)}")
    if focus.related_subjects_considered:
        lines.append(
            "Related subjects considered: "
            + ", ".join(_format_subject_ref(subject) for subject in focus.related_subjects_considered)
        )
    return lines


def _format_subject_ref(subject: InvestigationSubjectRef) -> str:
    namespace = f"{subject.namespace}/" if subject.namespace else ""
    cluster = f" [{subject.cluster}]" if subject.cluster else ""
    return f"{subject.kind}:{namespace}{subject.name}{cluster}"


def _format_execution_focus(focus: PlannerSeedExecutionFocus) -> str:
    extras: list[str] = [focus.profile]
    if focus.service_name:
        extras.append(f"service={focus.service_name}")
    if focus.node_name:
        extras.append(f"node={focus.node_name}")
    return f"{focus.scope}:{focus.target} ({', '.join(extras)})"


def _render_explain_more(report: InvestigationReport) -> list[PresentationSection]:
    diagnosis_lines = [
        f"Diagnosis: {report.diagnosis}",
        f"Confidence: {report.confidence}",
        f"Target: {report.target}",
    ]
    if report.likely_cause:
        diagnosis_lines.append(f"Likely cause: {report.likely_cause}")
    follow_up_lines = [f"- {report.recommended_next_step}"]
    follow_up_lines.extend(f"- {item}" for item in report.suggested_follow_ups)
    follow_up_lines.extend(_guideline_lines(report.guidelines))
    return [
        PresentationSection("diagnosis", "Diagnosis", diagnosis_lines),
        PresentationSection("why", "Why This Conclusion", _bullet_lines(_verbose_evidence_lines(report), fallback="None.")),
        PresentationSection("related_data", "Related Data", _related_data_lines(report)),
        PresentationSection("limitations", "Limitations", _bullet_lines(report.limitations, fallback="None reported.")),
        PresentationSection("follow_ups", "Follow-ups And Guidance", follow_up_lines or ["- None."]),
    ]


def _summary_evidence_lines(report: InvestigationReport) -> list[str]:
    if report.evidence_items:
        rendered: list[str] = []
        seen: set[str] = set()
        for item in report.evidence_items:
            line = _format_evidence_item(item)
            normalized = line.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rendered.append(line)
        if rendered:
            return _collapse_overlapping_evidence(rendered)

    return _collapse_overlapping_evidence(_legacy_evidence_lines(report))


def _verbose_evidence_lines(report: InvestigationReport) -> list[str]:
    if report.evidence_items:
        rendered: list[str] = []
        seen: set[str] = set()
        for item in report.evidence_items:
            line = _format_evidence_item(item)
            normalized = line.strip().lower()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rendered.append(line)
        if rendered:
            return rendered

    return _legacy_evidence_lines(report)


def _legacy_evidence_lines(report: InvestigationReport) -> list[str]:
    rendered = []
    seen: set[str] = set()
    for item in report.evidence:
        line = _sanitize_evidence_text(item)
        normalized = line.strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        rendered.append(line)
    return rendered


def _format_evidence_item(item: EvidenceItem) -> str:
    summary = _sanitize_evidence_text(item.summary)
    detail = _sanitize_evidence_text(item.detail or "")
    if summary.startswith("events: "):
        summary = summary.removeprefix("events: ").strip()
    if item.kind == "event" and detail:
        extracted = _extract_event_detail(item.detail or "")
        if extracted:
            return f"{summary}: {extracted}"
        reason, message = parse_compact_event_text(detail)
        return f"{summary}: {reason} - {message}"
    if detail and _should_inline_detail(detail):
        return f"{summary} - {detail}"
    return summary


def _sanitize_evidence_text(value: str) -> str:
    text = " ".join(value.split())
    text = text.replace(" - # The following events (YAML format) were found:", "")
    text = text.replace("# The following events (YAML format) were found:", "")
    return text.strip()


def _extract_event_detail(detail: str) -> str | None:
    normalized = _sanitize_evidence_text(detail)
    patterns = [
        r"(Back-off restarting failed container .*?)(?:\s+Namespace:|$)",
        r"(CrashLoopBackOff.*?)(?:\s+Namespace:|$)",
        r"(ImagePullBackOff.*?)(?:\s+Namespace:|$)",
        r"(OOMKilled.*?)(?:\s+Namespace:|$)",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match is not None:
            return match.group(1).strip()

    yaml_warning = re.search(
        r"Message:\s+(.+?)\s+Namespace:.*?Reason:\s+([A-Za-z0-9]+).*?Type:\s+Warning",
        normalized,
        re.IGNORECASE,
    )
    if yaml_warning is not None:
        message = yaml_warning.group(1).strip()
        reason = yaml_warning.group(2).strip()
        return f"{reason} - {message}"

    return None


def _should_inline_detail(detail: str) -> bool:
    if not detail:
        return False
    if len(detail) > 180:
        return False
    if "InvolvedObject:" in detail or "Timestamp:" in detail:
        return False
    return True


def _collapse_overlapping_evidence(items: list[str]) -> list[str]:
    if len(items) < 2:
        return items

    lowered = [item.lower() for item in items]
    keep = [True] * len(items)
    has_backoff_event = any("back-off restarting failed container" in item for item in lowered)

    if has_backoff_event:
        for idx, item in enumerate(lowered):
            if "crash loop detected" in item and "back-off restarting failed container" not in item:
                keep[idx] = False

    collapsed = [item for item, include in zip(items, keep, strict=False) if include]
    return collapsed or items


def _bullet_lines(items: list[str], *, fallback: str = "None.") -> list[str]:
    if not items:
        return [fallback]
    return [f"- {item}" for item in items]


def _related_data_lines(report: InvestigationReport) -> list[str]:
    if report.related_data:
        return [f"- {item.timestamp} {item.summary}" for item in report.related_data]
    if report.related_data_note:
        return [report.related_data_note]
    return ["No related data reported."]


def _guideline_lines(guidelines: list[ResolvedGuideline]) -> list[str]:
    if not guidelines:
        return []
    return [f"- [{item.category}] {item.text}" for item in guidelines]


_PROFILE_RENDERERS: dict[PresentationProfileType, Callable[[InvestigationReport], list[PresentationSection]]] = {
    "operator_summary": _render_operator_summary,
    "incident_report": _render_incident_report,
    "debug_trace": _render_debug_trace,
    "explain_more": _render_explain_more,
}
