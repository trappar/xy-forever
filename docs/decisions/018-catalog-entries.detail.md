# 018: Catalog entries for missing machines (research) (detail)

Detail of [018-catalog-entries.md](018-catalog-entries.md).

## 7. Device check (safe; no content write beyond #17 and #18)
1. With #17: the host hashes `content/op1_factory.json` and lists `presets/` and `display/` names outside the official manifest (leftovers of the old open-xy
   setup).
2. After a content switch (#18): the developer boots the entry, opens the instrument, send-effect and player menus and reads out the names, choosing no unknown
   one. Expected: stock names plus the entry's mods only.
3. Power cycle to XY Forever (PLAY at power-on); the host rehashes the catalog and `content.list`: unchanged = the OS wrote nothing (settles the 1.2.1 writer).
Choosing a leftover entry (the [U] cells) is not needed under the strict rule; never try the player row.
