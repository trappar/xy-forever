// target/xyf_clock.hpp for tests/test_xyf_clock.py (ctypes; hosttest/CMakeLists.txt builds it).
#include "xyf_clock.hpp"
extern "C" unsigned clock_ms(unsigned low, unsigned high) { return xyf::clock::ms_from_cycles(low, high); }
extern "C" unsigned clock_units(unsigned units) { return xyf::clock::ms_from_units(units); }
