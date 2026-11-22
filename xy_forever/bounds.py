"""Hard bounds on tests, processes and simulator calls (#230): a hang ends, named, within its bound.

Never raise one to make a test pass. Only a parked simulator sweep (the `sweep` tier, waiting to move to
native tests, #233) gets more: pytest (tests/xyf_bounds.py) allows it SLOW_S, and the simulators and host
Devices it makes (slow_now, set before its setUp) get a SLOW_S deadline and their calls' own timeouts; its
waits pass SLOW_S where they need it.
"""

TEST_S = 60.0  # one test with the fixtures before it (pytest's timeout ini, tests/xyf_bounds.py: HUNG)
SIM_TEST_S = 5.0  # one simulator core test (the `sim` tier, #233): hard-killed past it
SIM_DEADLINE_S = 5.0  # a test's simulator exits on its own this long after it started (xyf_sim --deadline-s)
SIM_CALL_S = 2.0  # one host call to the simulator, wall time (xyf_host.py --sim, conformance.py --sim)
SIM_CONNECT_S = 5.0  # finding the simulator's endpoint and its HELLO (xyf_host.py connect --sim)
WAIT_S = 10.0  # a test's wait for the simulator to reach a state
COMPILE_S = 120.0  # one host compile or link (xy_forever/hostbuild.py)
SLOW_S = 120.0  # a parked simulator sweep (the `sweep` tier, #233)


slow_now = False  # the running test is a parked sweep (tests/xyf_bounds.py sets it before its setUp)


def sim_deadline_s():
    """A new simulator's deadline (xy_forever/sim.py Simulator)."""
    return SLOW_S if slow_now else SIM_DEADLINE_S


def sim_call_s():
    """A new host Device's cap on each call to the simulator (None: each call's own timeout)."""
    return None if slow_now else SIM_CALL_S
