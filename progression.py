import copy
import json
import os
from datetime import date


SKILL_ORDER = (
    "speed", "invisible", "phase", "spear", "flash", "teleport", "trap"
)
LOADOUT_SKILLS = SKILL_ORDER[:-1]
MAX_SKILL_LEVEL = 5
SKILL_PRICES = {
    "speed": 2000,
    "invisible": 3000,
    "phase": 3500,
    "spear": 4000,
    "flash": 3000,
    "teleport": 4500,
    "trap": 0,
}
MISSION_DEFS = {
    "matches": {
        "label": "Complete 1 match",
        "target": 1,
        "coins": 120,
        "xp": 80,
    },
    "skills": {
        "label": "Use skills 3 times",
        "target": 3,
        "coins": 90,
        "xp": 60,
    },
    "escapes": {
        "label": "Escape as a runner",
        "target": 1,
        "coins": 180,
        "xp": 120,
    },
}


def default_profile():
    return {
        "economy_version": 2,
        "name": "",
        "coins": 0,
        "xp": 0,
        "level": 1,
        "owned_skills": [],
        "equipped_skills": [],
        "skill_levels": {skill: 1 for skill in SKILL_ORDER},
        "stats": {
            "matches": 0,
            "escapes": 0,
            "hunter_wins": 0,
            "skills_used": 0,
        },
        "daily": {
            "date": date.today().isoformat(),
            "missions": {
                key: {"progress": 0, "claimed": False}
                for key in MISSION_DEFS
            },
        },
    }


def _merge_profile(raw):
    profile = default_profile()
    if not isinstance(raw, dict):
        return profile

    if raw.get("economy_version") != profile["economy_version"]:
        raw = dict(raw)
        raw["coins"] = 0
        raw["owned_skills"] = []
        raw["equipped_skills"] = []
        raw["skill_levels"] = {}

    for key in ("name", "coins", "xp", "level"):
        if key in raw:
            profile[key] = raw[key]

    owned = raw.get("owned_skills")
    if isinstance(owned, list):
        profile["owned_skills"] = [
            skill for skill in SKILL_ORDER if skill in owned
        ]
    equipped = raw.get("equipped_skills")
    if isinstance(equipped, list):
        profile["equipped_skills"] = [
            skill for skill in equipped
            if skill in LOADOUT_SKILLS and skill in profile["owned_skills"]
        ][:1]

    levels = raw.get("skill_levels", {})
    if isinstance(levels, dict):
        for skill in SKILL_ORDER:
            profile["skill_levels"][skill] = max(
                1, min(MAX_SKILL_LEVEL, int(levels.get(skill, 1)))
            )

    stats = raw.get("stats", {})
    if isinstance(stats, dict):
        for key in profile["stats"]:
            profile["stats"][key] = max(0, int(stats.get(key, 0)))

    daily = raw.get("daily")
    if isinstance(daily, dict) and daily.get("date") == date.today().isoformat():
        for key in MISSION_DEFS:
            state = daily.get("missions", {}).get(key, {})
            profile["daily"]["missions"][key] = {
                "progress": max(0, int(state.get("progress", 0))),
                "claimed": bool(state.get("claimed", False)),
            }
    return profile


def load_profile(path):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _merge_profile(json.load(handle))
    except (OSError, ValueError, TypeError):
        return default_profile()


def save_profile(path, profile):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as handle:
        json.dump(profile, handle, ensure_ascii=False, indent=2)
    os.replace(temp_path, path)


def sanitize_name(value):
    cleaned = "".join(
        char for char in value
        if char.isalnum() or char in " _-"
    ).strip()
    return cleaned[:18]


def xp_needed(level):
    return 100 + max(0, level - 1) * 75


def add_xp(profile, amount):
    profile["xp"] += max(0, int(amount))
    while profile["xp"] >= xp_needed(profile["level"]):
        profile["xp"] -= xp_needed(profile["level"])
        profile["level"] += 1


def add_mission_progress(profile, mission_key, amount=1):
    definition = MISSION_DEFS.get(mission_key)
    state = profile["daily"]["missions"].get(mission_key)
    if definition is None or state is None or state["claimed"]:
        return False

    state["progress"] = min(
        definition["target"], state["progress"] + max(0, int(amount))
    )
    if state["progress"] < definition["target"]:
        return False

    state["claimed"] = True
    profile["coins"] += definition["coins"]
    add_xp(profile, definition["xp"])
    return True


def network_profile(profile):
    return {
        "name": sanitize_name(profile.get("name", "")),
        "equipped_skills": list(profile.get("equipped_skills", []))[:1],
        "skill_levels": copy.deepcopy(profile.get("skill_levels", {})),
    }


def skill_upgrade_price(skill, current_level):
    if current_level >= MAX_SKILL_LEVEL:
        return None
    return 900 + current_level * current_level * 550 \
        + SKILL_ORDER.index(skill) * 175
