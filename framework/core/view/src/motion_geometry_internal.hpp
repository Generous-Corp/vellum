// motion_geometry_internal.hpp — PRIVATE shared declarations for the
// motion geometry-walker translation units.
//
// Created in the 2026-05 refactor that split the geometry-walker +
// scroll-geometry helpers out of motion.cpp into motion_geometry.cpp.
// The motion Coordinator (still in motion.cpp) calls a small set of
// geometry entry points; this header is their single declaration
// point. The file-local matrix/AABB helpers stay private to
// motion_geometry.cpp.
//
// PRIVATE: lives under core/view/src/, not the public include tree.

#pragma once

#include <vellum/view/motion.hpp>   // GeometryProperty/Space/Source, ScrollProperty, Rect

#include <string>

namespace vellum::view {
class View;
class ScrollView;
}

namespace vellum::view::motion {

// Resolve a View's geometry rect in the requested space, using either
// the layout or the presentation transform. Defined in
// motion_geometry.cpp.
Rect resolve_geometry(vellum::view::View& v,
                      GeometrySpace space,
                      GeometrySource source);

// Pull a single scalar geometry property (MinX/MidY/Width/…) from a
// resolved rect. Defined in motion_geometry.cpp.
double extract_property(const Rect& r, GeometryProperty prop);

// Stable human-readable name for a GeometryProperty (trace output).
// Defined in motion_geometry.cpp.
const char* property_name(GeometryProperty prop);

// Pull a single scroll-geometry property from a ScrollView. Defined in
// motion_geometry.cpp.
double extract_scroll_property(const vellum::view::ScrollView& sv,
                               ScrollProperty prop);

// Stable human-readable name for a ScrollProperty (trace output).
// Defined in motion_geometry.cpp.
const char* scroll_property_name(ScrollProperty prop);

// Format a double with fixed precision for trace lines. Defined in
// motion_geometry.cpp.
std::string fmt_double(double v, int precision);

}  // namespace vellum::view::motion
