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
target_compile_options(_native PRIVATE ${WARN})
target_include_directories(_native PRIVATE ${ROOT} ${CMAKE_CURRENT_BINARY_DIR})
install(TARGETS _native LIBRARY DESTINATION xyf_hosttest)

# The doctest binary: every C++ unit test (tests/native/NAME_test.cpp), with address and undefined-behaviour
# sanitizers; tests/test_doctest.py runs its cases per file. xyf_doctest_sources(SOURCES...): the repository
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
