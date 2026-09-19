"""Pure, deterministic lineup rules and performance normalization."""

from .engine import (
    MetricPerformance,
    PeerStatistic,
    Ruleset,
    RulesEvaluation,
    anti_air_rule,
    backup_depth_rule,
    br_cohesion_rule,
    evaluate_lineup_rules,
    infer_roles,
    load_ruleset,
    performance_metric,
    role_coverage_rule,
    role_redundancy_rule,
    scouting_rule,
    statistical_strength_rule,
    uptier_resilience_rule,
)

__all__ = [
    "MetricPerformance",
    "PeerStatistic",
    "RulesEvaluation",
    "Ruleset",
    "anti_air_rule",
    "backup_depth_rule",
    "br_cohesion_rule",
    "evaluate_lineup_rules",
    "infer_roles",
    "load_ruleset",
    "performance_metric",
    "role_coverage_rule",
    "role_redundancy_rule",
    "scouting_rule",
    "statistical_strength_rule",
    "uptier_resilience_rule",
]
