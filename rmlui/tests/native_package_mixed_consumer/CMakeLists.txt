cmake_minimum_required(VERSION 3.24)
project(VellumRmlUiMixedConsumer LANGUAGES CXX)

# One executable linking Native and Core, which each carry their own RmlUi.
# Generating it must fail; RunNativePackageConsumer.cmake expects that.
find_package(VellumRmlUiNative 0.1 REQUIRED COMPONENTS Native Core)

add_executable(vellum-rmlui-mixed-consumer main.cpp)
target_link_libraries(vellum-rmlui-mixed-consumer PRIVATE VellumRmlUi::Native VellumRmlUi::Core)
