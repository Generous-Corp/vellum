# The probe has no X11 development files on its host; the module only needs the
# imported target to link.
if(NOT TARGET X11::X11)
    add_library(X11::X11 INTERFACE IMPORTED)
endif()
set(X11_FOUND TRUE)
