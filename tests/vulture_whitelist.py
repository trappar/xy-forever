# ruff: noqa
"""vulture's false positives (pyproject.toml [tool.vulture]): names used where vulture cannot see. Never imported.

A name goes here only with its reason; dead code is deleted instead.
"""

# Set for code that reads them by name: pytest-xdist (conftest.py), zipfile (an entry's host OS: Unix),
# the peer-services snapshot packed by getattr over its field-name tuples (test_peer_services.py).
_.ensure_show_status
_.create_system
_.spi_control, _.spi, _.gpio_role, _.gpio_slots, _.gpio_table, _.interrupt_mask, _.vtable, _.rx_dma
_.primary_state, _.released, _.driver_phase, _.rx_pending

# Called from C++ through the nanobind binding (hosttest/bind/content.cpp).
_.read_target, _.read_staged

# Read by pyusb: a device's timeout for control transfers that pass none (usb.util.get_string; usb_alive.py).
_.default_timeout

# Fields of parsed records (companions.py): the whole format, read or not.
_.file_checksum, _.checksum_type

# Positions in enumerations that mirror a C++ enum through range(): the unused ones keep the numbering.
VIEW_READING  # sim_mods_page.py
H_RANGE  # test_p224.py
WAITING_PHASE  # test_peer_boot.py
CHOOSE, BOUNDS, TIMEOUT, DRAIN, CLOSE  # test_peer_services.py
TRACK1  # test_sim_safemode.py
EBUSY, EMFILE  # test_simulator.py
HOME_EMPTY  # test_ui_flow.py
REQUEST_BUILD, REQUEST_UNVERIFIED  # test_ui_mods.py
BUILD_ID  # test_xyf_stage2.py
CHECK_MAGIC, CHECK_SIZE, CHECK_STAGE1  # test_xyf_stage2_service.py
