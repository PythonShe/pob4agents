"""The ablation run: toggle each mod line off inside a rolled-back transaction and collect its delta."""

from __future__ import annotations

from typing import Any

from ..analyzer.inspectors import resolve_slot_name
from ..bridge_client import BridgeWorker
from ..bridge_protocol import get_worker_caps
from ..errors import DaemonRequestError
from ..formatters import strip_pob_escapes
from ..snapshot import BuildScope, BuildTransaction, capture_digest, capture_direct_digest
from .deltas import (
    _ANNULLABLE_CATEGORIES,
    _build_baseline_block,
    _eval_single_mod_delta,
    _extract_fractured_keys,
    _has_utility_guard,
    _line_switched_off,
    _main_group_label,
    _parse_item_craftability,
    _slot_category,
    _slot_is_active,
)


def ablate_item_mods(worker: BridgeWorker, slot_name: str) -> dict[str, Any]:
    """Toggle each mod line off on an equipped item in a 0-residue CoW transaction."""
    caps = get_worker_caps(worker)
    if not caps.get("itemModLineToggle"):
        raise DaemonRequestError(
            "Single-mod ablation (`--ablate`) requires engine capability `itemModLineToggle` "
            "(PoB 2.69+ / updated PobTools-zh bridge). Run `pob update` to upgrade your local engine."
        )

    with BuildTransaction(worker, scope=BuildScope.ACTIVE_SETS, will_commit=False) as tx:
        items_res = worker.call("list_items", {})
        resolved_slot = resolve_slot_name(worker, slot_name, items_res=items_res)
        slot_obj = next((s for s in (items_res.get("slots") or []) if s.get("name") == resolved_slot), None)
        item_id = slot_obj.get("selItemId") if isinstance(slot_obj, dict) else None
        if not item_id:
            raise DaemonRequestError(
                f"Slot '{resolved_slot}' is empty; equip an item before running `--ablate`."
            )
        item_id = int(item_id)
        item_meta = next((it for it in (items_res.get("items") or []) if it.get("id") == item_id), {})
        slot_active = _slot_is_active(slot_obj, 2 if items_res.get("useSecondWeaponSet") else 1)
        is_flask = bool(slot_obj.get("isFlask"))

        base_digest = capture_digest(worker)
        tx.verify_digest = base_digest
        # Align baseline warnings with `capture_direct_digest` so zh-CN/zh-TW translated warnings
        # compare in the same locale/format as each ablated candidate pass.
        base_direct = capture_direct_digest(worker, base_digest=base_digest)
        # The stats come from that pass as well: every stat is compared, and the two paths do not
        # report the same set of them.
        eval_base_digest = {
            **base_digest,
            "stats": base_direct.get("stats") or base_digest.get("stats") or {},
            "warnings": list(base_direct.get("warnings") or base_digest.get("warnings") or []),
        }
        base_main_group = _main_group_label(base_digest.get("buildHeader"))
        baseline = _build_baseline_block(base_digest)

        edit_state = worker.call("item_edit_begin", {"id": item_id})
        try:
            raw_text = str(edit_state.get("raw") or "")
            mod_lines = [ml for ml in (edit_state.get("modLines") or []) if isinstance(ml, dict)]
        finally:
            try:
                worker.call("item_edit_cancel", {})
            except Exception:
                pass

        fractured_keys = _extract_fractured_keys(raw_text)
        cluster_jewel = bool(item_meta.get("clusterJewel"))
        deltas: list[dict[str, Any]] = []
        disabled_count = 0

        def _judge(ml: dict[str, Any], cand_digest: dict[str, Any], unmeasured: str | None) -> dict[str, Any]:
            return _eval_single_mod_delta(
                entry=ml,
                slot_category=_slot_category(ml, fractured_keys),
                base_digest=eval_base_digest,
                cand_digest=cand_digest,
                unmeasured=unmeasured,
                cluster_jewel=cluster_jewel,
            )

        for ml in mod_lines:
            if ml.get("disabled"):
                # Already off on the item: there is nothing to switch, and the count says so.
                disabled_count += 1
                continue
            line_idx = int(ml.get("index") or 0)
            if line_idx <= 0:
                continue
            edit_open = False
            unmeasured: str | None = None
            cand_digest = eval_base_digest
            try:
                worker.call("item_edit_begin", {"id": item_id})
                edit_open = True
                set_state = worker.call("item_edit_set", {"modLine": {"index": line_idx, "enabled": False}})
                if not _line_switched_off(set_state, line_idx):
                    unmeasured = "toggleIgnored"
                else:
                    # `bridge.lua:5477` calls `AddDisplayItem(p.equip == false)`. Because `item_edit_begin`
                    # preserves `newItem.id = src.id`, `ItemsTab:AddItem` replaces `tab.items[item_id]` in place
                    # without re-auto-equipping the item into another slot (e.g. Ring 2 stays in Ring 2).
                    worker.call("item_edit_commit", {"equip": True})
                    edit_open = False
                    if _main_group_label(worker.call("get_build_header", {})) != base_main_group:
                        unmeasured = "mainSkillShifted"
                    cand_digest = capture_direct_digest(worker, base_digest=base_digest)
            finally:
                if edit_open:
                    try:
                        worker.call("item_edit_cancel", {})
                    except Exception:
                        pass
                tx.restore(rebuild=False)

            deltas.append(_judge(ml, cand_digest, unmeasured))

        restore_check = tx.rollback_verified(base_digest)
        if tx.notes:
            # Every lazy restore that fell back to re-importing the code, not only the last one.
            restore_check = {**restore_check, "fallback": list(tx.notes)}
        if not slot_active:
            # A swapped-out weapon or an unticked flask is outside the calculation: every toggle
            # reads zero whatever the line does once the item is in use.
            for d in deltas:
                d["isDeadMod"] = False
        rarity, corrupted, mirrored, craftable = _parse_item_craftability(raw_text, item_meta)
        annullable_dead_count = sum(
            1
            for d in deltas
            if craftable
            and slot_active
            and not is_flask
            and d["isDeadMod"]
            and d["slotCategory"] in _ANNULLABLE_CATEGORIES
            and not _has_utility_guard(str(d.get("modText") or ""))
        )
        item_name = strip_pob_escapes(
            str(item_meta.get("title") or item_meta.get("name") or resolved_slot)
        )
        item_name_zh = strip_pob_escapes(
            str(item_meta.get("titleZh") or item_meta.get("nameZh") or "")
        )
        out: dict[str, Any] = {
            "slotName": resolved_slot,
            "slotActive": slot_active,
            "itemId": item_id,
            "itemName": item_name,
            "itemRarity": rarity or None,
            "itemCorrupted": corrupted,
            "itemMirrored": mirrored,
            "craftable": craftable,
            "raw": raw_text,
            "primaryDpsKey": baseline["primaryDpsKey"],
            "baseline": baseline,
            "ablation_deltas": deltas,
            "disabledLineCount": disabled_count,
            "unmeasuredCount": sum(1 for d in deltas if d.get("unmeasuredReason")),
            "deadModCount": sum(1 for d in deltas if d["isDeadMod"]),
            "annullableDeadModCount": annullable_dead_count,
            "loadBearingCount": sum(1 for d in deltas if d["bricksRequirements"]),
            "restoreCheck": restore_check,
        }
        if item_name_zh and item_name_zh != item_name:
            out["itemNameZh"] = item_name_zh
        return out
