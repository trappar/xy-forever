# Native JSON records

The independently versioned native package/configuration formats and their
bounded adapters live in `native/json.hpp`, `xy_forever/native_json.py` and
`clients/ts/src/native-json.ts`.

- [Schemas, parser bounds and host selection](native-json/schemas.md)
- [Native readers, ownership and integrated resource measurements](native-json/readers.md)

Migration remains in progress in #95. Host package admission and metadata reads,
native library/cold boot, content switches, journal source validation and package
reports accept the native package record. Mod assembly/Mods readers,
configuration and all default writers still use legacy records. No ordinary
boot performs migration writes.
