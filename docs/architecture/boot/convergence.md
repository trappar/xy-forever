# Boot convergence

Part of [boot architecture](../boot.md).

### Converge at power-on (#197)

Every convergence ends by storing `/yaffs2/xy-forever/converged.txt` (`target/xyf_converge.hpp`): the
SHA-256 of the [selected configuration](../../storage/state.md#saved-configuration) and the default it
converged to. A's check at a plain power-on (`file_boot.cpp` `a_converge`) reads the configuration and mark. The same text and no doubt: the default boots
at once, writing nothing. B opens the menu on the progress screen if the fingerprint differs or is missing, a
journal or unfinished switch exists, the default or its content is missing, or official content needs its one-time
cleanup. The menu replays the journal, runs any cleanup and the operations returned by `plan()`, stores the
fingerprint and boots. With no setup saved, that plan is the switch back
to the default's own content alone (nothing saved is written; the switch ends its journal). Menu at power-on set to
Always opens the menu instead when only the default's content is missing; its first row converges. No stock entry, or no record and no
default: it adopts the stock entry first ([jobs.md](../../storage/jobs.md#the-stock-entrys-adoption-178)). PLAY at power-on only opens the menu,
except to adopt or run the [one-time cleanup](../../storage/content-set.md) for an older install. The menu stays open.

Over USB, `menu.converge` (protocol 1.30) starts the same sequence: mode 1 as the first row's PLAY, mode 2 a library
entry booted once with its own content (since 1.31 the stock base's content set first when it has none, #284; its
content switch, then the boot: no record, default or fingerprint written); `job.status` follows its jobs and mode 0 reports it (running, booting, or why it stopped).

Code: `target/usb_service.cpp` (`home_*`), `file_boot.cpp` and `key_ui.cpp` (`XYF_ONE_SESSION`),
`storage_server.cpp`, `peer_services.cpp` (RPC mode), `menu_link.cpp`. Home, reads, dry runs, reboots,
writes, set default without a reboot, and install from home have run on the device.
