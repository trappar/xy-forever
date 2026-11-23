# Hardware and stock-code facts: USB identity

Part of [Hardware and stock-code facts](../HARDWARE.md). How the OP-XY names itself on USB in each mode, and
what a browser remembers a permission by (#288). The protocol's summary: [transport](../protocol/transport.md).

## The serial number

- Every mode sends the unit's own serial: the stock metadata's `serial` (8 characters; `11a05340` keeps it
  in the RAM copy at `0x8803d880`). The USB stack's controller start `88a08d90` takes it through
  `88a34edc`, turning each byte outside 0x20..0x7e into `0`. The device-descriptor fill `88a0acc0` sends
  it as iSerialNumber in place of the function driver's own serial, which goes out only when the unit has
  none. Every stock driver record names the stack's copy at `0x880734c4` (`88a35044` fills it through
  `88a13640`).
- The stock MTP's GetDeviceInfo (`88a0fd5e`) copies Manufacturer, Model and SerialNumber from the string
  descriptors iManufacturer, iProduct and iSerialNumber.

## Stock modes

USB start `88a35044(flags)` sets up one function per flag bit (`88a13676`). Without bit 14 that function's own
driver record registers; with it, the composite `88a0d55e` stacks the functions in this order: bit 0 audio
(MIDI), bit 10 CDC serial, bit 6 mass storage, bit 7 MTP. The composite is class EF/02/01 when audio or
serial is in (each brings an IAD), else 00. All records: vendor 0x2367, "teenage engineering", the unit's serial.

| Flags (caller) | Product id | Class | Product string | Interfaces |
|---|---|---|---|---|
| 0x4401 (`88a1d71e`) | 0x0021 | EF/02/01 | OP-XY Boot | 0 audio control, 1 MIDI streaming (IAD "OP-XY (Composite)"); 2 CDC 02/02/01, 3 CDC data (IAD "OP-XY Boot") |
| 0x0001 (`88a1d71e`) | 0x8021 | 00 | OP-XY | 0 audio control, 1 MIDI streaming |
| 0x4440 (`88a1d690`) | 0x0021 | EF/02/01 | OP-XY Boot | 0..1 CDC, 2 mass storage |
| 0x0040 (`88a1d690`, `88a1dc30`) | 0x0021 | 00 | OP-XY Disk | 0 mass storage 08/06/50 (the charging loop) |
| 0x0080 (`88a1d6b0`, before `mtp_init`) | 0x0021 | 00 | OP-XY | 0 MTP, class 6/1/1 in the record; the device showed 255/1/1 |
| 0x0400 (XY Forever's serial fallback) | 0x0021 | 02 | OP-XY Boot | 0 CDC 02/02/01, 1 CDC data |

## XY Forever's own

- The composite (`target/mtp_usb.cpp` `kDevInfo`, `kConfig`, start flag 0x200): 0x2367:0x0021, class EF/02/01,
  manufacturer "XY Forever", product "OP-XY (XY Forever)"; interface 0 CDC 02/02/01, 1 CDC data, 2 MTP 06/01/01.
  Its serial `XYF0001` goes out only when the unit has none.
- MTP DeviceInfo (`native/mtp_responder.cpp`): "XY Forever", "OP-XY", version "1", the unit's serial
  (`usb_service_mtp_session.ipp` `unit_serial`, the RAM copy the stack reads).

## Recorded on the device (2026-11-23)

| Mode | Vendor:product | Class | Vendor / product string | Interfaces | MTP DeviceInfo |
|---|---|---|---|---|---|
| Stock recovery menu (COM at power-on) | 2367:0021 | EF | teenage engineering / OP-XY Boot | 0–1 MIDI, 2–3 CDC | - |
| Stock dev-menu MTP (Track 3, then Track 2) | 2367:0021 | EF | teenage engineering / OP-XY Boot | the probe saw 0: 255/1/1 | teenage engineering, OP-XY, 1000.0.20 |
| XY Forever menu | 2367:0021 | EF | XY Forever / OP-XY (XY Forever) | 0–1 CDC, 2 MTP 6/1/1 | XY Forever, OP-XY, 1 |
| Stock OS | 2367:8021 | 00 | OP-XY | - | - |

Every mode sends the unit's serial, so vendor id, product id and serial match in every mode of the
bootloader and XY Forever; the strings differ, and XY Forever keeps its own. Two things the record does not
explain: through the dev-menu MTP an ioreg poll every second saw only the composite "OP-XY Boot", yet the MTP
probe listed one interface; and GetDeviceInfo's Model said "OP-XY" although the stock builder takes it from
iProduct. Read with no hands: `ioreg -p IOUSB -l -w0` (it shows `OP_XY Boot`, `OP_XY _XY Forever_`); MTP's
strings: `uv run scripts/mtp_probe.py`. Never commit a unit's serial.

## What a browser keys a permission on

Chrome, from Chromium `8e9fa951` (2026-11-23). The web app uses Web MIDI for te-boot, WebUSB for the dev-menu
MTP copy, and Web Serial for XY Forever.

- **Web Serial** (`chrome/browser/serial/serial_chooser_context.cc`): a port is remembered only if it has a
  display name and, off Windows, a vendor id, product id and non-empty serial. It is matched on exactly those,
  plus on macOS the bundle id of the driver on its USB interface
  (`services/device/serial/serial_device_enumerator_mac.cc`: `GetUsbDriverName`). The display name (the USB
  product string) is shown, not matched. On Windows it is matched on the port's device instance id, which for
  a composite device carries the interface number: CDC is interface 2 in the recovery menu, 0 in XY Forever.
- **WebUSB** (`chrome/browser/usb/usb_chooser_context.cc`): remembered only with a non-empty serial, matched on
  vendor id, product id and serial: the whole device, whatever its interfaces.
- **Web MIDI** ([spec](https://webaudio.github.io/web-midi-api/), `requestMIDIAccess`): the permission (`midi`,
  with or without `sysex`) belongs to the origin, not a device.

Checked on macOS (Chrome 157, build `efb07ddc`, `web/device-test.html`): the OP-XY is one device per API in
every mode. A Web Serial grant given once in XY Forever's menu was the one remembered port, connected in the
recovery menu and back in XY Forever, across two power cycles; the recovery menu's CDC port "OP-XY Boot" is
that same port, though it does not speak XY Forever's protocol. A WebUSB grant was the same remembered device
in both modes. Each API needs its own grant, and none is lost when the mode changes. Chrome's settings list
each API's grant under the name it had, which looked like two devices. Windows is untested: from the source,
Web Serial there keeps the two modes' ports apart (the interface number differs) and WebUSB does not.

`web/device-test.html` (dev only: `pnpm --dir web dev`, then `/device-test.html`) asks each API once and lists
what the page may use; rerun it after any change to the USB descriptors or on another OS.
