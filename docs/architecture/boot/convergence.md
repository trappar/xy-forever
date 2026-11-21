# Boot convergence

Part of [boot architecture](../boot.md).

### Converge at power-on (#197)

Every convergence ends by storing `/yaffs2/xy-forever/converged.txt` (`target/xyf_converge.hpp`): the
SHA-256 of the [selected configuration](../../storage/state.md#saved-configuration) and the default it
converged to. A's check at a plain power-on (`file_boot.cpp` `a_converge`) reads the configuration and mark. The same text and no doubt: the default boots
at once, writing nothing. A different or missing fingerprint, a journal (`job.txt`), a switch unfinished or
no default: B opens the menu on the progress screen, which runs the existing journal replay, then only
the operations `plan()` returns, stores the fingerprint and boots. No stock entry, or no record and no
default: it adopts the stock entry first ([jobs.md](../../storage/jobs.md#the-stock-entrys-adoption-178)). PLAY at power-on only opens the menu,
except in that adoption case (read once the menu has the library and the record): it adopts, and the menu stays.

Code: `target/usb_service.cpp` (`home_*`), `file_boot.cpp` and `key_ui.cpp` (`XYF_ONE_SESSION`),
`storage_server.cpp`, `peer_services.cpp` (RPC mode), `menu_link.cpp`. Home, reads, dry runs, reboots,
writes, set default without a reboot, and install from home have run on the device.
