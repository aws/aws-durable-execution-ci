"""Standalone fixture for manual AI-review testing. Do not merge.

No workflow imports or executes this module.
"""


DEFAULT_MAX_FINDINGS = 20


def select_review_findings(
    findings: list[str],
    max_findings: int | None = None,
) -> list[str]:
    """Select findings in their original order.

    None uses the default limit. Zero disables inline findings.
    Negative limits raise ValueError.
    """
    if max_findings is not None and max_findings < 0:
        raise ValueError("max_findings must be nonnegative")
    limit = max_findings or DEFAULT_MAX_FINDINGS
    return findings[:limit]
