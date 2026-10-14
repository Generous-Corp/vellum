# Links both Skia/Dawn providers into one image. Configuring this must fail:
# Vellum::Gpu and Vellum::MirrorCanvas each carry the locked archives.
cmake_minimum_required(VERSION 3.24)
project(VellumProviderConflict LANGUAGES CXX)
find_package(Vellum CONFIG REQUIRED)
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/main.cpp" "int main() { return 0; }\n")
add_executable(provider-conflict "${CMAKE_CURRENT_BINARY_DIR}/main.cpp")
target_link_libraries(provider-conflict PRIVATE Vellum::Gpu Vellum::MirrorCanvas)
