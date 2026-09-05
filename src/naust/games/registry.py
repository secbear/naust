"""Profiles by name. The composition root looks games up here and nowhere else."""

from naust.games.profile import GameProfile
from naust.games.valheim.profile import VALHEIM

PROFILES: dict[str, GameProfile] = {VALHEIM.name: VALHEIM}


def get_profile(name: str) -> GameProfile:
    try:
        return PROFILES[name]
    except KeyError:
        known = ", ".join(sorted(PROFILES)) or "none"
        raise ValueError(f"unknown game {name!r}; known games: {known}") from None
