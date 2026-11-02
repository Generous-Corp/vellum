cmake_minimum_required(VERSION 3.24)
project(VellumRmlUiConsumer LANGUAGES CXX)

find_package(VellumRmlUi 0.1 REQUIRED COMPONENTS Core SoftRenderer)

add_executable(vellum-rmlui-consumer main.cpp)
target_link_libraries(vellum-rmlui-consumer PRIVATE VellumRmlUi::SoftRenderer)
