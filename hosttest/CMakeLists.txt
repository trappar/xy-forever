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
