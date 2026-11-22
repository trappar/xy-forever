"""Hard bounds on tests, processes and simulator calls (#230): a hang ends, named, within its bound.

A bound ends a hang, never a slow run: each is several times what its work takes on a busy machine, as the
CPU budgets (tests/conftest.py) are what hold a test to its speed. Never raise one to make a test pass.
"""

TEST_S = 60.0  # one test with the fixtures before it (pytest's timeout ini, tests/xyf_bounds.py: HUNG)
SIM_TEST_S = 20.0  # one simulator core test (the `sim` tier, #233): hard-killed past it
SIM_DEADLINE_S = 20.0  # a test's simulator exits on its own this long after it started (xyf_sim --deadline-s)
SIM_CALL_S = 5.0  # one host call to the simulator, wall time (xyf-host --sim, conformance.py --sim)
SIM_CONNECT_S = 5.0  # finding the simulator's endpoint and its HELLO (xyf-host connect --sim)
WAIT_S = 10.0  # a test's wait for the simulator to reach a state
COMPILE_S = 120.0  # one host compile or link (xy_forever/hostbuild.py)
