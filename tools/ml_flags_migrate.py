#!/usr/bin/env python3
"""
tools/ml_flags_migrate.py

One-off migration script to:

  - Drop legacy `ml_flags.viral_v1`
  - Normalise / create the new `ml_flags.viral_v2` schema:

      ml_flags.viral_v2 = {
          model_version: 1,
          label_rule_version: 1,

          # stage-level snapshots (may be null until evaluated)
          h6: {
              score_proba:      float | None,   # model_6h probability 0–1
              score_100:        int   | None,   # score_proba * 100
              is_candidate:     bool  | None,   # score_100 >= threshold_100_6h ?
              threshold_proba:  float | None,   # decision threshold 0–1
              threshold_100:    int   | None,   # threshold_proba * 100
              evaluated_at:     datetime | None,
          },
          h12: {
              score_proba:      float | None,
              score_100:        int   | None,
              is_viral_12h:     bool  | None,   # score_100 >= threshold_100_12h ?
              threshold_proba:  float | None,
              threshold_100:    int   | None,
              evaluated_at:     datetime | None,
          },
          h24_validation: {
              score_proba:      float | None,   # model_24h probability 0–1
              score_100:        int   | None,
              evaluated_at:     datetime | None,
          },

          final: {
              status:          "unknown" | "non_viral" | "candidate" | "viral",
              decided_stage:   "6h" | "12h" | "24h" | None,
              score_proba:     float | None,
              score_100:       int   | None,
              threshold_proba: float | None,
              threshold_100:   int   | None,
              decided_at:      datetime | None,
              reason:          str | None,
          }
      }

Migration is **idempotent**:
  - Preserves any other fields inside `ml_flags` (low_quality, …)
  - Merges with existing `ml_flags.viral_v2` if present
  - Always removes `ml_flags.viral_v1`
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Dict, Optional, Iterable, Tuple

from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection


# ---------------------------------------------------------------------------
# Helpers to build the new viral_v2 structure
# ---------------------------------------------------------------------------

def _merge_stage(existing: Optional[Dict[str, Any]],
                 template: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge an existing stage sub-document with a template.

    Any missing keys in `existing` will be filled from `template`,
    existing keys are preserved.
    """
    out: Dict[str, Any] = dict(existing or {})
    for k, v in template.items():
        out.setdefault(k, v)
    return out


STAGE_H6_TEMPLATE: Dict[str, Any] = {
    "score_proba": None,
    "score_100": None,
    "is_candidate": None,
    "threshold_proba": None,
    "threshold_100": None,
    "evaluated_at": None,
}

STAGE_H12_TEMPLATE: Dict[str, Any] = {
    "score_proba": None,
    "score_100": None,
    "is_viral_12h": None,
    "threshold_proba": None,
    "threshold_100": None,
    "evaluated_at": None,
}

STAGE_H24_TEMPLATE: Dict[str, Any] = {
    "score_proba": None,
    "score_100": None,
    "evaluated_at": None,
}

FINAL_TEMPLATE: Dict[str, Any] = {
    "status": "unknown",
    "decided_stage": None,
    "score_proba": None,
    "score_100": None,
    "threshold_proba": None,
    "threshold_100": None,
    "decided_at": None,
    "reason": None,
}


def _auto_fill_threshold_100(stage: Dict[str, Any]) -> None:
    """
    If a stage has threshold_proba but missing threshold_100,
    auto-compute threshold_100 = round(threshold_proba * 100).
    """
    th_p = stage.get("threshold_proba")
    th_100 = stage.get("threshold_100")
    if th_p is not None and th_100 is None:
        try:
            stage["threshold_100"] = int(round(float(th_p) * 100))
        except (TypeError, ValueError):
            # keep None if cannot parse
            pass


