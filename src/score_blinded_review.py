"""Score exported blinded ratings after the owner explicitly unblinds them."""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import fmean
from typing import Sequence


DIMENSIONS = (
    "diagnostic_usefulness",
    "student_plausibility",
    "teacher_actionability",
)
ISSUE_TYPES = (
    "mathematically_inconsistent",
    "correct_answer_collision",
    "duplicate",
    "nonsense",
)
BOOTSTRAP_SEED = 20260713
BOOTSTRAP_SAMPLES = 10_000


def _candidate_from_csv(row: dict[str, str], prefix: str) -> dict:
    issues_text = str(row.get(f"{prefix}_issues", "")).strip()
    return {
        "diagnostic_usefulness": int(
            row[f"{prefix}_diagnostic_usefulness"]
        ),
        "student_plausibility": int(
            row[f"{prefix}_student_plausibility"]
        ),
        "teacher_actionability": int(
            row[f"{prefix}_teacher_actionability"]
        ),
        "issues": [
            value for value in issues_text.split("|") if value
        ],
    }


def _normalize_payload(value: dict) -> dict:
    ratings = []
    for raw in value.get("ratings", []):
        ratings.append(
            {
                "review_item_id": str(raw.get("review_item_id", "")),
                "preference": str(raw.get("preference", "")),
                "candidate_a": {
                    dimension: raw.get("candidate_a", {}).get(dimension)
                    for dimension in DIMENSIONS
                }
                | {
                    "issues": list(
                        raw.get("candidate_a", {}).get("issues", [])
                    )
                },
                "candidate_b": {
                    dimension: raw.get("candidate_b", {}).get(dimension)
                    for dimension in DIMENSIONS
                }
                | {
                    "issues": list(
                        raw.get("candidate_b", {}).get("issues", [])
                    )
                },
                "note": str(raw.get("note", "")),
            }
        )
    return {
        "schema_version": str(value.get("schema_version", "")),
        "reviewer_code": str(value.get("reviewer_code", "")),
        "ratings": ratings,
    }


def parse_ratings_file(path: str | Path) -> dict:
    """Parse the review tool's JSON or CSV export into one canonical shape."""
    input_path = Path(path)
    if input_path.suffix.casefold() == ".json":
        value = json.loads(input_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected a JSON object: {input_path}")
        return _normalize_payload(value)
    if input_path.suffix.casefold() != ".csv":
        raise ValueError(f"ratings file must end in .json or .csv: {input_path}")
    with input_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"ratings CSV is empty: {input_path}")
    schema_versions = {row.get("schema_version", "") for row in rows}
    reviewer_codes = {row.get("reviewer_code", "") for row in rows}
    if len(schema_versions) != 1 or len(reviewer_codes) != 1:
        raise ValueError("CSV must contain one schema version and reviewer code")
    return {
        "schema_version": schema_versions.pop(),
        "reviewer_code": reviewer_codes.pop(),
        "ratings": [
            {
                "review_item_id": str(row.get("review_item_id", "")),
                "preference": str(row.get("preference", "")),
                "candidate_a": _candidate_from_csv(row, "a"),
                "candidate_b": _candidate_from_csv(row, "b"),
                "note": str(row.get("note", "")),
            }
            for row in rows
        ],
    }


def wilson_interval(
    successes: int,
    total: int,
    *,
    z: float = 1.959963984540054,
) -> list[float] | None:
    if total <= 0:
        return None
    proportion = successes / total
    denominator = 1 + (z * z / total)
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1 - proportion) / total
            + z * z / (4 * total * total)
        )
        / denominator
    )
    return [100 * max(0.0, center - margin), 100 * min(1.0, center + margin)]


def _percentile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile needs at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _bootstrap_item_means(
    values_by_item: dict[str, list[float]],
    *,
    samples: int,
    seed: int,
) -> list[float]:
    item_ids = sorted(values_by_item)
    if not item_ids:
        return []
    item_means = {
        item_id: fmean(values_by_item[item_id]) for item_id in item_ids
    }
    rng = random.Random(seed)
    estimates = []
    for _ in range(samples):
        draw = [rng.choice(item_ids) for _ in item_ids]
        estimates.append(fmean(item_means[item_id] for item_id in draw))
    return estimates


def _bootstrap_summary(
    values_by_item: dict[str, list[float]],
    *,
    samples: int,
    seed: int,
) -> dict:
    observed = fmean(
        value
        for values in values_by_item.values()
        for value in values
    )
    estimates = _bootstrap_item_means(
        values_by_item,
        samples=samples,
        seed=seed,
    )
    return {
        "mean": observed,
        "bootstrap_ci95": [
            _percentile(estimates, 0.025),
            _percentile(estimates, 0.975),
        ],
        "method": "question-cluster bootstrap",
        "bootstrap_samples": samples,
    }


