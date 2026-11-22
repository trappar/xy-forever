# A simulator variant `uv sync` does not build (xy_forever/sim.py build(): a --version or --crash-trigger run), into
# its own build folder: cmake -S hosttest/sim -B FOLDER -G Ninja -DXYF_SIM_OPTIONS=JSON, then cmake --build FOLDER.
cmake_minimum_required(VERSION 3.20...3.31)
project(xyf_sim LANGUAGES C CXX)
set(ROOT ${CMAKE_CURRENT_SOURCE_DIR}/../..)
find_package(Python 3.14 COMPONENTS Interpreter REQUIRED)
include(${CMAKE_CURRENT_SOURCE_DIR}/../simulator.cmake)
xyf_simulator(xyf_sim ${XYF_SIM_OPTIONS})
