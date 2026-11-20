# Native JSON records

The independently versioned native package/configuration formats and their
bounded adapters live in `native/json.hpp`, `xy_forever/native_json.py` and
`clients/ts/src/native-json.ts`.

- [Schemas, parser bounds and host selection](native-json/schemas.md)
- [Native readers, ownership and integrated resource measurements](native-json/readers.md)

New native packages and saved Mods selections use JSON. The library, cold boot,
content and mod jobs, package reports and host tools still read legacy records
when native records are absent. Ordinary boot writes no migration files.
