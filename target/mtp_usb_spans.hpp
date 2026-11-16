#ifndef XYF_MTP_USB_SPANS_HPP
#define XYF_MTP_USB_SPANS_HPP
// Stock 1.2.1 code the MTP USB driver (target/mtp_usb*, mode xyf-mtp-usb only) reaches beyond
// mtp_spans.hpp: the TX start 88a0c0b0, reached through submit IN 88a08f24 (record 027). Kept out
// of mtp_spans.hpp, which storage_server.cpp CRC-checks at run time in the product: no product
// build reads this table. The builder checks it against the authenticated bootloader in the MTP
// USB mode; B checks it at run time before the driver registers (usb_service.cpp, XYF_MTP_USB).
#include <stdint.h>
namespace xyf { namespace mtp_usb_spans {
static const uint32_t table[][3] = {
    {0x88a0c0b0u, 0x80u, 0xcd2ec45bu},   // TX start; census: see record 027
};
static const uint32_t count = sizeof(table) / sizeof(table[0]);
} }
#endif
