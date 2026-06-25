from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class QueryBenchmarkResult:
    name: str
    repeat: int
    elapsed_ms: float
    row_count: int
    mpp_used: bool
    explain_text: str


ANALYTICS_QUERIES: dict[str, str] = {
    "daily_site_order_summary": """
        SELECT
            e.site_id,
            DATE(e.created_at) AS event_day,
            COUNT(DISTINCT CASE WHEN e.event_type = 'order_stored' THEN e.order_id END) AS stored_orders,
            COUNT(DISTINCT CASE WHEN e.event_type = 'order_collected' THEN e.order_id END) AS collected_orders,
            COUNT(DISTINCT img.id) AS image_count,
            ROUND(AVG(inf.confidence), 4) AS avg_confidence,
            ROUND(AVG(inf.inference_ms), 2) AS avg_inference_ms
        FROM locker_events e
        LEFT JOIN parcel_image_assets img ON img.order_id = e.order_id
        LEFT JOIN parcel_inference_results inf ON inf.image_id = img.id
        WHERE e.created_at >= :since
        GROUP BY e.site_id, DATE(e.created_at)
        ORDER BY event_day DESC, e.site_id
    """,
    "locker_utilization_summary": """
        SELECT
            e.site_id,
            e.locker_id,
            COUNT(DISTINCT CASE WHEN e.event_type = 'order_stored' THEN e.order_id END) AS stored_orders,
            COUNT(DISTINCT CASE WHEN e.event_type = 'door_open' THEN e.order_id END) AS door_opens,
            COUNT(DISTINCT CASE WHEN e.event_type = 'issue_report' THEN e.order_id END) AS issue_events
        FROM locker_events e
        WHERE e.created_at >= :since
        GROUP BY e.site_id, e.locker_id
        ORDER BY stored_orders DESC, e.site_id, e.locker_id
        LIMIT 20
    """,
    "ml_model_quality_summary": """
        SELECT
            r.model_name,
            r.label,
            COUNT(*) AS detection_count,
            ROUND(AVG(r.confidence), 4) AS avg_confidence,
            ROUND(AVG(r.inference_ms), 2) AS avg_inference_ms,
            COUNT(DISTINCT r.site_id) AS active_sites
        FROM parcel_inference_results r
        WHERE r.created_at >= :since
        GROUP BY r.model_name, r.label
        ORDER BY detection_count DESC, avg_confidence DESC
        LIMIT 50
    """,
}


def maybe_enable_tidb_mpp(session: Session, enforce_mpp: bool = False) -> tuple[bool, str]:
    """Attempt to enable MPP mode when connected to TiDB.

    Returns:
        (success, message)
    """
    try:
        session.execute(text("SET @@session.tidb_allow_mpp = 1"))
        session.execute(text(f"SET @@session.tidb_enforce_mpp = {1 if enforce_mpp else 0}"))
        session.commit()
        return True, f"TiDB MPP session enabled (enforce_mpp={enforce_mpp})."
    except Exception as exc:
        session.rollback()
        return False, f"Skipping TiDB MPP session settings: {exc}"


def run_query_benchmarks(
    session: Session,
    days: int = 30,
    repeat: int = 3,
    use_tidb_mpp: bool = False,
    enforce_mpp: bool = False,
) -> dict[str, Any]:
    since = datetime.now() - timedelta(days=max(1, days))
    mpp_success = False
    mpp_message = "TiDB MPP session settings were not requested."
    if use_tidb_mpp:
        mpp_success, mpp_message = maybe_enable_tidb_mpp(session, enforce_mpp=enforce_mpp)

    results: list[QueryBenchmarkResult] = []
    for name, sql in ANALYTICS_QUERIES.items():
        explain_rows = session.execute(text(f"EXPLAIN {sql}"), {"since": since}).all()
        explain_text = "\n".join(" | ".join(str(value) for value in row) for row in explain_rows)
        mpp_used = "ExchangeSender" in explain_text or "ExchangeReceiver" in explain_text or "mpp[tiflash]" in explain_text

        elapsed_total = 0.0
        row_count = 0
        for _ in range(max(1, repeat)):
            started = time.perf_counter()
            rows = session.execute(text(sql), {"since": since}).all()
            elapsed_total += (time.perf_counter() - started) * 1000.0
            row_count = len(rows)

        results.append(
            QueryBenchmarkResult(
                name=name,
                repeat=max(1, repeat),
                elapsed_ms=elapsed_total / max(1, repeat),
                row_count=row_count,
                mpp_used=mpp_used,
                explain_text=explain_text,
            )
        )

    return {
        "since": since.isoformat(timespec="seconds"),
        "tidb_mpp_requested": use_tidb_mpp,
        "tidb_mpp_applied": mpp_success,
        "tidb_mpp_message": mpp_message,
        "results": [asdict(item) for item in results],
    }
