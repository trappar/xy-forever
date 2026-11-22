# The native code built for the host, for the tests (#233): one nanobind extension (xyf_hosttest._native, a
# submodule per bind/NAME.cpp) and one doctest binary (xyf_hosttest/doctest: the C++ unit tests). `uv sync` builds
# both once; a C++ edit rebuilds only what it touched (pyproject.toml: cache-keys, build-dir; the build runs in
# the project environment, not an isolated one, so the build folder survives between builds).
#
# Firmware-shared code compiles as it does for the firmware's language (gnu++11, -Wall -Wextra -Werror); the
# binding glue and the doctest cases may use C++17.
cmake_minimum_required(VERSION 3.20...3.31)
project(xyf_hosttest LANGUAGES C CXX)

set(ROOT ${CMAKE_CURRENT_SOURCE_DIR}/..)
set(WARN -Wall -Wextra -Werror)
find_package(Python 3.14 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(nanobind CONFIG REQUIRED)

# Each firmware source compiles once (gnu++11) into one object library the extension links.
set(FIRMWARE_SOURCES)
set(MODULES)
# xyf_module(NAME SOURCES...): bind/NAME.cpp binds NAME (void bind_NAME(nanobind::module_&)), with the
# repository sources it tests (relative to the repository root).
function(xyf_module name)
  set(MODULES ${MODULES} ${name} PARENT_SCOPE)
  set(sources ${FIRMWARE_SOURCES})
  foreach(source ${ARGN})
    list(APPEND sources ${ROOT}/${source})
  endforeach()
  list(REMOVE_DUPLICATES sources)
  set(FIRMWARE_SOURCES ${sources} PARENT_SCOPE)
endfunction()

xyf_module(aes native/aes.cpp)
xyf_module(hash native/hash.cpp)
xyf_module(content native/content.cpp native/package_policy.cpp native/hash.cpp)
xyf_module(ldr native/ldr.cpp)
xyf_module(policy native/package_policy.cpp)
xyf_module(p224 native/p224.cpp native/tfw_signature.cpp native/hash.cpp)
xyf_module(json native/json.cpp)
xyf_module(peer_boot native/peer_boot.cpp native/hash.cpp native/inflate.cpp)
xyf_module(companions native/companions.cpp)
xyf_module(ble_session native/companions.cpp native/ble_session.cpp native/hash.cpp)
xyf_module(zip native/zip.cpp native/inflate.cpp)
xyf_module(mtp_responder native/mtp_responder.cpp)
xyf_module(package native/package.cpp native/images.cpp native/zip.cpp native/inflate.cpp native/hash.cpp
           native/package_policy.cpp native/companions.cpp native/ldr.cpp)
xyf_module(package_record native/json.cpp target/xyf_manifest.cpp target/xyf_package_record.cpp)
xyf_module(tfw_work native/tfw_os.cpp native/tfw.cpp native/tfw_signature.cpp native/p224.cpp native/aes.cpp
           native/hash.cpp native/inflate.cpp native/ldr.cpp native/zip.cpp native/package_policy.cpp)
xyf_module(manifest target/xyf_manifest.cpp target/xyf_filelist.cpp target/xyf_recognise.cpp target/xyf_store.cpp
           target/xyf_info.cpp)
xyf_module(tfw native/tfw.cpp native/aes.cpp native/hash.cpp)
xyf_module(tfw_package native/tfw.cpp native/aes.cpp native/package.cpp native/images.cpp native/zip.cpp native/inflate.cpp
           native/hash.cpp native/package_policy.cpp native/companions.cpp native/ldr.cpp)
xyf_module(crc32 target/xyf_crc32.cpp native/inflate.cpp)
xyf_module(sound_pack_archive target/file_engine.cpp target/xyf_manifest.cpp native/zip.cpp native/inflate.cpp
           native/mod_content.cpp native/mod_link.cpp native/mod_elf.cpp native/mod_numbers.cpp native/json.cpp)
xyf_module(storage_server target/storage_server.cpp target/file_engine.cpp)
xyf_module(file_write target/storage_server.cpp target/file_engine.cpp)
xyf_module(store target/xyf_store.cpp target/xyf_info.cpp)
xyf_module(state target/xyf_store.cpp target/xyf_crc32.cpp target/xyf_state.cpp)
xyf_module(file_client target/file_client.cpp)
xyf_module(peer_services target/peer_services.cpp native/peer_boot.cpp native/hash.cpp native/inflate.cpp
           target/xyf_crc32.cpp)
xyf_module(file_engine target/file_engine.cpp)
xyf_module(stage2 target/xyf_stage2.cpp target/xyf_crc32.cpp native/hash.cpp)
xyf_module(usb_service target/usb_proto.cpp target/xyf_crc32.cpp target/usb_service.cpp target/storage_server.cpp)
set_source_files_properties(${ROOT}/target/usb_service.cpp PROPERTIES COMPILE_DEFINITIONS XYF_USB_SERVICE=1)
xyf_module(trace_slot native/trace_slot.cpp native/ldr.cpp)
xyf_module(ui target/ui_gfx.cpp target/ui_screens.cpp target/ui_pages.cpp target/xyf_store.cpp target/xyf_info.cpp
           target/xyf_library.cpp target/xyf_state.cpp target/xyf_crc32.cpp target/xyf_catalog.cpp target/xyf_menu.cpp
           target/xyf_menu_view.cpp target/xyf_mods.cpp target/xyf_mods_view.cpp)
# storage_server.cpp: these defines only add host functions (the write scope, the install allowlist), so one
# build serves the default, file-write and USB-service tests.
set_source_files_properties(${ROOT}/target/storage_server.cpp PROPERTIES
                            COMPILE_DEFINITIONS "XYF_USB_FILE_WRITE=1;XYF_USB_SERVICE=1")

add_library(firmware OBJECT ${FIRMWARE_SOURCES})
set_target_properties(firmware PROPERTIES CXX_STANDARD 11 CXX_EXTENSIONS ON POSITION_INDEPENDENT_CODE ON)
target_compile_options(firmware PRIVATE ${WARN})
target_include_directories(firmware PRIVATE ${ROOT}/native ${ROOT}/target)

set(BIND_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/bind/module.cpp)
set(REGISTRY "")
foreach(name ${MODULES})
  list(APPEND BIND_SOURCES ${CMAKE_CURRENT_SOURCE_DIR}/bind/${name}.cpp)
  string(APPEND REGISTRY "XYF_MODULE(${name})\n")
endforeach()
file(CONFIGURE OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/modules.inc CONTENT "${REGISTRY}")

nanobind_add_module(_native NB_STATIC ${BIND_SOURCES} $<TARGET_OBJECTS:firmware>)
set_target_properties(_native PROPERTIES CXX_STANDARD 17)
# Python.h first in every binding, as Python requires (on Linux, a standard header before it defines _POSIX_C_SOURCE).
target_compile_options(_native PRIVATE ${WARN} "SHELL:-include Python.h")
target_include_directories(_native PRIVATE ${ROOT} ${CMAKE_CURRENT_BINARY_DIR})
install(TARGETS _native LIBRARY DESTINATION xyf_hosttest)

# The doctest binary: every C++ unit test (tests/native/NAME_test.cpp), with address and undefined-behaviour
# sanitizers; tests/test_doctest.py runs every case. xyf_doctest_sources(SOURCES...): the repository
# sources the cases link, compiled once each (gnu++11, sanitized).
set(SANITIZE -fsanitize=address,undefined -fno-omit-frame-pointer)
set(DOCTEST_FIRMWARE)
function(xyf_doctest_sources)
  set(sources ${DOCTEST_FIRMWARE})
  foreach(source ${ARGN})
    list(APPEND sources ${ROOT}/${source})
  endforeach()
  list(REMOVE_DUPLICATES sources)
  set(DOCTEST_FIRMWARE ${sources} PARENT_SCOPE)
endfunction()

xyf_doctest_sources(native/hash.cpp)
xyf_doctest_sources(native/json.cpp)
xyf_doctest_sources(native/trace_slot_zero.cpp native/trace_slot.cpp native/ldr.cpp native/memory.cpp)
xyf_doctest_sources(target/xyf_store.cpp target/xyf_info.cpp native/json.cpp target/xyf_manifest.cpp
                    target/xyf_package_record.cpp)
xyf_doctest_sources(target/boot_services.cpp target/cold_heap.cpp target/cold_storage.cpp target/retained_lookup.cpp
                    target/xyf_filelist.cpp target/xyf_crc32.cpp target/xyf_library.cpp target/xyf_state.cpp
                    target/xyf_catalog.cpp target/xyf_menu.cpp target/xyf_state_write.cpp
                    target/xyf_state_write_library.cpp target/storage_probe_record.cpp target/usb_proto.cpp)

add_library(firmware_sanitized OBJECT ${DOCTEST_FIRMWARE})
set_target_properties(firmware_sanitized PROPERTIES CXX_STANDARD 11 CXX_EXTENSIONS ON)
target_compile_options(firmware_sanitized PRIVATE ${WARN} ${SANITIZE})
target_include_directories(firmware_sanitized PRIVATE ${ROOT}/native ${ROOT}/target)

file(GLOB DOCTEST_CASES CONFIGURE_DEPENDS ${ROOT}/tests/native/*_test.cpp)
add_executable(doctest ${CMAKE_CURRENT_SOURCE_DIR}/bind/doctest_main.cpp ${DOCTEST_CASES}
               $<TARGET_OBJECTS:firmware_sanitized>)
set_target_properties(doctest PROPERTIES CXX_STANDARD 17)
target_compile_options(doctest PRIVATE ${WARN} ${SANITIZE})
target_link_options(doctest PRIVATE ${SANITIZE})
target_include_directories(doctest PRIVATE ${ROOT}/native ${ROOT}/target ${ROOT}/tests/native
                           ${ROOT}/third_party/doctest)
install(TARGETS doctest RUNTIME DESTINATION xyf_hosttest)

# Four case files run production code cut from the sources at build time (tests/native/extract.py), with the
# defines that code is built with.
set(EXTRACTED ${CMAKE_CURRENT_BINARY_DIR}/extracted)
set(EXTRACT_OUT ${EXTRACTED}/role_a_dispatch.inc ${EXTRACTED}/lifecycle.inc ${EXTRACTED}/fake_fs.inc
                ${EXTRACTED}/default.inc ${EXTRACTED}/install_verify.inc)
file(GLOB EXTRACT_PARTS CONFIGURE_DEPENDS ${ROOT}/target/usb_service*.ipp ${ROOT}/target/file_boot*.ipp)
add_custom_command(OUTPUT ${EXTRACT_OUT}
  COMMAND ${Python_EXECUTABLE} ${ROOT}/tests/native/extract.py ${ROOT} ${EXTRACTED}
  DEPENDS ${ROOT}/tests/native/extract.py ${ROOT}/xy_forever/source_text.py ${ROOT}/target/peer_services.cpp
          ${ROOT}/target/cold_storage.cpp ${ROOT}/target/storage_server.cpp ${ROOT}/target/storage_server_engine.ipp
          ${ROOT}/target/usb_service.cpp ${ROOT}/target/file_boot.cpp ${ROOT}/target/key_ui.cpp
          ${ROOT}/tests/native/xyf_library_test.cpp ${EXTRACT_PARTS}
  VERBATIM)
target_sources(doctest PRIVATE ${EXTRACT_OUT})
target_include_directories(doctest PRIVATE ${EXTRACTED})
set_source_files_properties(${ROOT}/tests/native/v3_write_lifecycle_test.cpp PROPERTIES
                            COMPILE_DEFINITIONS "XYF_ONE_SESSION=1;XYF_USB_FILE_WRITE=1")
set_source_files_properties(${ROOT}/tests/native/v3_default_refresh_test.cpp PROPERTIES
                            COMPILE_DEFINITIONS XYF_ONE_SESSION=1)

# Programs and loadable libraries the tests and the host tools run (#229), each its own build with its own defines and
# flags where the extension's one build cannot serve: fresh static state per test (a library copied and loaded
# again), a define's variants, the sanitizers, a host tool, the simulator. xyf_hosttest.program(NAME) finds one
# (programs.json: its file and the repository sources it compiles, for --testmon).
# xyf_program(NAME [LIBRARY] [FORK_EACH] SOURCES... [OPTIONS...] [INCLUDES...] [USES...]): SOURCES relative to the
# repository root, or absolute (generated); OPTIONS the compile flags (-O0 unless one is given; -fsanitize also
# links); a LIBRARY is a loadable bundle for ctypes; FORK_EACH: tests/fork_each.cpp runs main (tests/fork_each.py);
# USES: more repository files the build reads, for --testmon (a generated source's original).
function(xyf_program name)
  cmake_parse_arguments(P "LIBRARY;FORK_EACH" "" "SOURCES;OPTIONS;INCLUDES;USES" ${ARGN})
  if(P_FORK_EACH)
    list(APPEND P_SOURCES tests/fork_each.cpp)
    list(APPEND P_OPTIONS -Dmain=fork_each_main)
  endif()
  set(sources)
  set(listed)
  foreach(source ${P_SOURCES} ${P_USES})
    if(NOT IS_ABSOLUTE ${source})
      list(APPEND listed "\"${source}\"")
    endif()
  endforeach()
  foreach(source ${P_SOURCES})
    if(IS_ABSOLUTE ${source})
      list(APPEND sources ${source})
    else()
      list(APPEND sources ${ROOT}/${source})
    endif()
  endforeach()
  if(P_LIBRARY)
    add_library(${name} MODULE ${sources})
    install(TARGETS ${name} LIBRARY DESTINATION xyf_hosttest/bin)
  else()
    add_executable(${name} ${sources})
    install(TARGETS ${name} RUNTIME DESTINATION xyf_hosttest/bin)
  endif()
  set_target_properties(${name} PROPERTIES CXX_STANDARD 11 CXX_EXTENSIONS OFF PREFIX "")
  target_compile_options(${name} PRIVATE -O0 -UNDEBUG ${P_OPTIONS})
  list(FILTER P_OPTIONS INCLUDE REGEX "^-f(no-)?sanitize")
  target_link_options(${name} PRIVATE ${P_OPTIONS})
  target_include_directories(${name} PRIVATE ${P_INCLUDES})
  list(JOIN listed ", " listed)
  set_property(GLOBAL APPEND PROPERTY XYF_PROGRAMS
               "\"${name}\": {\"file\": \"$<TARGET_FILE_NAME:${name}>\", \"sources\": [${listed}]}")
endfunction()

# The simulator's rewritten sources (sim_sources), its gen/cold_policy.inc the host tools include, xyf_simulator().
include(${CMAKE_CURRENT_SOURCE_DIR}/simulator.cmake)
set(STRICT -Wall -Wextra -Werror)
set(ASAN -fsanitize=address,undefined -fno-omit-frame-pointer)
set(ASAN_STRICT -O1 -g ${ASAN} -fno-sanitize-recover=all)
set(FREESTANDING -ffreestanding -fno-builtin)

# The stage-2 menu with its bridge: each test loads its own copy (fresh static state).
xyf_program(stage2_menu LIBRARY OPTIONS -O1 ${STRICT} ${FREESTANDING} -fno-exceptions -fno-rtti
  SOURCES target/stage2_menu.cpp target/xyf_menu.cpp target/xyf_menu_view.cpp target/ui_pages.cpp target/ui_screens.cpp
          target/ui_gfx.cpp target/xyf_catalog.cpp target/xyf_store.cpp tests/stage2_menu_bridge.cpp)
# tfw_import_source on the device's path (__bfin__) with the simulator's rewrite of the dispatcher.
set(REWRITTEN ${CMAKE_BINARY_DIR}/rewritten/tfw_device_check.cpp)
add_custom_command(OUTPUT ${REWRITTEN}
  COMMAND ${Python_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/generate.py rewrite ${ROOT}
          ${ROOT}/target/tfw_device_check.cpp ${REWRITTEN}
  DEPENDS ${CMAKE_CURRENT_SOURCE_DIR}/generate.py ${ROOT}/xy_forever/sim.py ${ROOT}/target/tfw_device_check.cpp VERBATIM)
xyf_program(tfw_import_source OPTIONS -O2 ${STRICT} -Wno-unknown-attributes -D__bfin__=1 -DXYF_TFW_CHECK=1
  INCLUDES ${ROOT}/target USES target/tfw_device_check.cpp
  SOURCES tests/tfw_import_source_bridge.cpp ${REWRITTEN} target/tfw_import_source.cpp target/file_engine.cpp
          target/boot_services.cpp native/tfw_check.cpp native/tfw.cpp native/tfw_signature.cpp native/p224.cpp
          native/aes.cpp native/hash.cpp)
xyf_program(clock LIBRARY OPTIONS ${STRICT} INCLUDES ${ROOT}/target SOURCES tests/clock_bridge.cpp)
# The composite USB driver against the stack model, without and with the RX kick (XYF_MTP_USB_KICK).
foreach(kick 0 1)
  xyf_program(mtp_usb_kick${kick} OPTIONS ${STRICT} ${ASAN} -DXYF_MTP_USB_KICK=${kick}
    SOURCES target/mtp_usb.cpp target/mtp_usb_test.cpp native/mtp_responder.cpp)
endforeach()
xyf_program(proto LIBRARY OPTIONS -O1 ${STRICT} INCLUDES ${ROOT}/target
  SOURCES target/usb_proto.cpp target/xyf_crc32.cpp tests/proto_bridge.cpp)
xyf_program(crash_screen OPTIONS ${STRICT} -fsanitize=address,undefined INCLUDES ${ROOT}/target
  SOURCES tests/crash_screen_main.cpp)
xyf_program(package_bridge_sanitized OPTIONS -O1 ${STRICT} ${ASAN}
  SOURCES tests/package_bridge.cpp native/package.cpp native/images.cpp native/zip.cpp native/inflate.cpp native/hash.cpp
          native/package_policy.cpp native/companions.cpp native/ldr.cpp)
xyf_program(zip_bridge_sanitized FORK_EACH OPTIONS -O1 ${STRICT} ${ASAN}
  SOURCES tests/zip_bridge.cpp native/zip.cpp native/inflate.cpp)
# zip and inflate as the freestanding firmware builds them: their objects call no runtime (test_native_zip.py).
add_library(zip_freestanding STATIC ${ROOT}/native/zip.cpp ${ROOT}/native/inflate.cpp)
set_target_properties(zip_freestanding PROPERTIES CXX_STANDARD 11 CXX_EXTENSIONS OFF PREFIX "")
target_compile_options(zip_freestanding PRIVATE -O2 ${FREESTANDING} -fno-exceptions -fno-rtti -fno-stack-protector)
install(TARGETS zip_freestanding ARCHIVE DESTINATION xyf_hosttest/bin)
set_property(GLOBAL APPEND PROPERTY XYF_PROGRAMS "\"zip_freestanding\": {\"file\": \
\"$<TARGET_FILE_NAME:zip_freestanding>\", \"sources\": [\"native/zip.cpp\", \"native/inflate.cpp\"]}")
# The narrow modes' nibble CRC table (XYF_SMALL_CRC): the extension builds these sources without it.
xyf_program(crc32_small LIBRARY OPTIONS -O1 ${STRICT} ${FREESTANDING} -DXYF_SMALL_CRC=1
  SOURCES native/inflate.cpp target/xyf_crc32.cpp tests/xyf_crc32_bridge.cpp tests/zip_crc_bridge.cpp)

# The host tools (xy_forever/modlink.py, modpatch.py, tfw_extract.py): each also sanitized (XYF_SANITIZE=1).
set(LINK_SOURCES native/mod_link.cpp native/mod_elf.cpp native/mod_numbers.cpp native/json.cpp target/xyf_manifest.cpp
                 target/xyf_package_record.cpp)
xyf_program(mod_link_bridge OPTIONS -O2 ${STRICT} -DXYF_MOD_WORK_COUNT SOURCES tests/mod_link_bridge.cpp ${LINK_SOURCES})
xyf_program(mod_link_bridge_sanitized OPTIONS ${ASAN_STRICT} ${STRICT} -DXYF_MOD_WORK_COUNT
  SOURCES tests/mod_link_bridge.cpp ${LINK_SOURCES})
# The product's module + stack reservation (xyf-build --xyf-product), cold.cpp's ranges (gen/cold_policy.inc).
set(POLICY ${CMAKE_BINARY_DIR}/sim-sources/gen/cold_policy.inc)
set(ASSEMBLE_SOURCES host/mod_assemble.cpp native/mod_patch.cpp native/mod_content.cpp native/mod_job.cpp native/hash.cpp
                     native/ldr.cpp native/inflate.cpp ${LINK_SOURCES} ${POLICY})
set(EXTRACT_SOURCES host/tfw_extract.cpp native/tfw_os.cpp native/tfw.cpp native/tfw_check.cpp native/tfw_signature.cpp
                    native/p224.cpp native/aes.cpp native/hash.cpp native/inflate.cpp native/ldr.cpp
                    native/package_policy.cpp ${POLICY})
set(PRODUCT -DXYF_STACK_END=0x8ab84000u)
foreach(variant "" _sanitized)
  set(flags -O2)
  set(each)
  if(variant)
    set(flags ${ASAN_STRICT})
    set(each FORK_EACH)  # many runs for one sanitizer start (tests/fork_each.py)
  endif()
  xyf_program(mod_assemble${variant} OPTIONS ${flags} ${STRICT} ${PRODUCT} INCLUDES ${CMAKE_BINARY_DIR}/sim-sources/gen
    USES target/cold.cpp SOURCES ${ASSEMBLE_SOURCES})
  xyf_program(tfw_extract${variant} ${each} OPTIONS ${flags} ${STRICT} ${PRODUCT}
    INCLUDES ${CMAKE_BINARY_DIR}/sim-sources/gen USES target/cold.cpp SOURCES ${EXTRACT_SOURCES})
endforeach()

# The simulator variants the tests start (xy_forever/sim.py VARIANTS); another one builds on demand (hosttest/sim/).
foreach(variant ${XYF_SIM_VARIANTS})
  xyf_simulator(sim-${variant} ${variant})
  install(TARGETS sim-${variant} RUNTIME DESTINATION xyf_hosttest/bin)
  set_property(GLOBAL APPEND PROPERTY XYF_PROGRAMS "\"sim-${variant}\": {\"file\": \"sim-${variant}\", \"sources\": []}")
endforeach()

get_property(programs GLOBAL PROPERTY XYF_PROGRAMS)
list(JOIN programs ",\n  " programs)
file(GENERATE OUTPUT ${CMAKE_CURRENT_BINARY_DIR}/programs.json CONTENT "{\n  ${programs}\n}\n")
install(FILES ${CMAKE_CURRENT_BINARY_DIR}/programs.json DESTINATION xyf_hosttest)
