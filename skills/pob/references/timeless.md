# Timeless / Historic Jewel Seed Search (`pob timeless`)

Searches PoE1 Historic timeless jewels (`Lethal Pride`, `Brutal Restraint`, `Glorious Vanity`, `Militant Faith`, `Elegant Hubris`, `Heroic Tragedy`, etc.) using parallel throwaway headless PoB workers (other Historic jewels removed cleanly during simulation).

> Never pass PoE2 timeless/radius jewels (`Undying Hate`/`不朽之恨`, `Against the Darkness`/`抗衡黑暗`, `Controlled Metamorphosis`, `Prism of Belief`, `From Nothing`) — those are hard-blocked (`pob2 timeless`).
> `Heroic Tragedy` is a PoE1 jewel and works by its PoE1 Chinese names `英勇悲剧` (zh-CN) / `英勇的悲劇` (zh-TW); only the PoE2 localization `英雄的悲歌` is blocked.

## 1. List Supported PoE1 Timeless Jewels & Allocated Sockets
```powershell
pob timeless list [--target main] [--json]
```

## 2. Search Optimal Seeds Verified by PoB
```powershell
# Find best Lethal Pride seeds for DPS in socket nodeId 26725
pob timeless search --jewel "Lethal Pride" --socket 26725 --conqueror Kaom --stat CombinedDPS --top 15

# Chinese jewel name + filter specific notable mod lines
pob timeless search --jewel "致命的骄傲" --socket 26725 --mod "5% chance to deal Double Damage" --stat FullDPS --top 10

# Scan multiple sockets (or `--socket all` for all allocated jewel sockets) in one shared worker pool
pob timeless search --jewel "Festering Vengeance" --socket all --stat CombinedDPS --top 5

# Exact full evaluation mode (checks every PRNG group in PoB; only pass when explicitly requested)
pob timeless search --jewel "Brutal Restraint" --socket 26725 --conqueror Balbala --stat TotalEHP --exact
```

Default search uses non-exhaustive fast modes — additive fitting (`narrow_down`, which may stop once remaining PRNG groups are bounded) for Legion jewels and a conditional `max(top * 4, 200)` weight shortlist for `Glorious Vanity` and 3.30 Abyss jewels (`Festering Vengeance` / `溃烂复仇`, etc.) — reporting both currently allocated notables (`0` extra points) and unallocated notables in radius; either fast mode can miss a higher seed that `--exact` (full evaluation of every resolved group) finds. Note: `Putrid Seed` (`腐朽之种`) is the 3.30 outer cluster passive fork, whereas the 3.30 Abyss timeless jewel is `Festering Vengeance`.

## 3. Conquered Radius Boundaries & `Elegant Hubris` `0`-Stat Passives
- **`Elegant Hubris` (`优雅的狂妄`) `Price of Glory`**: Conquers every small passive in radius into **`Price of Glory`** (zh-TW `榮耀代價`), which grants **nothing**; the conquered notables and keystone carry the jewel's stats and a mastery keeps its own, so a small passive in radius is worth its point only as the path to one of those or to a jewel socket.
- **`TreeWaste` Dead-End Audit (`pob diagnose`)**: Flags an allocated conquered `0`-stat `Price of Glory` small passive when taking it out would disconnect nothing but other such passives: points that lead nowhere. It is not a general dead-end check; a `Price of Glory` node on the way to anything with stats is left alone.
- **Inspect Active Socket Radius (`pob tree --socket-radius <id>`)**:
  ```powershell
  pob tree --socket-radius 26725
  ```
  Lists the nodes around jewel socket `<id>` under a `Radius <name>` heading per ring; a ring beyond the equipped jewel's own radius is marked `outside equipped ... radius (not affected by this jewel)`, so conquered and unconquered passives are not mixed up.

## 4. Direct In-Stock Buyout Lookup (`--trade`)
```powershell
# Evaluate all 3 conquerors in PoB and query in-stock instant buyouts (Early-Stop on top batches):
pob timeless search --jewel "Glorious Vanity" --socket 26725 --conqueror any --trade --budget 5 --currency divine --status securable --trade-limit 5 --max-per-seller 2 [--league Covens] [--realm global|tw]

# Zero-HTTP mode: emit all complexity-safe pre-encoded Trade URLs without calling the API:
pob timeless search --jewel "Lethal Pride" --socket 26725 --conqueror Kaom --trade --max-trade-batches 0 [--realm tw]
```
- **True Multi-Conqueror PoB Evaluation (`--conqueror any`)**: Evaluates each of the jewel's conquerors in PoB, ranks `(conqueror, seed)` pairs by exact PoB gain, and joins trade listings strictly on `(conqueror, seed)` so cross-conqueror keystones never misattribute DPS/EHP. PoE 3.30 `Mazcotl` <-> `Ahuana` (`explicit.pseudo_timeless_jewel_ahuana`) maps transparently in both directions (including `zh-TW` `阿呼阿娜`).
- **Flags & Complexity Sharding**: `--status securable|available|online|any` (`--securable` shortcut), `--budget` + `--currency divine|chaos`, `--trade-limit` (Early-Stop target & post-sort cap), `--max-per-seller` (applied after PoB gain ranking), `--max-trade-batches` (`0` = 0-HTTP URLs for all shards), `--league`, `--realm global|tw` (`tw` localizes jewel/base/league names to `pathofexile.tw`).
- **Offline & Error Resilience**: Under `POB_OFFLINE=1`, `--trade` degrades to `urls_only` (`0` HTTP calls) with a warning; on `CookieRequired` (exit `3`), `RateLimited` (exit `4`), or `TradeError` (exit `5`), the full PoB seed table and pre-encoded fallback URLs are still emitted before exiting.
