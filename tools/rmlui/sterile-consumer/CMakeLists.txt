cmake_minimum_required(VERSION 3.24)
project(VellumRmlUiSterileConsumer LANGUAGES CXX)

find_package(VellumRmlUi CONFIG REQUIRED COMPONENTS Core SoftRenderer VellumRenderer)

add_executable(vellum-rmlui-sterile-consumer main.cpp)
target_compile_features(vellum-rmlui-sterile-consumer PRIVATE cxx_std_20)
target_link_libraries(vellum-rmlui-sterile-consumer PRIVATE
    VellumRmlUi::SoftRenderer VellumRmlUi::VellumRenderer Vellum::DawnHeaders)
