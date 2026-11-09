# Controls for the module's link boundary check: a stand-in VellumRenderer
# links a stand-in Vellum::Gpu, and with PLANT set another library links it
# too, privately (PLANT=private) or publicly through a generator expression
# (PLANT=genex). Each case is matched on the check's own output.
cmake_minimum_required(VERSION 3.24)
project(vellum_rmlui_link_boundary_control C)

include("${VELLUM_RMLUI_MODULE_DIR}/cmake/VellumRmlUiLinkBoundary.cmake")

file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/stub.c" "int vellum_rmlui_link_boundary_stub(void) { return 0; }\n")
add_library(Vellum::Gpu INTERFACE IMPORTED)
add_library(vellum-rmlui-core STATIC "${CMAKE_CURRENT_BINARY_DIR}/stub.c")
add_library(vellum-rmlui-native STATIC "${CMAKE_CURRENT_BINARY_DIR}/stub.c")
add_library(vellum-rmlui-vellum-renderer STATIC "${CMAKE_CURRENT_BINARY_DIR}/stub.c")
target_link_libraries(vellum-rmlui-native PUBLIC vellum-rmlui-core)
if(NOT PLANT STREQUAL "unlinked_renderer")
    target_link_libraries(vellum-rmlui-vellum-renderer PUBLIC vellum-rmlui-core Vellum::Gpu)
endif()
if(PLANT STREQUAL "private")
    target_link_libraries(vellum-rmlui-native PRIVATE Vellum::Gpu)
elseif(PLANT STREQUAL "genex")
    target_link_libraries(vellum-rmlui-core PUBLIC "$<$<PLATFORM_ID:${CMAKE_SYSTEM_NAME}>:Vellum::Gpu>")
endif()

vellum_rmlui_check_link_boundary(ALLOW vellum-rmlui-vellum-renderer
    TARGETS vellum-rmlui-core vellum-rmlui-native vellum-rmlui-vellum-renderer)
