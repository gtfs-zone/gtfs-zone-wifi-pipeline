"""Raw sessions from the wifi-raw bucket, validated against the pinned schemas."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import obstore
import yaml
from jsonschema import Draft202012Validator

if TYPE_CHECKING:
    from obstore.store import ObjectStore

SCHEMA_DIR = Path(__file__).parent / "schemas"
RAW_PREFIX = "raw/"
MAX_ERRORS = 5
_NAME = re.compile(r"^session\.v(\d+)\.json$")

type Session = dict[str, Any]


def validators(schema_dir: Path = SCHEMA_DIR) -> dict[int, Draft202012Validator]:
    """A validator per `session.v<N>.json`, keyed by N."""
    out = {}
    for path in sorted(schema_dir.glob("session.v*.json")):
        if m := _NAME.match(path.name):
            schema = json.loads(path.read_text())
            out[int(m.group(1))] = Draft202012Validator(
                schema, format_checker=Draft202012Validator.FORMAT_CHECKER
            )
    return out


def errors(checks: dict[int, Draft202012Validator], session: object) -> list[str]:
    """Up to MAX_ERRORS validation messages; empty when valid."""
    if not isinstance(session, dict):
        return ["$: not a JSON object"]
    version = session.get("schema_version")
    check = checks.get(version) if type(version) is int else None
    if check is None:
        return [f"$.schema_version: no schema for {version!r}"]
    found = sorted(check.iter_errors(session), key=lambda e: e.json_path)
    return [f"{e.json_path}: {e.message}" for e in found[:MAX_ERRORS]]


def load_excluded(path: Path) -> set[str]:
    """Session ids listed under `excluded` in the YAML file."""
    doc = yaml.safe_load(path.read_text()) or {}
    return {str(entry["id"]) for entry in doc.get("excluded") or []}


@dataclass
class RawSessions:
    sessions: list[Session] = field(default_factory=list)
    invalid: list[dict[str, Any]] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)


def read_raw(store: ObjectStore, excluded: set[str]) -> RawSessions:
    """Every valid, not excluded session under raw/, sorted by session_id.

    Invalid objects go into the report rather than failing the run. A
    session_id seen twice keeps its first key in key order.
    """
    checks = validators()
    out = RawSessions()
    seen: set[str] = set()
    keys = sorted(
        meta["path"]
        for batch in obstore.list(store, prefix=RAW_PREFIX)
        for meta in batch
        if meta["path"].endswith(".json")
    )
    for key in keys:
        try:
            session = json.loads(bytes(obstore.get(store, key).bytes()))
        except ValueError as exc:
            out.invalid.append({"key": key, "errors": [f"not JSON: {exc}"]})
            continue
        if problems := errors(checks, session):
            out.invalid.append({"key": key, "errors": problems})
            continue
        sid = session["session_id"]
        if sid in excluded:
            out.excluded.append(sid)
        elif sid in seen:
            out.duplicates.append(key)
        else:
            seen.add(sid)
            out.sessions.append(session)
    out.sessions.sort(key=lambda s: s["session_id"])
    return out
