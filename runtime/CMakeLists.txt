add_library(vellum-runtime
    src/kernel.cpp)
add_library(Vellum::Runtime ALIAS vellum-runtime)

# Compatibility alias for the retained canvas target. This is deliberately not
# an installed public namespace: installed consumers use Vellum::Runtime.
add_library(pulp::runtime ALIAS vellum-runtime)

set_target_properties(vellum-runtime PROPERTIES
    EXPORT_NAME Runtime)

target_include_directories(vellum-runtime
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)

target_compile_features(vellum-runtime PUBLIC cxx_std_20)
target_link_libraries(vellum-runtime PUBLIC Vellum::Foundation)
