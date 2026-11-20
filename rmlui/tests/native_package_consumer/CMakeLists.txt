cmake_minimum_required(VERSION 3.24)
project(VellumRmlUiNativePackageConsumer LANGUAGES CXX OBJCXX)

# Two installed packages on CMAKE_PREFIX_PATH: VellumRmlUiNative gives Native
# and Metal, and finds VellumRmlUi (Core, SoftRenderer) from the other prefix.
find_package(VellumRmlUiNative 0.1 REQUIRED COMPONENTS Native Metal Core SoftRenderer)
find_package(VellumRmlUi 0.1 REQUIRED COMPONENTS NativeHost)

# Core and Native each carry their own RmlUi, so they are linked into
# separate executables.
add_executable(vellum-rmlui-native-metal-consumer native_metal.mm)
target_compile_features(vellum-rmlui-native-metal-consumer PRIVATE cxx_std_20)
target_compile_options(vellum-rmlui-native-metal-consumer PRIVATE -fobjc-arc)
target_link_libraries(vellum-rmlui-native-metal-consumer PRIVATE
    VellumRmlUi::Native VellumRmlUi::Metal)

add_executable(vellum-rmlui-core-consumer core_soft.cpp)
target_compile_features(vellum-rmlui-core-consumer PRIVATE cxx_std_20)
target_link_libraries(vellum-rmlui-core-consumer PRIVATE VellumRmlUi::SoftRenderer)

add_executable(vellum-rmlui-native-parent-consumer native_parent.mm)
target_compile_features(vellum-rmlui-native-parent-consumer PRIVATE cxx_std_20)
target_compile_options(vellum-rmlui-native-parent-consumer PRIVATE -fobjc-arc)
target_link_libraries(vellum-rmlui-native-parent-consumer PRIVATE
    VellumRmlUi::NativeHost "-framework Cocoa" "-framework QuartzCore")
