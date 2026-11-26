"""What one switched-off mod line did: categories, the delta row, and the lines a toggle cannot isolate."""

from __future__ import annotations

import math
import re
from typing import Any

from ..formatters import _parse_numeric_or_none, strip_pob_escapes
from ..snapshot import _finite_float, _safe_min_ele_max_hit

_EPS = 1e-4
_MAX_HIT_KEYS = (
    "PhysicalMaximumHitTaken",
    "FireMaximumHitTaken",
    "ColdMaximumHitTaken",
    "LightningMaximumHitTaken",
    "ChaosMaximumHitTaken",
)
_ANNULLABLE_CATEGORIES = frozenset({"explicit", "crafted"})
# Where a line sits on the item decides what can remove it. An anoint, a cluster jewel's passive
# lines and other enchants carry PoB's `crafted` flag as well, so the section is read first.
_SECTION_CATEGORIES = frozenset({"buff", "rune", "enchant", "scourge", "implicit", "crucible"})
# PoB's `colorCodes.UNSUPPORTED`: `formatModLine` paints a line with it when the parser left part of
# the line unread, so toggling it cannot change the calculation whatever it does in game.
_UNSUPPORTED_COLOUR = "^xf05050"
# `colorCodes.FRACTURED`. The raw `{fractured}` line cannot always be matched by text: it keeps its
# `(10-20)` range where the displayed line has the rolled value.
_FRACTURED_COLOUR = "^xa29160"
# Lines whose toggle PoB cannot isolate; each is reported with an `unmeasuredReason` instead of a verdict.
# PoB re-reads allocation paths on an item change only for cluster and timeless jewels, so passives
# such a jewel let the build take stay allocated when its line (or its ring size) is off.
_ALLOCATES_UNCONNECTED = ("without being connected to your tree", "only affects passives in")
# A cluster jewel's size, type, socket and notable lines lay its passives out. With one off the nodes
# are renumbered or gone, the build's allocation lands elsewhere, and the delta is that reshuffle.
# "also grant" and "have N% increased Effect" lines change no layout and stay measured.
_CLUSTER_LAYOUT_RE = re.compile(
    r"^adds \d+ (?:jewel socket |small )?passive skills?"
    r"|^added small passive skills grant"
    r"|added passive skills? (?:is|are) ",
    re.IGNORECASE,
)
# Reasons under which the candidate was a different build, not the same one less a line: its
# numbers are withheld. (Removing a granted skill's socket group shifts the groups after it, so
# `mainSocketGroup` can point at another skill.)
_NO_NUMBERS_REASONS = frozenset({"mainSkillShifted", "clusterLayoutChanges"})
_MAX_OTHER_STATS = 8
# Light radius moves these two and nothing a build is judged by; a line that moves only them is dead.
# A build that scales something with light radius moves other stats too, and is not caught here.
_COSMETIC_STATS = frozenset({"LightRadiusInc", "LightRadiusMod"})
# Stats a row reports in a delta field of its own; `otherChangedStats` lists what is left.
_REPORTED_STATS = frozenset(
    {
        "FullDPS", "CombinedDPS", "MinionCombinedDPS", "TotalEHP", "Life", "Mana", "EnergyShield",
        "EffectiveSpellSuppressionChance", "EffectiveMovementSpeedMod", "MovementSpeedMod", "MovementSpeed",
        "FireResist", "ColdResist", "LightningResist", "ChaosResist",
        "FireResistOverCap", "ColdResistOverCap", "LightningResistOverCap", "ChaosResistOverCap",
        "Str", "Dex", "Int", "Omni",
    }
)  # fmt: skip
_NON_CRAFTABLE_RARITIES = frozenset({"UNIQUE", "RELIC"})
_TAG_PREFIX_RE = re.compile(r"^(?:\{[^}]+\})+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_UTILITY_GUARD_KEYWORDS = (
    "regen",
    "recover",
    "leech",
    "on hit",
    "on kill",
    "block",
    "mana cost",
    "flask",
    "charge",
    "ailment",
    "avoid",
    "stun",
    "curse",
    "cooldown",
    "area of effect",
    # Only the main skill's output is compared: a line for another skill of the build reads as dead.
    "minion",
    "socketed",
    "grants level",
    "trigger",
)
_UTILITY_STAT_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lifeRegen", ("NetLifeRegen", "LifeRegenRecovery", "LifeRegen")),
    ("manaRegen", ("NetManaRegen", "ManaRegenRecovery", "ManaRegen")),
    ("esRegen", ("NetEnergyShieldRegen", "EnergyShieldRegen")),
    ("block", ("EffectiveBlockChance", "BlockChance")),
    ("spellBlock", ("EffectiveSpellBlockChance", "SpellBlockChance")),
    ("ward", ("Ward",)),
    ("manaCost", ("ManaCost", "SkillManaCost")),
    ("lifeOnHit", ("LifeOnHit",)),
    ("lifeLeech", ("LifeLeechGainRate",)),
)
_REQ_WARN_TOKENS = (
    "attribute requirement",
    "requirements",
    "don't have enough",
    "do not have enough",
    "cannot use",
    "strength requirement",
    "dexterity requirement",
    "intelligence requirement",
    "不足",
    "无法使用",
    "需求",
)


