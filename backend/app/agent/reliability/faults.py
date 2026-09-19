"""Seeded, replayable fault schedule and injection primitives."""

from __future__ import annotations

from collections import defaultdict

from app.agent.reliability.models import FaultKind, FaultRecord, TrialManifest


class InjectedFault(RuntimeError):
    def __init__(self, record: FaultRecord) -> None:
        super().__init__(f"Injected {record.kind.value} at {record.target}")
        self.record = record


class FaultController:
    """按 kind/target 暴露已调度 Fault，并用 occurrence 限制注入次数。"""

    def __init__(self, manifest: TrialManifest) -> None:
        self.manifest = manifest
        self._hits: defaultdict[str, int] = defaultdict(int)
        self._records: dict[str, FaultRecord] = {}

    def hit(self, kind: FaultKind, target: str) -> FaultRecord | None:
        scheduled = next(
            (
                item.spec
                for item in self.manifest.faults
                if item.scheduled
                and item.spec.kind == kind
                and item.spec.target == target
            ),
            None,
        )
        if scheduled is None:
            return None
        self._hits[scheduled.id] += 1
        occurrence = self._hits[scheduled.id]
        if occurrence > scheduled.occurrence:
            return None
        record = FaultRecord(
            fault_id=scheduled.id,
            kind=scheduled.kind,
            target=scheduled.target,
            injected=True,
            recovered=False,
            occurrence=occurrence,
        )
        self._records[scheduled.id] = record
        raise InjectedFault(record)

    def recover(self, fault_id: str) -> None:
        record = self._records.get(fault_id)
        if record is not None:
            self._records[fault_id] = record.model_copy(update={"recovered": True})

    def block(self, kind: FaultKind, target: str) -> FaultRecord | None:
        """把 Safety Fault 记录为已拦截，不让异常穿透到 Executor。"""

        try:
            self.hit(kind, target)
        except InjectedFault as error:
            self.recover(error.record.fault_id)
            return self._records[error.record.fault_id]
        return None

    def records(self) -> list[FaultRecord]:
        return list(self._records.values())
