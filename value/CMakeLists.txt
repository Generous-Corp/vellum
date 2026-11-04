# vellum::value: the generic value model Vellum widgets bind to (ParamModel,
# ValueFeed) and its in-memory implementations. It depends on nothing else in
# Vellum and on no audio framework.
add_library(vellum-value
    src/in_memory_param_model.cpp
    src/in_memory_value_feed.cpp)
add_library(Vellum::Value ALIAS vellum-value)

set_target_properties(vellum-value PROPERTIES
    EXPORT_NAME Value)

target_include_directories(vellum-value
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>)

target_compile_features(vellum-value PUBLIC cxx_std_20)

