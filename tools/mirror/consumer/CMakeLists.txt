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

# The installed usage requirements must name the shared provider directly.
get_target_property(_links Vellum::MirrorCanvas INTERFACE_LINK_LIBRARIES)
if(NOT "Vellum::SkiaDawnProvider" IN_LIST _links)
    message(FATAL_ERROR "Vellum::MirrorCanvas does not expose the shared provider")
endif()
get_target_property(_provider_type Vellum::SkiaDawnProvider TYPE)
if(NOT _provider_type STREQUAL "SHARED_LIBRARY")
    message(FATAL_ERROR "SkiaDawnProvider must own the binary definitions")
endif()
