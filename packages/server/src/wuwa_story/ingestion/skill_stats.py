"""Preserve authored skill parameters without deriving or extrapolating stats."""


def skill_description_parameters(detail: object) -> list[str]:
    """Validate skill.json SkillDetailNum paired with SkillDescribe.

    Reject malformed arrays rather than shifting placeholder indices or guessing
    values. MultiSkillDescribe has its own independently ordered parameter array.
    """
    if isinstance(detail, list) and all(isinstance(value, str) for value in detail):
        return list(detail)
    return []


def skill_stat_parameters(detail: object) -> list[list[str]]:
    if not isinstance(detail, list):
        return []
    parameters = []
    for entry in detail:
        values = entry.get("ArrayString") if isinstance(entry, dict) else None
        # Keep missing positions: filtering them would shift subsequent levels.
        parameters.append(
            [value if isinstance(value, str) else "" for value in values]
            if isinstance(values, list) else []
        )
    return parameters


def skill_stat_values(parameters: list[list[str]], description: str | None) -> list[str]:
    """Render verified single-parameter templates; retain others as parameters.

    A singleton remains a singleton constant. Longer arrays retain their source
    length, including missing levels; consumers must not repeat their last value.
    Multi-parameter description semantics are not verified in the source data.
    """
    if len(parameters) != 1:
        return []
    values = parameters[0]
    if not description:
        return list(values)
    remainder = description.replace("{0}", "")
    if "{0}" not in description or "{" in remainder or "}" in remainder:
        return []
    return [description.replace("{0}", value) if value else "" for value in values]
