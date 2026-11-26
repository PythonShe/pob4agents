"""Single-mod Copy-on-Write ablation (`pob item inspect <slot> --ablate`).

Measures each toggleable mod line's independent marginal impact on DPS, EHP,
resistances, attributes, and equipment/gem requirements using PoB's native
`ToggleDisplayItemModLine` inside a zero-residue `BuildTransaction`.
"""

from .run import ablate_item_mods

__all__ = ["ablate_item_mods"]
