"""#65 Ask before USB control, for host tools (xyf_host.py Device.call): while the device asks "Allow this computer to
control your OP-XY?" every request but hello answers `waiting`; the host says so once and sends it again every
RETRY_S, with no time limit (the device has none), unless XYF_ASK_LIMIT_S is set (xy_forever/sim.py Simulator.start
sets 30 for the tests): then a TimeoutError after that long. After Don't allow every request answers `declined`:
NotAllowed. #191: with USB mode MTP, hello says serial=off (said once per hello) and every other request answers
`serial_off`: SerialOff."""

import os
import sys
import time

from xy_forever import usb_proto_ids as ids

ASK = 'Allow on your OP-XY: it asks "Allow this computer to control your OP-XY?" (PLAY allows)'
NOT_ALLOWED = (
    "not allowed: Don't allow was chosen on the OP-XY; unplug the USB cable and plug it in again to be asked again"
)
SERIAL_OFF = 'Serial is off on your OP-XY: its USB mode is MTP. Pick MTP + Serial in Settings > USB mode on the OP-XY.'
RETRY_S = 0.5


class NotAllowed(Exception):
    """The device refuses this computer (status declined) until the cable is unplugged or it restarts."""

    status = ids.S_DECLINED


class SerialOff(NotAllowed):
    """USB mode MTP: serial is off until USB mode changes on the device (status serial_off)."""

    status = ids.S_SERIAL_OFF


def say_ask(text=ASK):
    sys.stderr.write(text + '\n')
    sys.stderr.flush()


def call(once, kind, on_ask=say_ask, sleep=time.sleep):
    """once() -> (status, body), sent again while the device asks; on_ask(ASK) once per call (None: silent)."""
    asked, limit = False, float(os.environ.get('XYF_ASK_LIMIT_S') or 0)
    deadline = time.monotonic() + limit
    while True:
        code, body = once()
        if code == ids.S_DECLINED and kind != ids.HELLO:
            raise NotAllowed(NOT_ALLOWED)
        if code == ids.S_SERIAL_OFF and kind != ids.HELLO:
            raise SerialOff(SERIAL_OFF)
        if kind == ids.HELLO and code == ids.S_OK and b';serial=off' in body and on_ask:
            on_ask(SERIAL_OFF)
        if code != ids.S_WAITING or kind == ids.HELLO:
            return code, body
        if limit and time.monotonic() > deadline:
            raise TimeoutError('the device still asks after %g s (XYF_ASK_LIMIT_S): nobody answered it' % limit)
        if not asked and on_ask:
            on_ask(ASK)
        asked = True
        sleep(RETRY_S)
