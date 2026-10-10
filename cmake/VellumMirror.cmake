set(_VELLUM_MIRROR_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/mirror")
# Dry-run mirror package. Sources retain their original namespace by Decision 0010.
file(GLOB_RECURSE _mirror_platform_sources CONFIGURE_DEPENDS
    "${_VELLUM_MIRROR_ROOT}/core/platform/src/*.cpp"
    "${_VELLUM_MIRROR_ROOT}/core/platform/platform/mac/*.mm")
file(GLOB_RECURSE _mirror_canvas_sources CONFIGURE_DEPENDS
    "${_VELLUM_MIRROR_ROOT}/core/canvas/src/*.cpp"
    "${_VELLUM_MIRROR_ROOT}/core/canvas/platform/mac/*.mm")
add_library(vellum-mirror-foundation STATIC ${_mirror_platform_sources})
add_library(Vellum::MirrorFoundation ALIAS vellum-mirror-foundation)
set_target_properties(vellum-mirror-foundation PROPERTIES EXPORT_NAME MirrorFoundation)
target_include_directories(vellum-mirror-foundation PUBLIC
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/core/platform/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/external>
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/external/cpp-httplib>
    $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-mirror-foundation PUBLIC cxx_std_20)
add_library(vellum-mirror-canvas STATIC ${_mirror_canvas_sources})
add_library(Vellum::MirrorCanvas ALIAS vellum-mirror-canvas)
set_target_properties(vellum-mirror-canvas PROPERTIES EXPORT_NAME MirrorCanvas)
target_include_directories(vellum-mirror-canvas PUBLIC
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/core/canvas/include>
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/core/platform/include>
    $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/external>
    $<BUILD_INTERFACE:${_VELLUM_MIRROR_ROOT}/external/nanosvg>
    $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-mirror-canvas PUBLIC cxx_std_20)
target_link_libraries(vellum-mirror-canvas PUBLIC Vellum::MirrorFoundation)
set(VELLUM_MIRROR_RECEIPT "${CMAKE_CURRENT_SOURCE_DIR}/provenance/mirror/receipts/20261010-7ec59a087914.json")
if(NOT EXISTS "${VELLUM_MIRROR_RECEIPT}")
    message(FATAL_ERROR "Vellum mirror receipt is required: ${VELLUM_MIRROR_RECEIPT}")
endif()
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/canvas/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/platform/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/runtime/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(DIRECTORY ${_VELLUM_MIRROR_ROOT}/core/simd/include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(FILES "${VELLUM_MIRROR_RECEIPT}" DESTINATION "${CMAKE_INSTALL_DATADIR}/vellum/provenance/mirror/receipts")
if(VELLUM_BUILD_TESTS AND NOT EMSCRIPTEN)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_test(NAME vellum.mirror-receipt COMMAND "${Python3_EXECUTABLE}" "${CMAKE_SOURCE_DIR}/tools/provenance/test_mirror_receipt.py" "${VELLUM_MIRROR_RECEIPT}")
    add_test(NAME vellum.mirror-install-consumer COMMAND "${CMAKE_COMMAND}" "-DVELLUM_SOURCE_DIR=${CMAKE_SOURCE_DIR}" "-DVELLUM_BUILD_DIR=${CMAKE_BINARY_DIR}" "-DTEST_CONFIG=$<CONFIG>" -P "${CMAKE_SOURCE_DIR}/cmake/RunMirrorInstallConsumer.cmake")
endif()