def _validate_key(key: dict) -> tuple[list[str], dict[str, dict]]:
    if key.get("schema_version") != "blinded-review-key-v1":
        raise ValueError("unexpected hidden-key schema")
    rows = key.get("items")
    if not isinstance(rows, list) or not rows:
        raise ValueError("hidden key has no items")
    item_ids = [str(row.get("review_item_id", "")) for row in rows]
    if not all(item_ids) or len(item_ids) != len(set(item_ids)):
        raise ValueError("hidden-key review item IDs must be unique")
    sources = list(key.get("source_labels") or [])
    if not sources:
        sources = sorted(
            {
                str(row.get(field, ""))
                for row in rows
                for field in (
                    "candidate_a_source",
                    "candidate_b_source",
                )
            }
        )
    if len(sources) != 2 or not all(sources):
        raise ValueError("hidden key must identify exactly two sources")
    for row in rows:
        pair = {
            str(row.get("candidate_a_source", "")),
            str(row.get("candidate_b_source", "")),
        }
        if pair != set(sources):
            raise ValueError(
                f"{row.get('review_item_id')} does not map both sources"
            )
    return sources, {item_id: row for item_id, row in zip(item_ids, rows, strict=True)}


def _validate_ratings(
    payload: dict,
    *,
    expected_ids: set[str],
) -> dict[str, dict]:
    if payload.get("schema_version") != "blinded-ratings-v1":
        raise ValueError("unexpected ratings schema")
    reviewer_code = str(payload.get("reviewer_code", "")).strip()
    if not reviewer_code:
        raise ValueError("ratings export has no reviewer code")
    ratings = payload.get("ratings")
    if not isinstance(ratings, list):
        raise ValueError(f"{reviewer_code}: ratings must be a list")
    ids = [str(row.get("review_item_id", "")) for row in ratings]
    if len(ids) != len(set(ids)) or set(ids) != expected_ids:
        raise ValueError(f"{reviewer_code}: item IDs do not match hidden key")
    for row in ratings:
        item_id = str(row["review_item_id"])
        if row.get("preference") not in {"A", "Tie", "B"}:
            raise ValueError(f"{reviewer_code}/{item_id}: invalid preference")
        for candidate in ("candidate_a", "candidate_b"):
            values = row.get(candidate)
            if not isinstance(values, dict):
                raise ValueError(f"{reviewer_code}/{item_id}: missing {candidate}")
            for dimension in DIMENSIONS:
                if values.get(dimension) not in {1, 2, 3, 4, 5}:
                    raise ValueError(
                        f"{reviewer_code}/{item_id}: invalid {candidate} {dimension}"
                    )
            issues = values.get("issues")
            if not isinstance(issues, list) or any(
                issue not in ISSUE_TYPES for issue in issues
            ):
                raise ValueError(
                    f"{reviewer_code}/{item_id}: invalid issue flag"
                )
            if len(issues) != len(set(issues)):
                raise ValueError(
                    f"{reviewer_code}/{item_id}: duplicate issue flag"
                )
    return {
        str(row["review_item_id"]): row for row in ratings
    }


def _system_candidate(
    rating: dict,
    key_row: dict,
    source: str,
) -> dict:
    if key_row["candidate_a_source"] == source:
        return rating["candidate_a"]
    if key_row["candidate_b_source"] == source:
        return rating["candidate_b"]
    raise ValueError(f"source is not mapped for {rating['review_item_id']}")


def _preferred_source(rating: dict, key_row: dict) -> str | None:
    if rating["preference"] == "Tie":
        return None
    field = (
        "candidate_a_source"
        if rating["preference"] == "A"
        else "candidate_b_source"
    )
    return str(key_row[field])


def _fleiss_kappa(category_rows: Sequence[Sequence[str]]) -> float | None:
    if not category_rows:
        return None
    reviewer_counts = {len(row) for row in category_rows}
    if len(reviewer_counts) != 1 or next(iter(reviewer_counts)) < 2:
        return None
    n_raters = next(iter(reviewer_counts))
    categories = sorted({value for row in category_rows for value in row})
    counts = [Counter(row) for row in category_rows]
    observed = fmean(
        sum(count * (count - 1) for count in row.values())
        / (n_raters * (n_raters - 1))
        for row in counts
    )
    total_assignments = len(category_rows) * n_raters
    proportions = {
        category: sum(row[category] for row in counts) / total_assignments
        for category in categories
    }
    expected = sum(value * value for value in proportions.values())
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else None
    return (observed - expected) / (1 - expected)