def _norm_mod_key(text: str) -> str:
    cleaned = _TAG_PREFIX_RE.sub("", strip_pob_escapes(text).strip())
    return " ".join(cleaned.split()).lower()


def _extract_fractured_keys(raw_text: str) -> set[str]:
    """Identify fractured mod lines from PoB raw item text (`{fractured}` prefix tag)."""
    out: set[str] = set()
    for line in (raw_text or "").splitlines():
        s = line.strip()
        if "{fractured}" in s.lower():
            key = _norm_mod_key(s)
            if key:
                out.add(key)
    return out


def _slot_category(entry: dict[str, Any], fractured_keys: set[str]) -> str:
    kind = str(entry.get("kind") or "").strip().lower()
    section = str(entry.get("section") or "explicit").strip().lower()
    if section in _SECTION_CATEGORIES:
        return section
    if kind == "crafted":
        return "crafted"
    if kind == "custom":
        return "custom"
    if kind == "crucible":
        return "crucible"
    if str(entry.get("text") or "").lower().startswith(_FRACTURED_COLOUR):
        return "fractured"
    text_key = _norm_mod_key(str(entry.get("text") or ""))
    if text_key and text_key in fractured_keys:
        return "fractured"
    return section or "explicit"


def _parse_item_craftability(raw_text: str, item_meta: dict[str, Any]) -> tuple[str, bool, bool, bool]:
    """Return `(rarity, corrupted, mirrored, craftable)` from raw PoB text and item metadata."""
    rarity = str(item_meta.get("rarity") or "").strip().upper()
    corrupted = bool(item_meta.get("corrupted"))
    mirrored = bool(item_meta.get("mirrored"))
    for raw_line in (raw_text or "").splitlines():
        s = raw_line.strip()
        low = s.lower()
        if not rarity and low.startswith("rarity:"):
            rarity = s.split(":", 1)[1].strip().upper()
        elif low == "corrupted":
            corrupted = True
        elif low == "mirrored":
            mirrored = True
    craftable = (rarity not in _NON_CRAFTABLE_RARITIES) and not corrupted and not mirrored
    return rarity, corrupted, mirrored, craftable


def _slot_is_active(slot: dict[str, Any], active_weapon_set: int) -> bool:
    """Whether the slot's item takes part in the calculation: not a swapped-out weapon or an unticked flask."""
    if slot.get("inactive"):
        return False
    weapon_set = slot.get("weaponSet")
    if weapon_set and int(weapon_set) != active_weapon_set:
        return False
    return not (slot.get("isFlask") and slot.get("active") is False)


