add_library(vellum-component-abi INTERFACE)
add_library(Vellum::ComponentAbi ALIAS vellum-component-abi)

set_target_properties(vellum-component-abi PROPERTIES
    EXPORT_NAME ComponentAbi)

target_include_directories(vellum-component-abi
    INTERFACE
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)

install(DIRECTORY include/ DESTINATION "${CMAKE_INSTALL_INCLUDEDIR}")
