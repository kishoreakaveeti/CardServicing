"""FinCrime review cases and audit trail store for human-in-the-loop workflows."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

DEFAULT_CASES_DB = Path("data/fincrime_cases.db")


class FinCrimeCase(BaseModel):
    case_id: str
    customer_id: str
    customer_username: str
    account_number: str
    card_id: str
    card_last4: str = ""
    declared_country: str
    is_overseas: bool = True
    policy_id: str = "POL-006"
    policy_version: str = "1.0.0"
    rules_version: str = "1.0.0"
    reason_for_escalation: str = "Overseas replacement requires FinCrime staff approval"
    rag_evidence: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_result: dict[str, Any] = Field(default_factory=dict)
    thread_id: str
    status: str = "PENDING"  # PENDING, APPROVED, REJECTED, COMPLETED, FAILED
    decision_by: str | None = None
    decision_reason: str | None = None
    decided_at: str | None = None
    replacement_card_id: str | None = None
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def current_country(self) -> str:
        return self.declared_country

    @property
    def decided_by(self) -> str | None:
        return self.decision_by

    @property
    def new_card_id(self) -> str | None:
        return self.replacement_card_id


def mask_identifier(val: str | None, keep_end: int = 4) -> str:
    if not val:
        return ""
    val_str = str(val).strip()
    if len(val_str) <= keep_end:
        return "***"
    return "*" * (len(val_str) - keep_end) + val_str[-keep_end:]


def mask_username(val: str | None) -> str:
    if not val:
        return ""
    parts = val.split("@")
    name = parts[0]
    if len(name) <= 2:
        masked = name[0] + "*"
    else:
        masked = name[:2] + "*" * (len(name) - 2)
    if len(parts) > 1:
        return f"{masked}@{parts[1]}"
    return masked


class FinCrimeCaseStore:
    """Persistent SQLite store for FinCrime approval cases and audit records."""

    def __init__(self, db_path: str | Path | None = None):
        self.db_path = str(db_path or os.getenv("FINCRIME_CASES_DB", str(DEFAULT_CASES_DB)))
        self._mem_conn = sqlite3.connect(":memory:", check_same_thread=False) if self.db_path == ":memory:" else None
        if self._mem_conn:
            self._mem_conn.row_factory = sqlite3.Row
        elif self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _get_conn(self) -> sqlite3.Connection:
        if self._mem_conn:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fincrime_cases (
                    case_id TEXT PRIMARY KEY,
                    customer_id TEXT NOT NULL,
                    customer_username TEXT NOT NULL,
                    account_number TEXT NOT NULL,
                    card_id TEXT NOT NULL,
                    card_last4 TEXT NOT NULL,
                    declared_country TEXT NOT NULL,
                    is_overseas INTEGER NOT NULL,
                    policy_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    rules_version TEXT NOT NULL,
                    reason_for_escalation TEXT NOT NULL,
                    rag_evidence TEXT NOT NULL,
                    evaluation_result TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decision_by TEXT,
                    decision_reason TEXT,
                    decided_at TEXT,
                    replacement_card_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fincrime_case_audit (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    event TEXT NOT NULL,
                    performed_by TEXT NOT NULL,
                    details TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS ix_fc_cases_status ON fincrime_cases(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_fc_cases_cust ON fincrime_cases(customer_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS ix_fc_cases_thread ON fincrime_cases(thread_id)")

    def create_case(
        self,
        customer_id: str,
        customer_username: str = "",
        account_number: str = "",
        card_id: str = "",
        card_last4: str = "",
        declared_country: str = "UNKNOWN",
        is_overseas: bool = True,
        policy_id: str = "POL-006",
        policy_version: str = "1.0.0",
        rules_version: str = "1.0.0",
        reason_for_escalation: str = "Overseas replacement requires FinCrime staff approval",
        rag_evidence: list[dict[str, Any]] | None = None,
        evaluation_result: dict[str, Any] | None = None,
        thread_id: str = "",
        **kwargs: Any,
    ) -> FinCrimeCase:
        declared_country = kwargs.get("current_country") or declared_country
        if kwargs.get("reasons"):
            reason_for_escalation = ", ".join(kwargs["reasons"])
        card_last4 = card_last4 or (str(card_id)[-4:] if card_id else "0000")
        thread_id = kwargs.get("conversation_id") or thread_id
        evaluation_result = evaluation_result or kwargs.get("policy_eval") or {}
        rag_evidence = rag_evidence or []

        case_id = f"FC-{uuid4().hex[:8].upper()}"
        now = datetime.now(timezone.utc).isoformat()
        case = FinCrimeCase(
            case_id=case_id,
            customer_id=customer_id,
            customer_username=customer_username,
            account_number=account_number,
            card_id=card_id,
            card_last4=card_last4,
            declared_country=declared_country,
            is_overseas=is_overseas,
            policy_id=policy_id,
            policy_version=policy_version,
            rules_version=rules_version,
            reason_for_escalation=reason_for_escalation,
            rag_evidence=rag_evidence,
            evaluation_result=evaluation_result,
            thread_id=thread_id,
            status="PENDING",
            created_at=now,
            updated_at=now,
        )

        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO fincrime_cases (
                    case_id, customer_id, customer_username, account_number, card_id, card_last4,
                    declared_country, is_overseas, policy_id, policy_version, rules_version,
                    reason_for_escalation, rag_evidence, evaluation_result, thread_id,
                    status, decision_by, decision_reason, decided_at, replacement_card_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    case.case_id,
                    case.customer_id,
                    case.customer_username,
                    case.account_number,
                    case.card_id,
                    case.card_last4,
                    case.declared_country,
                    1 if case.is_overseas else 0,
                    case.policy_id,
                    case.policy_version,
                    case.rules_version,
                    case.reason_for_escalation,
                    json.dumps(case.rag_evidence),
                    json.dumps(case.evaluation_result),
                    case.thread_id,
                    case.status,
                    None,
                    None,
                    None,
                    None,
                    case.created_at,
                    case.updated_at,
                ),
            )
            conn.commit()

        self._record_audit(case_id, "CASE_CREATED", customer_username, f"Case opened for {declared_country} replacement")
        return case

    def get_case(self, case_id: str) -> FinCrimeCase | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM fincrime_cases WHERE case_id=?", (case_id,)).fetchone()
            if not row:
                return None
            return self._row_to_case(row)

    def get_case_by_thread(self, thread_id: str) -> FinCrimeCase | None:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM fincrime_cases WHERE thread_id=? ORDER BY created_at DESC LIMIT 1", (thread_id,)).fetchone()
            if not row:
                return None
            return self._row_to_case(row)

    def list_cases(
        self,
        status: str | None = None,
        country: str | None = None,
        customer_id: str | None = None,
        limit: int = 100,
    ) -> list[FinCrimeCase]:
        query = "SELECT * FROM fincrime_cases WHERE 1=1"
        params: list[Any] = []
        if status:
            query += " AND status=?"
            params.append(status.upper())
        if country:
            query += " AND declared_country=?"
            params.append(country.upper())
        if customer_id:
            query += " AND customer_id=?"
            params.append(customer_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [self._row_to_case(r) for r in rows]

    def list_cases_for_customer(self, customer_id: str) -> list[FinCrimeCase]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fincrime_cases WHERE customer_id=? ORDER BY created_at DESC", (customer_id,)
            ).fetchall()
            return [self._row_to_case(r) for r in rows]

    def approve_case(self, case_id: str, analyst_username: str, reason: str) -> FinCrimeCase:
        if not reason or not reason.strip():
            raise ValueError("Decision reason is mandatory for FinCrime staff approval")

        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM fincrime_cases WHERE case_id=?", (case_id,)).fetchone()
            if not row:
                raise KeyError(f"Case {case_id} not found")
            current_status = row["status"]
            if current_status != "PENDING":
                raise ValueError(f"Case {case_id} is already decided (current status: {current_status})")

            conn.execute(
                """
                UPDATE fincrime_cases
                SET status='APPROVED', decision_by=?, decision_reason=?, decided_at=?, updated_at=?
                WHERE case_id=? AND status='PENDING'
                """,
                (analyst_username, reason.strip(), now, now, case_id),
            )
            conn.commit()

        self._record_audit(case_id, "STAFF_APPROVED", analyst_username, reason)
        case = self.get_case(case_id)
        assert case is not None
        return case

    def reject_case(self, case_id: str, analyst_username: str, reason: str) -> FinCrimeCase:
        if not reason or not reason.strip():
            raise ValueError("Decision reason is mandatory for FinCrime staff rejection")

        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM fincrime_cases WHERE case_id=?", (case_id,)).fetchone()
            if not row:
                raise KeyError(f"Case {case_id} not found")
            current_status = row["status"]
            if current_status != "PENDING":
                raise ValueError(f"Case {case_id} is already decided (current status: {current_status})")

            conn.execute(
                """
                UPDATE fincrime_cases
                SET status='REJECTED', decision_by=?, decision_reason=?, decided_at=?, updated_at=?
                WHERE case_id=? AND status='PENDING'
                """,
                (analyst_username, reason.strip(), now, now, case_id),
            )
            conn.commit()

        self._record_audit(case_id, "STAFF_REJECTED", analyst_username, reason)
        case = self.get_case(case_id)
        assert case is not None
        return case

    def mark_completed(self, case_id: str, replacement_card_id: str) -> FinCrimeCase:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE fincrime_cases
                SET status='COMPLETED', replacement_card_id=?, updated_at=?
                WHERE case_id=?
                """,
                (replacement_card_id, now, case_id),
            )
            conn.commit()
        self._record_audit(case_id, "EXECUTION_COMPLETED", "system", f"Replacement card {replacement_card_id} issued")
        case = self.get_case(case_id)
        assert case is not None
        return case

    def mark_failed(self, case_id: str, error_message: str) -> FinCrimeCase:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                UPDATE fincrime_cases
                SET status='FAILED', updated_at=?
                WHERE case_id=?
                """,
                (now, case_id),
            )
            conn.commit()
        self._record_audit(case_id, "EXECUTION_FAILED", "system", error_message)
        case = self.get_case(case_id)
        assert case is not None
        return case

    def _record_audit(self, case_id: str, event: str, performed_by: str, details: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._get_conn() as conn:
            conn.execute(
                """
                INSERT INTO fincrime_case_audit (id, case_id, event, performed_by, details, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (f"aud-{uuid4().hex[:10]}", case_id, event, performed_by, details, now),
            )
            conn.commit()

    def get_case_audit(self, case_id: str) -> list[dict[str, Any]]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM fincrime_case_audit WHERE case_id=? ORDER BY timestamp ASC", (case_id,)
            ).fetchall()
            return [dict(r) for r in rows]

    def _row_to_case(self, row: sqlite3.Row) -> FinCrimeCase:
        return FinCrimeCase(
            case_id=row["case_id"],
            customer_id=row["customer_id"],
            customer_username=row["customer_username"],
            account_number=row["account_number"],
            card_id=row["card_id"],
            card_last4=row["card_last4"],
            declared_country=row["declared_country"],
            is_overseas=bool(row["is_overseas"]),
            policy_id=row["policy_id"],
            policy_version=row["policy_version"],
            rules_version=row["rules_version"],
            reason_for_escalation=row["reason_for_escalation"],
            rag_evidence=json.loads(row["rag_evidence"]),
            evaluation_result=json.loads(row["evaluation_result"]),
            thread_id=row["thread_id"],
            status=row["status"],
            decision_by=row["decision_by"],
            decision_reason=row["decision_reason"],
            decided_at=row["decided_at"],
            replacement_card_id=row["replacement_card_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
