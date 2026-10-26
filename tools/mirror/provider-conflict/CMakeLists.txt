# A consumer may use both Vellum frontends, but never another Skia/Dawn provider.
cmake_minimum_required(VERSION 3.24)
project(VellumProviderConflict LANGUAGES CXX)
find_package(Vellum CONFIG REQUIRED)
add_library(alternate-provider INTERFACE)
set_target_properties(alternate-provider PROPERTIES
    INTERFACE_VELLUM_SKIA_DAWN_PROVIDER alternate-provider)
set_property(TARGET alternate-provider APPEND PROPERTY
    COMPATIBLE_INTERFACE_STRING VELLUM_SKIA_DAWN_PROVIDER)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/main.cpp" "int main() { return 0; }\n")
add_executable(provider-conflict "${CMAKE_CURRENT_BINARY_DIR}/main.cpp")
target_link_libraries(provider-conflict PRIVATE
    Vellum::Gpu Vellum::MirrorCanvas alternate-provider)