def _line_switched_off(edit_state: Any, line_idx: int) -> bool:
    """Whether `item_edit_set` took: PoB rebuilds a flask's base buff lines on every parse, ignoring the toggle."""
    lines = edit_state.get("modLines") if isinstance(edit_state, dict) else None
    entry = next((m for m in lines or [] if isinstance(m, dict) and m.get("index") == line_idx), None)
    return entry is None or bool(entry.get("disabled"))


def _main_group(header: Any) -> tuple[int, Any, list[Any]]:
    group = header.get("mainSocketGroup") if isinstance(header, dict) else None
    # Labels only: an entry also carries its position, which says nothing about which skill it is.
    entries = [e.get("label") if isinstance(e, dict) else e for e in (group.get("list") or [])] if isinstance(group, dict) else []
    idx = int(group.get("index") or 1) if isinstance(group, dict) else 1
    return idx, (entries[idx - 1] if 0 < idx <= len(entries) else None), entries[: max(idx - 1, 0)]


def _main_skill_moved(base_header: Any, cand_header: Any) -> bool:
    """Whether the main socket group is another skill now than in the baseline.

    PoB keeps the index when a group before it goes, so the entry at that index is compared, and
    the groups ahead of it as well: the next group can carry the very same label.
    """
    b_idx, b_entry, b_ahead = _main_group(base_header)
    c_idx, c_entry, c_ahead = _main_group(cand_header)
    return b_entry != c_entry or (b_idx == c_idx and b_ahead != c_ahead)


def _stat_changed(before: Any, after: Any) -> bool:
    if before == after:
        return False
    fb, fa = _parse_numeric_or_none(before), _parse_numeric_or_none(after)
    if fb is None or fa is None:
        return True
    if math.isnan(fb) or math.isnan(fa):
        return math.isnan(fb) != math.isnan(fa)
    if not (math.isfinite(fb) and math.isfinite(fa)):
        return fb != fa
    return abs(fa - fb) > max(1e-6, 1e-9 * max(abs(fa), abs(fb)))


def _changed_stats(base_stats: dict[str, Any], cand_stats: dict[str, Any]) -> list[str]:
    """Every calc stat that differs between the two digests, in a stable order."""
    keys = (base_stats.keys() | cand_stats.keys()) - _COSMETIC_STATS
    return sorted(k for k in keys if _stat_changed(base_stats.get(k), cand_stats.get(k)))


def _has_utility_guard(mod_text: str) -> bool:
    low = mod_text.lower()
    return any(kw in low for kw in _UTILITY_GUARD_KEYWORDS)


