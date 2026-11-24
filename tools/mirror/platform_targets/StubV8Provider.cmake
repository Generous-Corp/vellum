# Stands in for the sealed V8 provider resolver, which would download the
# provider archive. The probe reads the provider path the module compiles in,
# not the provider itself.
function(vellum_v8_resolve_provider)
    set(_stub "${CMAKE_CURRENT_BINARY_DIR}/stub-v8")
    file(MAKE_DIRECTORY "${_stub}")
    file(WRITE "${_stub}/libv8.stub" "")
    file(WRITE "${_stub}/manifest.json" "{}")
    add_library(vellum-v8 INTERFACE)
    set(VELLUM_V8_LIBRARY "${_stub}/libv8.stub" PARENT_SCOPE)
    set(VELLUM_V8_MANIFEST "${_stub}/manifest.json" PARENT_SCOPE)
    set(VELLUM_V8_VERSION "0.0.0-probe" PARENT_SCOPE)
endfunction()
