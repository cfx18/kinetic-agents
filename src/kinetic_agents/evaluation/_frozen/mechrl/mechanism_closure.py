"""Solver-object helpers for preserving reaction/species closure."""
from __future__ import annotations


def reaction_participant_names(reaction) -> set[str]:
    """Return stoichiometric participants plus any explicit collider."""
    involved = set(reaction.reactants) | set(reaction.products)
    third_body = getattr(reaction, "third_body", None)
    collider = None if third_body is None else getattr(third_body, "name", None)
    if collider and collider != "M":
        involved.add(str(collider))
    return involved
