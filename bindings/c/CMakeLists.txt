enable_language(C)

add_library(vellum-host-c SHARED src/vellum_host.cpp)
add_library(Vellum::HostC ALIAS vellum-host-c)

set_target_properties(vellum-host-c PROPERTIES
    EXPORT_NAME HostC
    OUTPUT_NAME vellum-host-c
    MACOSX_RPATH ON
    INSTALL_RPATH "@loader_path"
    OSX_ARCHITECTURES arm64
    MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET}
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON)

target_include_directories(vellum-host-c
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-host-c PRIVATE cxx_std_20)
target_link_libraries(vellum-host-c PRIVATE
    Vellum::Authoring
    Vellum::Gpu
    Vellum::DawnHeaders)

file(RELATIVE_PATH _vellum_host_c_pc_prefix
    "/vellum-prefix/${CMAKE_INSTALL_LIBDIR}/pkgconfig" "/vellum-prefix")
string(REGEX REPLACE "/$" "" _vellum_host_c_pc_prefix "${_vellum_host_c_pc_prefix}")
set(VELLUM_HOST_C_PC_PREFIX "\${pcfiledir}/${_vellum_host_c_pc_prefix}")
configure_file(vellum-host-c.pc.in
    "${CMAKE_CURRENT_BINARY_DIR}/vellum-host-c.pc" @ONLY)
install(TARGETS vellum-host-c
    EXPORT VellumTargets
    LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}"
    INCLUDES DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/vellum-host-c.pc"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/pkgconfig")
install(DIRECTORY include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")

if(VELLUM_BUILD_TESTS)
    find_package(Python3 3.9 REQUIRED COMPONENTS Interpreter)
    add_executable(vellum-host-c-test tests/vellum_host_c_test.c)
    set_target_properties(vellum-host-c-test PROPERTIES
        C_STANDARD 11
        C_STANDARD_REQUIRED ON
        C_EXTENSIONS OFF
        OSX_ARCHITECTURES arm64
        MACOSX_DEPLOYMENT_TARGET ${VELLUM_MACOS_DEPLOYMENT_TARGET})
    target_compile_options(vellum-host-c-test PRIVATE -Wall -Wextra -Werror -pedantic)
    target_link_libraries(vellum-host-c-test PRIVATE Vellum::HostC)
    add_test(NAME vellum.host-c
        COMMAND vellum-host-c-test
            "${PROJECT_SOURCE_DIR}/packages/vellum-swift/Fixtures/host-proof-app.js")
    set_tests_properties(vellum.host-c PROPERTIES ENVIRONMENT "MTL_DEBUG_LAYER=0")

    add_test(NAME vellum.host-c-exports
        COMMAND "${Python3_EXECUTABLE}"
            "${CMAKE_CURRENT_SOURCE_DIR}/tests/check_exports.py"
            "$<TARGET_FILE:vellum-host-c>"
            "${CMAKE_CURRENT_SOURCE_DIR}/include/vellum/c/vellum_host.h")
endif()
