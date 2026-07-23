"""Backward-compatible OCR streaming response helpers.

The persistent OCR API adds task metadata, but established clients still read
the last SSE event as the original per-page result and expect top-level
``success``, ``data`` or ``raw`` fields.  This module projects a persisted task
snapshot onto both contracts without duplicating inference results.
"""

from __future__ import annotations

from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def build_ocr_stream_payload(
    *,
    task_id: int,
    status: str,
    progress: float,
    progress_message: str | None,
    error_message: str | None,
    result_data: Any,
) -> dict[str, Any]:
    """Return one payload understood by both legacy and task-aware clients."""

    persisted = result_data if isinstance(result_data, dict) else {}
    stored_results = persisted.get("results", [])
    results = stored_results if isinstance(stored_results, list) else []
    latest = results[-1] if results and isinstance(results[-1], dict) else {}

    # Start with the old page-result contract. This preserves fields including
    # success, data, raw, json, markdown, provider, task and layout.
    payload = dict(latest)
    payload.update(
        {
            "task_id": task_id,
            "status": status,
            "page": latest.get("page", 0),
            "total": latest.get("total", 0),
            "percent": progress or 0,
            "message": progress_message,
            "error": error_message or latest.get("error"),
            "done": status in TERMINAL_STATUSES,
            "all_results": results,
            "document": persisted.get("document"),
            "merged": persisted.get("merged"),
        }
    )
    if status == "failed":
        payload["success"] = False
    return payload
