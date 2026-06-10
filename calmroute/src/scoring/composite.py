"""Composite safety score.

    safety = 100 - (w_crash*crash_norm + w_ice*ice_norm + w_constr*constr_norm)

Each layer's raw score is normalized 0-100 across the candidate set
(max-scaled: the worst candidate on a layer defines 100 for that layer).
When the winter layer is inactive its weight redistributes to crash, so
a dry summer day is scored almost entirely on crash history.
"""

from dataclasses import dataclass

from .. import config


@dataclass
class ScoredLayers:
    crash_raw: float
    ice_raw: float
    construction_raw: float


def effective_weights(winter_active: bool) -> dict[str, float]:
    w = dict(config.WEIGHTS)
    if not winter_active:
        w["crash"] += w["ice"]
        w["ice"] = 0.0
    return w


def normalize(raws: list[float]) -> list[float]:
    """Max-scale a layer's raw scores to 0-100 across the candidate set."""
    peak = max(raws, default=0.0)
    if peak <= 0:
        return [0.0 for _ in raws]
    return [100.0 * r / peak for r in raws]


def composite_scores(layers: list[ScoredLayers], winter_active: bool) -> list[dict]:
    """Safety score + per-layer subscores for each candidate, same order."""
    weights = effective_weights(winter_active)
    crash_norm = normalize([l.crash_raw for l in layers])
    ice_norm = normalize([l.ice_raw for l in layers]) if winter_active else [0.0] * len(layers)
    constr_norm = normalize([l.construction_raw for l in layers])

    results = []
    for c, i, k in zip(crash_norm, ice_norm, constr_norm):
        penalty = weights["crash"] * c + weights["ice"] * i + weights["construction"] * k
        results.append(
            {
                "safety_score": round(100.0 - penalty, 1),
                "subscores": {
                    "crash": round(c, 1),
                    "ice": round(i, 1),
                    "construction": round(k, 1),
                },
            }
        )
    return results
