add_library(vellum-authoring SHARED
    src/js_application.mm
    src/rendered_tree_materializer.mm
    src/service_bridge.cpp)
add_library(Vellum::Authoring ALIAS vellum-authoring)

set_target_properties(vellum-authoring PROPERTIES
    EXPORT_NAME Authoring
    MACOSX_RPATH ON
    INSTALL_RPATH "@loader_path")

target_include_directories(vellum-authoring
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)
target_compile_features(vellum-authoring PUBLIC cxx_std_20)
target_compile_options(vellum-authoring PRIVATE -fobjc-arc)
target_link_libraries(vellum-authoring
    PUBLIC Vellum::Graphics
    PRIVATE
        "-framework Foundation"
        "-framework JavaScriptCore")

if(VELLUM_BUILD_TESTS)
    add_executable(vellum-service-bridge-test
        tests/service_bridge_test.cpp)
    target_link_libraries(vellum-service-bridge-test PRIVATE Vellum::Authoring)
    add_test(NAME vellum.service-bridge COMMAND vellum-service-bridge-test)

    add_executable(vellum-rendered-tree-materializer-test
        tests/rendered_tree_materializer_test.cpp)
    target_include_directories(vellum-rendered-tree-materializer-test
        PRIVATE src)
    target_link_libraries(
        vellum-rendered-tree-materializer-test PRIVATE Vellum::Authoring)
    add_test(
        NAME vellum.rendered-tree-materializer
        COMMAND vellum-rendered-tree-materializer-test)
endif()
