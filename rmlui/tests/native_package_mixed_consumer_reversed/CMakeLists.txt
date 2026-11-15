cmake_minimum_required(VERSION 3.24)
project(VellumRmlUiMixedConsumerReversed LANGUAGES CXX)

# The reversed discovery order: VellumRmlUiNative found with its own
# components only, so VellumRmlUi::Core does not exist yet, then VellumRmlUi
# found on its own. One executable linking Native and Core must still fail to
# generate; RunNativePackageConsumer.cmake expects that.
find_package(VellumRmlUiNative 0.1 REQUIRED COMPONENTS Native Metal)
find_package(VellumRmlUi 0.1 REQUIRED COMPONENTS Core)

add_executable(vellum-rmlui-mixed-consumer-reversed
    "${CMAKE_CURRENT_SOURCE_DIR}/../native_package_mixed_consumer/main.cpp")
target_link_libraries(vellum-rmlui-mixed-consumer-reversed PRIVATE VellumRmlUi::Native VellumRmlUi::Core)
