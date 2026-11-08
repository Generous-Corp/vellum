#pragma once

// VellumRmlUi::Core is a shared library that links RmlUi privately, so RmlUi
// and FreeType symbols stay inside it and only the declarations marked here
// are visible to consumers.
//
// VellumRmlUi::Native is a static library that links RmlUi publicly; it
// defines VELLUM_RMLUI_STATIC for itself and its consumers, so nothing is
// imported from or exported to a DLL.
#if defined(VELLUM_RMLUI_STATIC)
#define VELLUM_RMLUI_API
#elif defined(_WIN32)
#if defined(VELLUM_RMLUI_BUILDING_CORE)
#define VELLUM_RMLUI_API __declspec(dllexport)
#else
#define VELLUM_RMLUI_API __declspec(dllimport)
#endif
#else
#define VELLUM_RMLUI_API __attribute__((visibility("default")))
#endif