def _weighted_kappa(
    first: Sequence[int],
    second: Sequence[int],
) -> float | None:
    if len(first) != len(second) or not first:
        return None
    levels = (1, 2, 3, 4, 5)
    n = len(first)
    first_counts = Counter(first)
    second_counts = Counter(second)
    observed_disagreement = fmean(
        ((a - b) / (len(levels) - 1)) ** 2
        for a, b in zip(first, second, strict=True)
    )
    expected_disagreement = sum(
        ((a - b) / (len(levels) - 1)) ** 2
        * (first_counts[a] / n)
        * (second_counts[b] / n)
        for a in levels
        for b in levels
    )
    if math.isclose(expected_disagreement, 0.0):
        return 1.0 if math.isclose(observed_disagreement, 0.0) else None
    return 1 - observed_disagreement / expected_disagreement


def _binary_kappa(
    first: Sequence[bool],
    second: Sequence[bool],
) -> float | None:
    if len(first) != len(second) or not first:
        return None
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / len(first)
    first_yes = sum(first) / len(first)
    second_yes = sum(second) / len(second)
    expected = first_yes * second_yes + (1 - first_yes) * (1 - second_yes)
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else None
    return (observed - expected) / (1 - expected)


def _agreement(
    *,
    reviewer_codes: Sequence[str],
    ratings_by_reviewer: dict[str, dict[str, dict]],
    key_by_item: dict[str, dict],
    sources: Sequence[str],
) -> dict:
    if len(reviewer_codes) < 2:
        return {
            "status": "UNAVAILABLE",
            "reviewers": len(reviewer_codes),
            "note": "At least two independent reviewers are required.",
        }
    item_ids = sorted(key_by_item)
    preference_rows = []
    for item_id in item_ids:
        key_row = key_by_item[item_id]
        categories = []
        for reviewer in reviewer_codes:
            rating = ratings_by_reviewer[reviewer][item_id]
            categories.append(_preferred_source(rating, key_row) or "Tie")
        preference_rows.append(categories)

    ordinal: dict[str, dict[str, dict]] = {}
    issue_any: dict[str, dict] = {}
    reviewer_pairs = list(itertools.combinations(reviewer_codes, 2))
    for source in sources:
        ordinal[source] = {}
        for dimension in DIMENSIONS:
            pair_values = []
            for first, second in reviewer_pairs:
                first_values = [
                    int(
                        _system_candidate(
                            ratings_by_reviewer[first][item_id],
                            key_by_item[item_id],
                            source,
                        )[dimension]
                    )
                    for item_id in item_ids
                ]
                second_values = [
                    int(
                        _system_candidate(
                            ratings_by_reviewer[second][item_id],
                            key_by_item[item_id],
                            source,
                        )[dimension]
                    )
                    for item_id in item_ids
                ]
                value = _weighted_kappa(first_values, second_values)
                if value is not None:
                    pair_values.append(value)
            ordinal[source][dimension] = {
                "mean_pairwise_quadratic_weighted_kappa": (
                    fmean(pair_values) if pair_values else None
                ),
                "reviewer_pairs": len(pair_values),
            }
        issue_values = []
        for first, second in reviewer_pairs:
            first_flags = [
                bool(
                    _system_candidate(
                        ratings_by_reviewer[first][item_id],
                        key_by_item[item_id],
                        source,
                    )["issues"]
                )
                for item_id in item_ids
            ]
            second_flags = [
                bool(
                    _system_candidate(
                        ratings_by_reviewer[second][item_id],
                        key_by_item[item_id],
                        source,
                    )["issues"]
                )
                for item_id in item_ids
            ]
            value = _binary_kappa(first_flags, second_flags)
            if value is not None:
                issue_values.append(value)
        issue_any[source] = {
            "mean_pairwise_cohen_kappa": (
                fmean(issue_values) if issue_values else None
            ),
            "reviewer_pairs": len(issue_values),
        }
    return {
        "status": "MEASURED",
        "reviewers": len(reviewer_codes),
        "preference_fleiss_kappa": _fleiss_kappa(preference_rows),
        "ordinal_weighted_kappa": ordinal,
        "any_issue_cohen_kappa": issue_any,
        "interpretation_note": (
            "Agreement statistics are descriptive on this small sample; "
            "adjudication should inspect disagreements item by item."
        ),
    }


