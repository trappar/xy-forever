// xyf_hosttest._native: one submodule per bind/NAME.cpp (CMakeLists.txt: xyf_module).
#include <nanobind/nanobind.h>

namespace nb = nanobind;

#define XYF_MODULE(name) void bind_##name(nb::module_);
#include "modules.inc"
#undef XYF_MODULE

NB_MODULE(_native, m) {
#define XYF_MODULE(name) bind_##name(m.def_submodule(#name));
#include "modules.inc"
#undef XYF_MODULE
}