def migrate_one(doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Compute the update document for a single Mongo record.

    - Removes `ml_flags.viral_v1` if present
    - Ensures `ml_flags.viral_v2` follows the new schema

    Returns:
        None  -> no change needed
        dict  -> update spec for update_one / bulk_write
    """
    ml: Dict[str, Any] = dict(doc.get("ml_flags") or {})

    # Drop legacy viral_v1
    had_v1 = "viral_v1" in ml
    if had_v1:
        ml.pop("viral_v1", None)

    v2_in: Dict[str, Any] = dict(ml.get("viral_v2") or {})

    v2_out: Dict[str, Any] = {}
    v2_out["model_version"] = int(v2_in.get("model_version") or 1)
    v2_out["label_rule_version"] = int(v2_in.get("label_rule_version") or 1)

    # Stage sub-docs
    h6 = _merge_stage(v2_in.get("h6"), STAGE_H6_TEMPLATE)
    _auto_fill_threshold_100(h6)

    h12 = _merge_stage(v2_in.get("h12"), STAGE_H12_TEMPLATE)
    _auto_fill_threshold_100(h12)

    h24 = _merge_stage(v2_in.get("h24_validation"), STAGE_H24_TEMPLATE)

    v2_out["h6"] = h6
    v2_out["h12"] = h12
    v2_out["h24_validation"] = h24

    # Final decision block
    final = _merge_stage(v2_in.get("final"), FINAL_TEMPLATE)
    _auto_fill_threshold_100(final)
    v2_out["final"] = final

    ml["viral_v2"] = v2_out

    original_ml = doc.get("ml_flags") or {}
    if not had_v1 and original_ml == ml:
        # Nothing actually changed
        return None

    return {"$set": {"ml_flags": ml}}


# ---------------------------------------------------------------------------
# Bulk runner
# ---------------------------------------------------------------------------

def iter_target_docs(coll: Collection,
                     limit: Optional[int] = None) -> Iterable[Dict[str, Any]]:
    """
    Iterate through candidate docs; we only need `_id` and `ml_flags`.
    """
    cur = coll.find({}, projection={"_id": 1, "ml_flags": 1})
    if limit is not None:
        cur = cur.limit(int(limit))
    return cur


def run_migration(
    coll: Collection,
    batch_size: int = 1000,
    limit: Optional[int] = None,
    dry_run: bool = False,
    verbose: bool = False,
) -> Tuple[int, int]:
    """
    Run migration over the collection.

    Returns:
        (matched_count, modified_count)
    """
    matched = 0
    modified_total = 0
    ops: list[UpdateOne] = []

    for doc in iter_target_docs(coll, limit=limit):
        upd = migrate_one(doc)
        if upd is None:
            continue

        matched += 1
        if verbose:
            print(f"[DEBUG] _id={doc['_id']} -> {upd}")

        if dry_run:
            continue

        ops.append(UpdateOne({"_id": doc["_id"]}, upd))
        if len(ops) >= batch_size:
            res = coll.bulk_write(ops, ordered=False)
            modified_total += res.modified_count
            if verbose:
                print(f"[DEBUG] bulk matched+={len(ops)}, modified+={res.modified_count}")
            ops = []

    # flush tail
    if ops and not dry_run:
        res = coll.bulk_write(ops, ordered=False)
        modified_total += res.modified_count
        if verbose:
            print(f"[DEBUG] final bulk matched+={len(ops)}, modified+={res.modified_count}")

    return matched, modified_total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Migrate videos.ml_flags to new viral_v2 schema (and drop viral_v1)."
    )

    ap.add_argument(
        "--mongo-uri",
        dest="mongo_uri",
        default=os.getenv("MONGO_URI", "mongodb://localhost:27017/ytscan"),
        help=(
            "Mongo connection string (may include /db). "
            "Default: env MONGO_URI or mongodb://localhost:27017/ytscan"
        ),
    )
    ap.add_argument(
        "--db",
        dest="db",
        default=os.getenv("MONGO_DB", "ytscan"),
        help="Database name. Default: env MONGO_DB or 'ytscan'.",
    )
    ap.add_argument(
        "--coll",
        dest="coll",
        default=os.getenv("MONGO_COLL", "videos"),
        help="Collection name. Default: env MONGO_COLL or 'videos'.",
    )
    ap.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Bulk write batch size (default: 1000).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of documents to scan (debug).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write anything, only print stats.",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Verbose debug logging.",
    )
    return ap


def main() -> int:
    ap = build_arg_parser()
    args = ap.parse_args()

    print("[INFO] Connecting to MongoDB...")
    client = MongoClient(args.mongo_uri)
    db = client[args.db]
    coll = db[args.coll]

    print(f"[INFO] DB={db.name}  coll={coll.name}")
    print(f"[INFO] dry_run={args.dry_run}  batch_size={args.batch_size}  limit={args.limit}")

    matched, modified = run_migration(
        coll,
        batch_size=args.batch_size,
        limit=args.limit,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    print(f"[DONE] Matched (to update): {matched:,}")
    if args.dry_run:
        print("[DONE] DRY RUN — no modifications written.")
    else:
        print(f"[DONE] Modified: {modified:,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