def score_reviews(
    rating_payloads: Sequence[dict],
    hidden_key: dict,
    *,
    bootstrap_samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> dict:
    """Unblind complete ratings and compute paired descriptive statistics."""
    if not rating_payloads:
        raise ValueError("at least one ratings export is required")
    sources, key_by_item = _validate_key(hidden_key)
    expected_ids = set(key_by_item)
    reviewer_codes = [
        str(payload.get("reviewer_code", "")).strip()
        for payload in rating_payloads
    ]
    if len(reviewer_codes) != len(set(reviewer_codes)):
        raise ValueError("reviewer codes must be unique across exports")
    ratings_by_reviewer = {
        reviewer: _validate_ratings(payload, expected_ids=expected_ids)
        for reviewer, payload in zip(
            reviewer_codes,
            rating_payloads,
            strict=True,
        )
    }

    preference_counts = {
        source: Counter(wins=0, losses=0, ties=0) for source in sources
    }
    rating_values: dict[str, dict[str, list[float]]] = {
        source: {dimension: [] for dimension in DIMENSIONS}
        for source in sources
    }
    issue_counts: dict[str, Counter] = {
        source: Counter() for source in sources
    }
    differences: dict[str, dict[str, list[float]]] = {
        dimension: defaultdict(list) for dimension in DIMENSIONS
    }
    preference_score_differences: dict[str, list[float]] = defaultdict(list)
    first_source, second_source = sources

    for reviewer in reviewer_codes:
        for item_id in sorted(expected_ids):
            rating = ratings_by_reviewer[reviewer][item_id]
            key_row = key_by_item[item_id]
            preferred = _preferred_source(rating, key_row)
            for source in sources:
                if preferred is None:
                    preference_counts[source]["ties"] += 1
                elif preferred == source:
                    preference_counts[source]["wins"] += 1
                else:
                    preference_counts[source]["losses"] += 1
                candidate = _system_candidate(rating, key_row, source)
                for dimension in DIMENSIONS:
                    rating_values[source][dimension].append(
                        float(candidate[dimension])
                    )
                if candidate["issues"]:
                    issue_counts[source]["any"] += 1
                for issue in candidate["issues"]:
                    issue_counts[source][issue] += 1

            first_candidate = _system_candidate(
                rating,
                key_row,
                first_source,
            )
            second_candidate = _system_candidate(
                rating,
                key_row,
                second_source,
            )
            for dimension in DIMENSIONS:
                differences[dimension][item_id].append(
                    float(first_candidate[dimension])
                    - float(second_candidate[dimension])
                )
            first_preference_score = (
                0.5
                if preferred is None
                else (1.0 if preferred == first_source else 0.0)
            )
            preference_score_differences[item_id].append(
                2 * first_preference_score - 1
            )

    total_ratings = len(reviewer_codes) * len(expected_ids)
    systems = {}
    for source in sources:
        counts = preference_counts[source]
        decisive = counts["wins"] + counts["losses"]
        systems[source] = {
            "preference": {
                "wins": counts["wins"],
                "ties": counts["ties"],
                "losses": counts["losses"],
                "total": total_ratings,
                "win_rate_pct": 100 * counts["wins"] / total_ratings,
                "win_rate_wilson_ci95": wilson_interval(
                    counts["wins"],
                    total_ratings,
                ),
                "tie_rate_pct": 100 * counts["ties"] / total_ratings,
                "tie_rate_wilson_ci95": wilson_interval(
                    counts["ties"],
                    total_ratings,
                ),
                "loss_rate_pct": 100 * counts["losses"] / total_ratings,
                "loss_rate_wilson_ci95": wilson_interval(
                    counts["losses"],
                    total_ratings,
                ),
                "decisive_win_rate_pct": (
                    100 * counts["wins"] / decisive if decisive else None
                ),
                "decisive_win_rate_wilson_ci95": wilson_interval(
                    counts["wins"],
                    decisive,
                ),
            },
            "ratings": {
                dimension: {
                    "mean": fmean(rating_values[source][dimension]),
                    "n": len(rating_values[source][dimension]),
                    "scale": "1-5",
                }
                for dimension in DIMENSIONS
            },
            "issues": {
                issue: {
                    "count": issue_counts[source][issue],
                    "rate_pct": (
                        100 * issue_counts[source][issue] / total_ratings
                    ),
                    "wilson_ci95": wilson_interval(
                        issue_counts[source][issue],
                        total_ratings,
                    ),
                }
                for issue in ("any", *ISSUE_TYPES)
            },
        }

    paired_differences = {}
    for index, dimension in enumerate(DIMENSIONS):
        summary = _bootstrap_summary(
            differences[dimension],
            samples=bootstrap_samples,
            seed=seed + index,
        )
        paired_differences[dimension] = {
            "contrast": f"{first_source} minus {second_source}",
            "mean_difference": summary["mean"],
            "bootstrap_ci95": summary["bootstrap_ci95"],
            "method": summary["method"],
            "bootstrap_samples": summary["bootstrap_samples"],
        }
    preference_summary = _bootstrap_summary(
        preference_score_differences,
        samples=bootstrap_samples,
        seed=seed + 10,
    )

    agreement = _agreement(
        reviewer_codes=reviewer_codes,
        ratings_by_reviewer=ratings_by_reviewer,
        key_by_item=key_by_item,
        sources=sources,
    )
    unavailable_note = (
        "This set-level rubric does not score every emitted distractor against "
        "all registered GDR gates. Issue flags are candidate-level and cannot "
        "be converted into pair-level all-gates pass/fail judgments."
    )
    return {
        "schema_version": "unblinded-human-review-results-v1",
        "design": {
            "items": len(expected_ids),
            "reviewers": len(reviewer_codes),
            "reviewer_codes": reviewer_codes,
            "source_order": sources,
            "bootstrap_samples": bootstrap_samples,
            "bootstrap_seed": seed,
        },
        "systems": systems,
        "preference_contrast": {
            "contrast": f"{first_source} minus {second_source}",
            "mean_net_preference": preference_summary["mean"],
            "bootstrap_ci95": preference_summary["bootstrap_ci95"],
            "scale": "-1 = all prefer second; 0 = balanced/tied; +1 = all prefer first",
            "method": preference_summary["method"],
        },
        "paired_differences": paired_differences,
        "human_gdr_proxy": {
            "status": "UNAVAILABLE",
            "note": unavailable_note,
        },
        "good_at_3_proxy": {
            "status": "UNAVAILABLE",
            "note": unavailable_note,
        },
        "inter_rater_agreement": agreement,
        "claim_limit": (
            "One reviewer is exploratory. A publishable comparative claim "
            "requires at least two independent reviewers, agreement reporting, "
            "and adjudication of disagreements."
        ),
    }


def render_markdown(result: dict) -> str:
    sources = result["design"]["source_order"]
    lines = [
        "# Unblinded human review results",
        "",
        f"- Items: {result['design']['items']}",
        f"- Reviewers: {result['design']['reviewers']}",
        "",
        "## Preference",
        "",
    ]
    for source in sources:
        preference = result["systems"][source]["preference"]
        lines.append(
            f"- **{source}:** {preference['wins']} wins, "
            f"{preference['ties']} ties, {preference['losses']} losses; "
            f"win rate {preference['win_rate_pct']:.1f}%."
        )
    lines.extend(["", "## Mean 1–5 ratings", ""])
    for source in sources:
        ratings = result["systems"][source]["ratings"]
        formatted = ", ".join(
            f"{name.replace('_', ' ')} {ratings[name]['mean']:.2f}"
            for name in DIMENSIONS
        )
        lines.append(f"- **{source}:** {formatted}.")
    lines.extend(
        [
            "",
            "## Registered proxy status",
            "",
            "- GDR human proxy: **UNAVAILABLE** from this set-level rubric.",
            "- Good@3 proxy: **UNAVAILABLE** from this set-level rubric.",
            "",
            result["claim_limit"],
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ratings", nargs="+", help="blinded JSON/CSV exports")
    parser.add_argument(
        "--key",
        required=True,
        help="owner-only hidden key",
    )
    parser.add_argument(
        "--confirm-review-complete",
        action="store_true",
        help="required acknowledgement before the key is read",
    )
    parser.add_argument(
        "--out",
        default="data/eval_out/human_review_results.json",
    )
    parser.add_argument(
        "--markdown-out",
        default="data/eval_out/human_review_results.md",
    )
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=BOOTSTRAP_SAMPLES,
    )
    args = parser.parse_args()
    if not args.confirm_review_complete:
        raise SystemExit(
            "Refusing to unblind. Re-run with --confirm-review-complete only "
            "after all reviewers have returned final exports."
        )
    key = json.loads(Path(args.key).read_text(encoding="utf-8"))
    payloads = [parse_ratings_file(path) for path in args.ratings]
    result = score_reviews(
        payloads,
        key,
        bootstrap_samples=args.bootstrap_samples,
    )
    output = Path(args.out)
    markdown_output = Path(args.markdown_out)
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    markdown_output.write_text(render_markdown(result), encoding="utf-8")
    print(f"wrote unblinded results -> {output}")
    print(f"wrote readable summary -> {markdown_output}")


if __name__ == "__main__":
    main()
