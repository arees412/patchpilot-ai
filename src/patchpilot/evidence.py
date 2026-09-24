"""Sanitized audit events and content-addressed evidence bundles."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from patchpilot.models import AuditEvent, EvidenceRecord
from patchpilot.sandbox import redact_output

SENSITIVE_KEYS = {"token", "password", "secret", "authorization", "credential", "api_key"}


def sanitize(value: Any) -> Any:
    """Recursively remove sensitive keyed values and redact strings."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                "<redacted>"
                if any(term in str(key).lower() for term in SENSITIVE_KEYS)
                else sanitize(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        return redact_output(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize(str(value))


def canonical_json(value: Any) -> str:
    return json.dumps(sanitize(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class AuditTrail:
    """Create sanitized structured events for append-only persistence."""

    def event(self, run_id: str, event_type: str, data: Mapping[str, Any]) -> AuditEvent:
        return AuditEvent(run_id=run_id, event_type=event_type, data=sanitize(data))


class EvidenceBuilder:
    """Build records whose digest covers their canonical sanitized payload."""

    def record(self, kind: str, data: Mapping[str, Any]) -> EvidenceRecord:
        sanitized = sanitize(data)
        return EvidenceRecord(kind=kind, data=sanitized, sha256=digest(sanitized))

    def bundle(
        self,
        records: Sequence[EvidenceRecord],
        *,
        output: Path | None = None,
    ) -> dict[str, Any]:
        manifest = {
            "schema_version": 1,
            "records": [record.model_dump(mode="json") for record in records],
            "record_hashes": [record.sha256 for record in records],
        }
        bundle = {**manifest, "bundle_sha256": digest(manifest)}
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return bundle
