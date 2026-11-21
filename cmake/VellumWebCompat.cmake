# Harness and host packaging resources are loaded explicitly by their consumers.
# They must not be embedded or evaluated by the native WidgetBridge startup.
install(FILES
    "${CMAKE_CURRENT_LIST_DIR}/../framework/core/view/js/web-compat.js"
    "${CMAKE_CURRENT_LIST_DIR}/../framework/core/view/js/web-compat-selector.js"
    "${CMAKE_CURRENT_LIST_DIR}/../framework/core/view/js/web-compat-runtime-apis.js"
    "${CMAKE_CURRENT_LIST_DIR}/../framework/core/view/js/web-compat-three-shim.js"
    DESTINATION "${CMAKE_INSTALL_LIBDIR}/cmake/Vellum/scripts")
