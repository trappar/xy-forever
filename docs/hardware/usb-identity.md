# Hardware and stock-code facts: USB identity

Part of [Hardware and stock-code facts](../HARDWARE.md). How the OP-XY names itself on USB in each mode, and
what a browser remembers a permission by (#288). The protocol's summary: [transport](../protocol/transport.md).

## The serial number

- Every mode sends the unit's own serial: the stock metadata's `serial` (8 characters; `11a05340` keeps it
  in the RAM copy at `0x8803d880`). The USB stack's controller start `88a08d90` takes it through
  `88a34edc`, turning each byte outside 0x20..0x7e into `0`. The device-descriptor fill `88a0acc0` sends
  it as iSerialNumber in place of the function driver's own serial, which goes out only when the unit has
  none. Seen through ioreg on build `10ba9028`.
- Every stock driver record names the stack's copy at `0x880734c4` as its serial (`88a35044` fills it
  through `88a13640`; `123456789abcdef` until then).
- The stock dev-menu MTP's GetDeviceInfo (`88a0fd5e`) copies Manufacturer, Model and SerialNumber from
  the string descriptors iManufacturer, iProduct and iSerialNumber.

## Who sets what

Stock driver records (vendor 0x2367, manufacturer "teenage engineering", the unit's serial):

| Record | Product id | Device class | Product string | Mode |
|---|---|---|---|---|
| `0x11803fbc` | 0x8021 | 00 | OP-XY | _pending_ (COM, MIDI?) |
| `0x88a0d7e8` | 0x0021 | EF/02/01 | OP-XY Boot | _pending_ |
| `0x88a0ea78` | 0x0021 | 00 | OP-XY Disk | charging-loop mass storage (`scripts/usb_timeline.py`) |
| `0x88a1251c` | 0x0021 | 00 | OP-XY | _pending_ (dev-menu MTP?) |
| `0x88a13478` | 0x0021 | 02 | OP-XY Boot | the CDC serial service (XY Forever's fallback) |

XY Forever's own:

- The composite (`target/mtp_usb.cpp` `kDevInfo`, `kConfig`): 0x2367:0x0021, class EF/02/01, manufacturer
  "XY Forever", product "OP-XY (XY Forever)", configuration and interface names "XY Forever ...". Its
  serial `XYF0001` goes out only when the unit has none.
- MTP DeviceInfo (`native/mtp_responder.cpp`): "XY Forever", "OP-XY", version "1", the unit's serial
  (`usb_service_mtp_session.ipp` `unit_serial`, the same RAM copy the stack reads). The driver's own test
  responder (`mtp_usb.cpp` `device_info`, only without the session) says `XYF0001`.

## Recorded on the device

| Mode | Vendor:product | Product name | Serial | MTP DeviceInfo |
|---|---|---|---|---|
| Stock bootloader COM (MIDI) | _pending_ | _pending_ | _pending_ | none |
| Stock dev-menu MTP | _pending_ | _pending_ | _pending_ | _pending_ |
| XY Forever (USB service and MTP) | 2367:0021 | _pending_ | the unit's | _pending_ |

Read with no hands: `ioreg -p IOUSB -l -w0` (`idVendor`, `idProduct`, `USB Product Name`, `USB Serial
Number`); MTP's strings: `uv run scripts/mtp_probe.py`. Never commit a unit's serial.

## What a browser keys a permission on

Chrome, from Chromium `8e9fa951` (2026-11-23):

- **Web Serial** (`chrome/browser/serial/serial_chooser_context.cc`): a port is remembered across
  restarts only with a display name and, off Windows, a vendor id, product id and non-empty serial;
  on macOS also the USB driver's name. It matches on exactly those. On Windows it keys on the device
  instance id. Anything else is forgotten when the port goes.
- **WebUSB** (`chrome/browser/usb/usb_chooser_context.cc`): remembered only with a non-empty serial,
  matched on vendor id, product id and serial; otherwise kept until the device is unplugged.
- **Web MIDI** ([spec](https://webaudio.github.io/web-midi-api/), `requestMIDIAccess`): the permission
  (`midi`, with or without `sysex`) belongs to the origin, not a device. `MIDIPort.id` SHOULD survive
  restarts but is the browser's own.

So on macOS and Linux the identity record decides it: same ids and serial (and the same CDC driver on
macOS), same permission. A browser check across a power cycle would add only Windows' instance id.