def _extract_utility_stats(stats: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for field, keys in _UTILITY_STAT_SPECS:
        val = 0.0
        for k in keys:
            if stats.get(k) is not None:
                val = _finite_float(stats.get(k))
                break
        out[field] = val
    return out


def _extract_movement_speed_mod(stats: dict[str, Any]) -> float:
    for key in ("EffectiveMovementSpeedMod", "MovementSpeedMod", "MovementSpeed"):
        if stats.get(key) is not None:
            return _finite_float(stats.get(key))
    return 0.0


def _extract_resists(stats: dict[str, Any]) -> dict[str, float]:
    return {
        "fire": _finite_float(stats.get("FireResist")),
        "cold": _finite_float(stats.get("ColdResist")),
        "lightning": _finite_float(stats.get("LightningResist")),
        "chaos": _finite_float(stats.get("ChaosResist")),
    }


def _extract_resist_overcaps(stats: dict[str, Any]) -> dict[str, float]:
    return {
        "fire": _finite_float(stats.get("FireResistOverCap")),
        "cold": _finite_float(stats.get("ColdResistOverCap")),
        "lightning": _finite_float(stats.get("LightningResistOverCap")),
        "chaos": _finite_float(stats.get("ChaosResistOverCap")),
    }


def _extract_attributes(stats: dict[str, Any]) -> dict[str, float]:
    out = {
        "str": _finite_float(stats.get("Str")),
        "dex": _finite_float(stats.get("Dex")),
        "int": _finite_float(stats.get("Int")),
        "omni": _finite_float(stats.get("Omni")),
        "reqStr": _finite_float(stats.get("ReqStr")),
        "reqDex": _finite_float(stats.get("ReqDex")),
        "reqInt": _finite_float(stats.get("ReqInt")),
        "reqOmni": _finite_float(stats.get("ReqOmni")),
    }
    return out


def _ehp_token(val: float | None) -> str:
    if val is None or math.isnan(val):
        return "nan"
    if math.isinf(val):
        return "inf" if val > 0 else "-inf"
    if abs(val - round(val)) < 1e-4:
        return str(round(val))
    return f"{val:.2f}".rstrip("0").rstrip(".")


def _stat_delta_or_changed(b_raw: Any, c_raw: Any) -> tuple[float | None, bool]:
    b_num = _parse_numeric_or_none(b_raw)
    c_num = _parse_numeric_or_none(c_raw)
    if b_num is not None and c_num is not None and math.isfinite(b_num) and math.isfinite(c_num):
        diff = c_num - b_num
        return round(diff, 2), abs(diff) > _EPS
    return None, _ehp_token(b_num) != _ehp_token(c_num)


def _build_baseline_block(digest: dict[str, Any]) -> dict[str, Any]:
    stats = digest.get("stats") or {}
    primary_key = str(digest.get("primaryDpsKey") or "CombinedDPS")
    attrs = _extract_attributes(stats)
    ehp_num = _parse_numeric_or_none(stats.get("TotalEHP"))
    ehp_inf = ehp_num is not None and math.isinf(ehp_num)
    total_ehp_val: float | str = (
        ("inf" if ehp_num > 0 else "-inf")
        if (ehp_inf and ehp_num is not None)
        else (round(ehp_num, 2) if (ehp_num is not None and math.isfinite(ehp_num)) else 0.0)
    )
    return {
        "primaryDpsKey": primary_key,
        "fullDps": round(_finite_float(stats.get("FullDPS")), 2),
        "combinedDps": round(_finite_float(stats.get("CombinedDPS")), 2),
        "primaryDps": round(_finite_float(stats.get(primary_key)), 2),
        "totalEhp": total_ehp_val,
        "totalEhpInfinite": ehp_inf,
        "life": round(_finite_float(stats.get("Life")), 2),
        "mana": round(_finite_float(stats.get("Mana")), 2),
        "energyShield": round(_finite_float(stats.get("EnergyShield")), 2),
        "spellSuppression": round(_finite_float(stats.get("EffectiveSpellSuppressionChance")), 2),
        "movementSpeedMod": round(_extract_movement_speed_mod(stats), 4),
        "resists": _extract_resists(stats),
        "resistOverCaps": _extract_resist_overcaps(stats),
        "attributes": attrs,
    }


def _detect_requirement_bricks(
    base_stats: dict[str, Any],
    cand_stats: dict[str, Any],
    base_warnings: set[str],
    cand_warnings: set[str],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    for attr, req_key in (("Str", "ReqStr"), ("Dex", "ReqDex"), ("Int", "ReqInt"), ("Omni", "ReqOmni")):
        b_val = _finite_float(base_stats.get(attr))
        c_val = _finite_float(cand_stats.get(attr))
        c_req = _finite_float(cand_stats.get(req_key))
        b_req = _finite_float(base_stats.get(req_key))
        eff_req = c_req if c_req > 0 else b_req
        if eff_req > 0 and c_val < eff_req - 0.5:
            b_slack = b_val - b_req if b_req > 0 else 0.0
            c_slack = c_val - eff_req
            if b_val >= eff_req - 0.5 or c_slack < b_slack - 0.5:
                reasons.append(f"{attr} {round(c_val)} < Req {round(eff_req)} ({round(c_slack):+d})")

    base_has_cjk = any(_CJK_RE.search(w) for w in base_warnings)
    cand_has_cjk = any(_CJK_RE.search(w) for w in cand_warnings)
    locale_mismatch = bool(base_warnings and cand_warnings and base_has_cjk != cand_has_cjk)
    if not locale_mismatch:
        for warn in sorted(cand_warnings - base_warnings):
            low = warn.lower()
            if any(tok in low for tok in _REQ_WARN_TOKENS):
                reasons.append(warn)

    return bool(reasons), reasons


def _eval_single_mod_delta(
    *,
    entry: dict[str, Any],
    slot_category: str,
    base_digest: dict[str, Any],
    cand_digest: dict[str, Any],
    unmeasured: str | None = None,
    cluster_jewel: bool = False,
    main_pass_changed: list[str] | None = None,
) -> dict[str, Any]:
    b_stats = base_digest.get("stats") or {}
    c_stats = cand_digest.get("stats") or {}
    primary_key = str(base_digest.get("primaryDpsKey") or "CombinedDPS")

    base_dps = _finite_float(b_stats.get(primary_key))
    cand_dps = _finite_float(c_stats.get(primary_key))
    delta_dps = cand_dps - base_dps
    delta_dps_pct = (delta_dps / base_dps * 100.0) if abs(base_dps) > _EPS else 0.0

    base_ehp_num = _parse_numeric_or_none(b_stats.get("TotalEHP"))
    cand_ehp_num = _parse_numeric_or_none(c_stats.get("TotalEHP"))
    both_ehp_finite = (
        base_ehp_num is not None
        and cand_ehp_num is not None
        and math.isfinite(base_ehp_num)
        and math.isfinite(cand_ehp_num)
    )
    delta_ehp: float | None
    delta_ehp_pct: float | None
    ehp_transition: str | None = None
    if both_ehp_finite and base_ehp_num is not None and cand_ehp_num is not None:
        raw_d_ehp = cand_ehp_num - base_ehp_num
        delta_ehp = round(raw_d_ehp, 2)
        delta_ehp_pct = round((raw_d_ehp / base_ehp_num * 100.0) if abs(base_ehp_num) > _EPS else 0.0, 3)
    else:
        delta_ehp = None
        delta_ehp_pct = None
        b_tok, c_tok = _ehp_token(base_ehp_num), _ehp_token(cand_ehp_num)
        ehp_transition = f"{b_tok}->{c_tok}"

    max_hit_changed = any(_stat_delta_or_changed(b_stats.get(k), c_stats.get(k))[1] for k in _MAX_HIT_KEYS)
    phys_mh_d, _ = _stat_delta_or_changed(b_stats.get("PhysicalMaximumHitTaken"), c_stats.get("PhysicalMaximumHitTaken"))
    ele_mh_d, _ = _stat_delta_or_changed(_safe_min_ele_max_hit(b_stats), _safe_min_ele_max_hit(c_stats))
    chaos_mh_d, _ = _stat_delta_or_changed(b_stats.get("ChaosMaximumHitTaken"), c_stats.get("ChaosMaximumHitTaken"))

    b_res, c_res = _extract_resists(b_stats), _extract_resists(c_stats)
    d_res = {k: round(c_res[k] - b_res[k], 2) for k in ("fire", "cold", "lightning", "chaos")}
    b_ov, c_ov = _extract_resist_overcaps(b_stats), _extract_resist_overcaps(c_stats)
    d_ov = {k: round(c_ov[k] - b_ov[k], 2) for k in ("fire", "cold", "lightning", "chaos")}

    d_attr = {
        k: round(_finite_float(c_stats.get(sk)) - _finite_float(b_stats.get(sk)), 2)
        for k, sk in (("str", "Str"), ("dex", "Dex"), ("int", "Int"), ("omni", "Omni"))
    }
    d_life = round(_finite_float(c_stats.get("Life")) - _finite_float(b_stats.get("Life")), 2)
    d_mana = round(_finite_float(c_stats.get("Mana")) - _finite_float(b_stats.get("Mana")), 2)
    d_es = round(_finite_float(c_stats.get("EnergyShield")) - _finite_float(b_stats.get("EnergyShield")), 2)
    d_supp = round(
        _finite_float(c_stats.get("EffectiveSpellSuppressionChance"))
        - _finite_float(b_stats.get("EffectiveSpellSuppressionChance")),
        2,
    )
    d_ms_raw = _extract_movement_speed_mod(c_stats) - _extract_movement_speed_mod(b_stats)
    d_ms_pct = round(d_ms_raw * 100.0, 2)
    b_util, c_util = _extract_utility_stats(b_stats), _extract_utility_stats(c_stats)
    d_util = {k: round(c_util[k] - b_util[k], 2) for k in b_util if abs(c_util[k] - b_util[k]) > _EPS}

    bricks, brick_reasons = _detect_requirement_bricks(
        b_stats,
        c_stats,
        set(base_digest.get("warnings") or []),
        set(cand_digest.get("warnings") or []),
    )

    # PoB wraps a long line; one row of the table holds one line of text.
    mod_text = " ".join(strip_pob_escapes(str(entry.get("text") or "")).split())
    if unmeasured is None and any(tok in mod_text.lower() for tok in _ALLOCATES_UNCONNECTED):
        unmeasured = "passivesStayAllocated"
    if unmeasured is None and cluster_jewel and _CLUSTER_LAYOUT_RE.search(mod_text):
        unmeasured = "clusterLayoutChanges"
    # A line is dead only when nothing PoB calculates moved: reservation, ailment avoidance,
    # recovery or a max hit count as much as the headline columns do.
    changed = sorted({*_changed_stats(b_stats, c_stats), *(main_pass_changed or ())})
    warnings_changed = set(base_digest.get("warnings") or []) != set(cand_digest.get("warnings") or [])
    unsupported = str(entry.get("text") or "").lower().startswith(_UNSUPPORTED_COLOUR)
    is_dead = not changed and not warnings_changed and not bricks and not unsupported and not unmeasured

    mod_text_zh = " ".join(strip_pob_escapes(str(entry.get("textZh") or "")).split())
    out: dict[str, Any] = {
        "lineIndex": int(entry.get("index") or 0),
        "modText": mod_text,
        "slotCategory": slot_category,
        "deltaDps": round(delta_dps, 2),
        "deltaDpsPct": round(delta_dps_pct, 3),
        "deltaEhp": delta_ehp,
        "deltaEhpPct": delta_ehp_pct,
        "deltaLife": d_life,
        "deltaMana": d_mana,
        "deltaEnergyShield": d_es,
        "deltaSuppression": d_supp,
        "deltaMovementSpeed": d_ms_pct,
        "deltaResists": d_res,
        "deltaResistOverCaps": d_ov,
        "deltaAttributes": d_attr,
        "changedStatCount": len(changed),
        "otherChangedStats": [k for k in changed if k not in _REPORTED_STATS and k != primary_key][:_MAX_OTHER_STATS],
        "bricksRequirements": bricks,
        "isDeadMod": is_dead,
    }
    if d_util:
        out["deltaUtility"] = d_util
    if ehp_transition is not None:
        out["ehpTransition"] = ehp_transition
    if max_hit_changed:
        out["deltaMaxHits"] = {"phys": phys_mh_d, "ele": ele_mh_d, "chaos": chaos_mh_d}
    if mod_text_zh and mod_text_zh != mod_text:
        out["modTextZh"] = mod_text_zh
    if brick_reasons:
        out["brickedReasons"] = brick_reasons
    if unsupported:
        out["unsupported"] = True
    if unmeasured:
        out["unmeasuredReason"] = unmeasured
        if unmeasured in _NO_NUMBERS_REASONS:
            for key in (
                "deltaDps", "deltaDpsPct", "deltaEhp", "deltaEhpPct", "deltaLife", "deltaMana",
                "deltaEnergyShield", "deltaSuppression", "deltaMovementSpeed",
            ):  # fmt: skip
                out[key] = None
            out.update(deltaResists={}, deltaResistOverCaps={}, deltaAttributes={}, otherChangedStats=[])
            out["changedStatCount"] = None
            out["bricksRequirements"] = False
            for key in ("brickedReasons", "ehpTransition", "deltaUtility", "deltaMaxHits"):
                out.pop(key, None)
    return out
