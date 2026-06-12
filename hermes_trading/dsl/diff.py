"""Structural diff between two DSL strategies, for the one_block_only limit.

goal.yaml's one_block_only (successor of one_variable_only) allows at most ONE
condition added OR removed OR modified per mutation, computed on canonical
JSON of the entry+exit groups. A logic flip (AND<->OR) counts as one
structural change too: it can invert a whole group's behaviour."""

from __future__ import annotations

import json
from collections import Counter


def canonical_condition(condition: dict) -> str:
    return json.dumps(condition, sort_keys=True, separators=(",", ":"))


def group_changes(old: dict, new: dict) -> int:
    old_counts = Counter(canonical_condition(c) for c in old.get("conditions", []))
    new_counts = Counter(canonical_condition(c) for c in new.get("conditions", []))
    added = sum((new_counts - old_counts).values())
    removed = sum((old_counts - new_counts).values())
    # One removed + one added pair is a single modified condition.
    changes = max(added, removed)
    if old.get("logic") != new.get("logic"):
        changes += 1
    return changes


def structural_diff(old_entry: dict, old_exit: dict, new_entry: dict, new_exit: dict) -> int:
    """Number of structural changes across both mutable groups."""
    return group_changes(old_entry, new_entry) + group_changes(old_exit, new_exit)
