"""Host-side tooling for XY Forever, the OP-XY firmware and mod manager."""
import sys

__version__ = "0.1.0"

if 'unittest' in sys.modules:           # a test run: no real device here or in any process it starts
    from xy_forever import no_device
    no_device.switch_on()
