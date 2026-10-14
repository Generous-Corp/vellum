cmake_minimum_required(VERSION 3.24)
project(VellumMirrorConsumer LANGUAGES CXX)

set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
find_package(Vellum CONFIG REQUIRED)

# The translation unit `verify_mirror_inertness.py --write-debt-probe` writes:
# it compiles every Phase-6 debt header the package ships.
set(VELLUM_MIRROR_DEBT_PROBE "" CACHE FILEPATH "Generated debt-header probe source")
if(NOT VELLUM_MIRROR_DEBT_PROBE OR NOT EXISTS "${VELLUM_MIRROR_DEBT_PROBE}")
    message(FATAL_ERROR "VELLUM_MIRROR_DEBT_PROBE must name the generated debt probe")
endif()
add_executable(vellum-mirror-consumer main.cpp "${VELLUM_MIRROR_DEBT_PROBE}")
target_link_libraries(vellum-mirror-consumer PRIVATE Vellum::MirrorCanvas)
