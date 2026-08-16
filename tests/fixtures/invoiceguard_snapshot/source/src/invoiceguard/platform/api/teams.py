"""Team dashboards (``ig.platform.api``): read-only aggregates.

Three views over the same world: queue health per team (how much READY
work each team faces), auditor performance over a caller-chosen window
(claims and closes per auditor), and a trailing-30-day production
rollup (closed reviews and recovered opportunity per team per day).

The production rollup is the spec's second and final declared raw-SQL
spot — its timestamp comparison renders values the way ``UTCDateTime``
stores them, exactly like the stale sweep's. The other two dashboards
stay ORM.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from flask import Blueprint, jsonify, request
from sqlalchemy import case, func, select, text

from invoiceguard.models import (
    FindingCategory,
    Invoice,
    InvoiceHistory,
    InvoiceStatus,
    User,
    utc_naive,
)
from invoiceguard.platform.api.identity import require_user
from invoiceguard.platform.api.query_helpers import QueryValidationError
from invoiceguard.platform.bootstrap.app import get_clock, get_session
from invoiceguard.platform.config import ConfigService
from invoiceguard.spine.queue import build_eligible_query

teams_blueprint = Blueprint("teams", __name__)

PRODUCTION_WINDOW_DAYS = 30

# Statuses that count as a completed review for the dashboards.
CLOSING_STATUSES: frozenset[InvoiceStatus] = frozenset(
    {InvoiceStatus.CLOSED, InvoiceStatus.NO_REVIEW_NEEDED}
)

# Raw-SQL spot 2 of 2 (spec §3): the production-rollup aggregate.
PRODUCTION_ROLLUP_SQL = text(
    """
    SELECT users.team AS team,
           DATE(invoice_history.at) AS day,
           COUNT(*) AS closed_count,
           SUM(COALESCE(invoices.opportunity, 0)) AS recovered_opportunity
    FROM invoice_history
    JOIN users ON users.short_name = invoice_history.actor
    JOIN invoices ON invoices.id = invoice_history.invoice_id
    WHERE invoice_history.to_status IN (:closed, :no_review)
      AND invoice_history.at >= :window_start
    GROUP BY users.team, DATE(invoice_history.at)
    ORDER BY users.team, day
    """
)


@teams_blueprint.get("/teams/queue-health")
@require_user
def queue_health():
    """READY backlog per team: count, total opportunity, total weight."""
    session = get_session()
    config = ConfigService(session, get_clock()).current()
    teams = []
    for team in sorted(config.team_to_categories):
        categories = [
            FindingCategory(name)
            for name in config.team_to_categories[team]
        ]
        eligible = build_eligible_query(config, categories).subquery()
        row = session.execute(
            select(
                func.count(eligible.c.id),
                func.coalesce(func.sum(eligible.c.opportunity), 0.0),
                func.coalesce(func.sum(eligible.c.weight), 0.0),
            )
        ).one()
        teams.append(
            {
                "team": team,
                "ready_invoices": row[0],
                "total_opportunity": row[1],
                "total_weight": row[2],
            }
        )
    return jsonify({"teams": teams})


@teams_blueprint.get("/teams/auditor-performance")
@require_user
def auditor_performance():
    """Claims and closes per auditor over a half-open [start, end) window."""
    start = _window_param("start")
    end = _window_param("end")
    session = get_session()
    claims = func.sum(
        case((InvoiceHistory.to_status == InvoiceStatus.CLAIMED, 1), else_=0)
    )
    closes = func.sum(
        case((InvoiceHistory.to_status.in_(CLOSING_STATUSES), 1), else_=0)
    )
    rows = session.execute(
        select(User.short_name, User.team, claims, closes)
        .join_from(InvoiceHistory, User, InvoiceHistory.actor == User.short_name)
        .where(InvoiceHistory.at >= start)
        .where(InvoiceHistory.at < end)
        .group_by(User.short_name, User.team)
        .order_by(User.short_name)
    ).all()
    return jsonify(
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "auditors": [
                {
                    "short_name": row.short_name,
                    "team": row.team,
                    "claims": row[2],
                    "closes": row[3],
                }
                for row in rows
            ],
        }
    )


@teams_blueprint.get("/teams/production")
@require_user
def production_rollup():
    """Closed reviews and recovered opportunity per team per day, 30 days."""
    session = get_session()
    window_start = utc_naive(get_clock().now()) - timedelta(
        days=PRODUCTION_WINDOW_DAYS
    )
    rows = session.execute(
        PRODUCTION_ROLLUP_SQL,
        {
            "closed": InvoiceStatus.CLOSED.value,
            "no_review": InvoiceStatus.NO_REVIEW_NEEDED.value,
            # Bound as the exact string SQLite stores (naive UTC with
            # microseconds), per the UTCDateTime raw-SQL rendering rule.
            "window_start": window_start.isoformat(
                sep=" ", timespec="microseconds"
            ),
        },
    ).all()
    return jsonify(
        {
            "window_days": PRODUCTION_WINDOW_DAYS,
            "production": [
                {
                    "team": row.team,
                    "day": row.day,
                    "closed_count": row.closed_count,
                    "recovered_opportunity": row.recovered_opportunity,
                }
                for row in rows
            ],
        }
    )


def _window_param(name: str) -> datetime:
    """Parse a required aware-ISO datetime query parameter.

    Raises :class:`QueryValidationError`, which the API package maps to
    a 400, so routes can use the value directly.
    """
    raw = request.args.get(name)
    if raw is None:
        raise QueryValidationError(f"Missing required parameter {name!r}")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise QueryValidationError(
            f"{name} is not an ISO datetime: {raw!r}"
        ) from exc
    if moment.tzinfo is None:
        raise QueryValidationError(f"{name} must be timezone-aware")
    return moment
