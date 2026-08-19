"""Human-in-the-loop reporting for video similarity results.

This module deliberately reports match candidates rather than making legal
findings. Copyright infringement depends on ownership, permission, licences,
fair use/fair dealing, and context that a fingerprint cannot determine.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def generate_report(
    similarity: dict[str, Any],
    meta1: dict[str, Any],
    meta2: dict[str, Any],
    threshold: float = 75.0,
) -> dict[str, Any]:
    threshold = max(50.0, min(95.0, float(threshold)))
    score = float(similarity.get("overall", 0.0))
    review_floor = max(40.0, threshold - 15.0)

    if score >= threshold:
        verdict = "MATCH_CANDIDATE"
        verdict_text = "Strong visual match — review the context"
        severity = "high"
        confidence_band = "Above your review threshold"
    elif score >= review_floor:
        verdict = "REVIEW_REQUIRED"
        verdict_text = "Possible overlap — take a closer look"
        severity = "medium"
        confidence_band = "Close to your review threshold"
    else:
        verdict = "NO_STRONG_MATCH"
        verdict_text = "No convincing visual match"
        severity = "low"
        confidence_band = "Below your review threshold"

    original = _determine_earlier_upload(meta1, meta2)
    analysis = _build_analysis(similarity, threshold, verdict)

    return {
        "verdict": verdict,
        "verdict_text": verdict_text,
        "severity": severity,
        "confidence_band": confidence_band,
        "decision_threshold": round(threshold, 1),
        "original_video": original,
        "analysis": analysis,
        "action_steps": _action_steps(verdict),
        "human_review_required": verdict != "NO_STRONG_MATCH",
        "automated_enforcement_recommended": False,
        "limitations": [
            "Similarity does not prove ownership, copying, or copyright infringement.",
            "The system cannot determine permission, licensing, parody, commentary, or fair use/fair dealing.",
            "Publication dates may be user-supplied or platform-reported and should be verified against reliable evidence.",
            "The chosen threshold affects false alerts and missed matches; test it against labelled examples before operational use.",
        ],
        "legal_notice": (
            "Decision-support output only. A qualified human should review the videos "
            "and relevant rights information before any report or enforcement action."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def _determine_earlier_upload(meta1: dict[str, Any], meta2: dict[str, Any]) -> str:
    timestamp1 = float(meta1.get("timestamp") or 0)
    timestamp2 = float(meta2.get("timestamp") or 0)
    if timestamp1 <= 0 or timestamp2 <= 0 or timestamp1 == timestamp2:
        return "unknown"
    return "video1" if timestamp1 < timestamp2 else "video2"


def _build_analysis(
    similarity: dict[str, Any],
    threshold: float,
    verdict: str,
) -> str:
    score = float(similarity.get("overall", 0.0))
    perceptual = float(similarity.get("perceptual", 0.0))
    temporal = float(similarity.get("temporal", 0.0))
    color = float(similarity.get("color", 0.0))
    motion = float(similarity.get("motion", 0.0))
    support_gate = float(
        similarity.get("support_gate", 100.0 if perceptual >= 65.0 else 0.0)
    )
    alignment = similarity.get("alignment", {})
    orientation = alignment.get("orientation", "normal")
    time_scale = float(alignment.get("time_scale", 1.0))
    matched_frames = int(alignment.get("matched_frames", 0))
    coverage = float(alignment.get("longer_video_coverage", 0.0))

    if verdict == "MATCH_CANDIDATE":
        lead = (
            f"The similarity score is {score:.1f}%, above the selected {threshold:.1f}% "
            "review threshold. This makes the pair a high-priority match candidate, not a legal finding."
        )
    elif verdict == "REVIEW_REQUIRED":
        lead = (
            f"The similarity score is {score:.1f}%, close to the selected {threshold:.1f}% "
            "threshold. The signals are inconclusive and side-by-side human review is required."
        )
    else:
        lead = (
            f"The similarity score is {score:.1f}%, below the selected {threshold:.1f}% "
            "threshold. The current fingerprint does not provide a strong reuse signal."
        )

    evidence = (
        f"Visual structure scored {perceptual:.1f}%. The editing-rhythm ({temporal:.1f}%), colour "
        f"({color:.1f}%), and movement ({motion:.1f}%) readings use that same frame alignment, and "
        f"only {support_gate:.1f}% of their possible contribution was retained. The strongest "
        f"alignment used the {orientation} orientation at an estimated {time_scale:.2f}x time scale"
        + (f", covering {matched_frames} matched frames and {coverage:.1f}% of the longer video." if matched_frames else ".")
    )

    caution = (
        "Before acting, verify authorship and publication dates, inspect the matched scenes, and check "
        "for permission, licences, commentary, parody, or other potentially legitimate reuse. The "
        "system is designed to reduce review workload; it must not trigger automated enforcement."
    )
    return "\n\n".join((lead, evidence, caution))


def _action_steps(verdict: str) -> list[dict[str, Any]]:
    if verdict == "MATCH_CANDIDATE":
        return [
            _step(1, "Preserve evidence", "Keep any lawful original files you already hold and record both post links, account names, timestamps, screenshots, and this report before content changes.", "high", "camera"),
            _step(2, "Review side by side", "Confirm that the matched scenes represent copied expression rather than a shared trend, template, or independently similar material.", "high", "eye"),
            _step(3, "Verify rights and context", "Check ownership, permission, licences, attribution, commentary, parody, and fair use/fair dealing before drawing a conclusion.", "high", "shield"),
            _step(4, "Use the correct report route", "If a qualified reviewer and the rights holder confirm likely unauthorised reuse, use the platform's copyright process. In Nepal, seek qualified legal advice promptly; use the Cyber Bureau route when hacking, impersonation, threats, fraud, or another electronic offence is also involved.", "medium", "flag"),
        ]
    if verdict == "REVIEW_REQUIRED":
        return [
            _step(1, "Inspect the aligned scenes", "Compare the relevant frames, sequence, crops, overlays, audio, and editing choices manually.", "high", "eye"),
            _step(2, "Gather provenance", "Verify creation files and reliable publication dates; the supplied dates alone are not proof of authorship.", "medium", "camera"),
            _step(3, "Do not automate enforcement", "Treat this score as a lead only and avoid reporting or accusing a creator without stronger evidence.", "medium", "shield"),
        ]
    return [
        _step(1, "No report recommended", "The current signals do not support escalation at the selected threshold.", "info", "check"),
        _step(2, "Retain original evidence", "Keep source project files and reliable publication records in case a clearer match appears later.", "low", "shield"),
    ]


def _step(
    number: int,
    action: str,
    description: str,
    priority: str,
    icon: str,
) -> dict[str, Any]:
    return {
        "step": number,
        "action": action,
        "description": description,
        "priority": priority,
        "icon": icon,
    }
