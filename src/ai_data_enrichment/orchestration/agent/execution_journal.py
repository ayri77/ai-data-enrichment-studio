"""SQLite journal for safely claiming and recovering whole-job execution."""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Literal

from ai_data_enrichment.execution.job_models import JobExecutionResult

ClaimResult = Literal["claimed", "started", "finished"]


class ExecutionJournal:
    """Persist execution boundaries independently of LangGraph checkpoints."""

    def __init__(self, path: Path) -> None:
        self.path = path

        # The parent directory must be provisioned by the caller.
        with closing(sqlite3.connect(self.path)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    request_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('started', 'finished')),
                    result_json TEXT,
                    CHECK (
                        (status = 'started' AND result_json IS NULL)
                        OR
                        (status = 'finished' AND result_json IS NOT NULL)
                    )
                )
                """
            )
            connection.commit()

    def claim(
        self,
        *,
        execution_id: str,
        request_fingerprint: str,
    ) -> ClaimResult:
        """Atomically claim a new execution or identify an existing one."""

        if not execution_id.strip() or not request_fingerprint.strip():
            raise ValueError("Execution identity and fingerprint must not be blank.")

        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute("BEGIN IMMEDIATE")

            row = connection.execute(
                """
                SELECT request_fingerprint, status
                FROM executions
                WHERE execution_id = ?
                """,
                (execution_id,),
            ).fetchone()

            if row is not None:
                previous_fingerprint, status = row

                if previous_fingerprint != request_fingerprint:
                    raise ValueError("Execution ID is already bound to a different request.")

                connection.commit()
                return status

            connection.execute(
                """
                INSERT INTO executions (
                    execution_id,
                    request_fingerprint,
                    status
                )
                VALUES (?, ?, 'started')
                """,
                (execution_id, request_fingerprint),
            )

            connection.commit()
            return "claimed"

    def finish(
        self,
        *,
        execution_id: str,
        result: JobExecutionResult,
    ) -> None:
        """Atomically persist the final result of a claimed execution."""

        payload = result.model_dump_json()

        with closing(sqlite3.connect(self.path, timeout=10)) as connection:
            connection.execute("BEGIN IMMEDIATE")

            cursor = connection.execute(
                """
                UPDATE executions
                SET status = 'finished',
                    result_json = ?
                WHERE execution_id = ?
                  AND status = 'started'
                """,
                (payload, execution_id),
            )

            if cursor.rowcount != 1:
                raise ValueError("Execution is not in a finishable state.")

            connection.commit()

    def load_result(self, *, execution_id: str) -> JobExecutionResult | None:
        """Read an already persisted result without invoking the executor."""

        with closing(sqlite3.connect(self.path)) as connection:
            row = connection.execute(
                """
                SELECT result_json
                FROM executions
                WHERE execution_id = ?
                  AND status = 'finished'
                """,
                (execution_id,),
            ).fetchone()

        if row is None:
            return None

        return JobExecutionResult.model_validate_json(row[0])
