# The device simulator (xy_forever/sim.py, docs/architecture/simulator.md): the device's own sources, rewritten by
# generate.py sim-sources into the build folder, built with a variant's product defines. Included by
# hosttest/CMakeLists.txt (the variants `uv sync` builds) and hosttest/sim/CMakeLists.txt (another variant, on demand).
# Needs ROOT and Python_EXECUTABLE.
set(GENERATE ${CMAKE_CURRENT_LIST_DIR}/generate.py)
set(SIMSRC ${CMAKE_BINARY_DIR}/sim-sources)
execute_process(COMMAND ${Python_EXECUTABLE} ${GENERATE} sim-lists ${ROOT} ${CMAKE_BINARY_DIR}/sim-lists.cmake
                COMMAND_ERROR_IS_FATAL ANY)
include(${CMAKE_BINARY_DIR}/sim-lists.cmake)
set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS ${GENERATE} ${ROOT}/xy_forever/sim_sources.py)

# Every file the rewrite reads, and the one it writes for each (a new or removed file reconfigures).
file(GLOB SIM_TARGET_FILES CONFIGURE_DEPENDS ${ROOT}/target/*.?pp)
file(GLOB SIM_NATIVE_FILES CONFIGURE_DEPENDS ${ROOT}/native/*.?pp ${ROOT}/native/*.inc)
file(GLOB_RECURSE SIM_THIRD_PARTY CONFIGURE_DEPENDS ${ROOT}/third_party/*)
set(SIM_WRITTEN ${SIMSRC}/gen/cold_policy.inc ${SIMSRC}/gen/trace_expected.inc)
foreach(file ${SIM_TARGET_FILES} ${SIM_NATIVE_FILES} ${SIM_THIRD_PARTY})
  file(RELATIVE_PATH rel ${ROOT} ${file})
  list(APPEND SIM_WRITTEN ${SIMSRC}/${rel})
endforeach()
add_custom_command(OUTPUT ${SIM_WRITTEN}
  COMMAND ${Python_EXECUTABLE} ${GENERATE} sim-sources ${ROOT} ${SIMSRC}
  DEPENDS ${GENERATE} ${SIM_TARGET_FILES} ${SIM_NATIVE_FILES} ${SIM_THIRD_PARTY} ${ROOT}/xy_forever/sim.py
          ${ROOT}/xy_forever/probe_report.py
  COMMENT "Rewriting the simulator's sources" VERBATIM)
add_custom_target(sim_sources DEPENDS ${SIM_WRITTEN})

set(SIM_FLAGS -w -O1 -UNDEBUG -fno-strict-aliasing -pthread)
list(TRANSFORM XYF_SIM_NATIVE REPLACE "(.+)" "${SIMSRC}/native/\\1.cpp" OUTPUT_VARIABLE SIM_NATIVE_CPP)
list(TRANSFORM XYF_SIM_TARGET REPLACE "(.+)" "${SIMSRC}/target/\\1.cpp" OUTPUT_VARIABLE SIM_TARGET_CPP)
list(TRANSFORM XYF_SIM_HOST REPLACE "(.+)" "${ROOT}/sim/\\1.cpp" OUTPUT_VARIABLE SIM_HOST_CPP)
set_property(SOURCE ${SIM_HOST_CPP} APPEND PROPERTY INCLUDE_DIRECTORIES ${ROOT}/sim)

# native/: no product defines, so one build serves every variant.
add_library(sim_native OBJECT ${SIM_NATIVE_CPP})
set_target_properties(sim_native PROPERTIES CXX_STANDARD 17 CXX_EXTENSIONS OFF)
target_compile_options(sim_native PRIVATE ${SIM_FLAGS})

# xyf_simulator(TARGET VARIANT): the simulator built with generate.py sim-defines VARIANT (a sim.VARIANTS name or
# product_defines() options as JSON), every device and simulator source with them.
function(xyf_simulator target variant)
  set(defines ${CMAKE_BINARY_DIR}/${target}-defines.h)
  add_custom_command(OUTPUT ${defines}
    COMMAND ${Python_EXECUTABLE} ${GENERATE} sim-defines ${ROOT} ${variant} ${defines}
    DEPENDS ${GENERATE} ${ROOT}/xy_forever/sim.py ${ROOT}/xy_forever/release.py ${ROOT}/xy_forever/xyf_build.py
    VERBATIM)
  add_executable(${target} ${SIM_TARGET_CPP} ${SIM_HOST_CPP} ${defines} $<TARGET_OBJECTS:sim_native>)
  add_dependencies(${target} sim_sources)
  set_target_properties(${target} PROPERTIES CXX_STANDARD 17 CXX_EXTENSIONS OFF)
  target_compile_options(${target} PRIVATE ${SIM_FLAGS} "SHELL:-include ${defines}" "SHELL:-include ${ROOT}/sim/sim.hpp")
  target_include_directories(${target} PRIVATE ${SIMSRC}/target ${SIMSRC}/gen)
  target_link_options(${target} PRIVATE -pthread)
endfunction()
