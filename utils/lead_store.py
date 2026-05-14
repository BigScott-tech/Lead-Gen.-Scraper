"""SQLite persistence for deduplication across runs."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List


class LeadStore:
    def __init__(self, db_path: str = "data/leads.sqlite3"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self):
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS leads (
                    fingerprint TEXT PRIMARY KEY,
                    email TEXT,
                    phone TEXT,
                    social_handle TEXT,
                    source_platform TEXT,
                    source_url TEXT,
                    lead_score INTEGER DEFAULT 0,
                    payload TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    seen_count INTEGER NOT NULL DEFAULT 1
                )
                """
            )

    def filter_new(self, leads: Iterable[Dict]) -> List[Dict]:
        new_leads: List[Dict] = []
        with self._connect() as conn:
            for lead in leads:
                fingerprint = self.fingerprint(lead)
                exists = conn.execute(
                    "SELECT 1 FROM leads WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if exists:
                    conn.execute(
                        """
                        UPDATE leads
                           SET last_seen = ?, seen_count = seen_count + 1
                         WHERE fingerprint = ?
                        """,
                        (datetime.now().isoformat(), fingerprint),
                    )
                    continue
                new_leads.append(lead)
                self._insert(conn, fingerprint, lead)
        return new_leads

    def save_many(self, leads: Iterable[Dict]) -> None:
        with self._connect() as conn:
            for lead in leads:
                fingerprint = self.fingerprint(lead)
                existing = conn.execute(
                    "SELECT 1 FROM leads WHERE fingerprint = ?",
                    (fingerprint,),
                ).fetchone()
                if existing:
                    continue
                self._insert(conn, fingerprint, lead)

    def recent(self, limit: int = 50) -> List[Dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM leads ORDER BY first_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def _insert(self, conn, fingerprint: str, lead: Dict) -> None:
        now = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO leads (
                fingerprint, email, phone, social_handle, source_platform,
                source_url, lead_score, payload, first_seen, last_seen
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fingerprint,
                lead.get("email", ""),
                lead.get("phone", ""),
                lead.get("social_handle", ""),
                lead.get("source_platform", ""),
                lead.get("source_url", ""),
                int(lead.get("lead_score") or 0),
                json.dumps(lead, ensure_ascii=False),
                now,
                now,
            ),
        )

    @staticmethod
    def fingerprint(lead: Dict) -> str:
        parts = [
            lead.get("email", "").lower().strip(),
            lead.get("phone", "").strip(),
            lead.get("social_handle", "").lower().strip(),
            lead.get("source_url", "").lower().strip(),
        ]
        raw = "|".join(part for part in parts if part) or json.dumps(lead, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
