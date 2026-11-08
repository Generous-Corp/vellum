cmake_minimum_required(VERSION 3.24)
project(VellumRmlUiNativeConsumer LANGUAGES CXX)

find_package(VellumRmlUi 0.1 REQUIRED COMPONENTS Native)

add_executable(vellum-rmlui-native-consumer main.cpp)
target_compile_features(vellum-rmlui-native-consumer PRIVATE cxx_std_20)
target_link_libraries(vellum-rmlui-native-consumer PRIVATE VellumRmlUi::Native)
