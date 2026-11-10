#pragma once

#include <vellum/view/design_frame_view.hpp>

namespace vellum::view {

// Knob Modulation — Ink & Signal catalog component. Renders the faithful
// Figma-exported SVG 1:1 via DesignFrameView (SkSVGDOM). Generated from
// Figma node 224:2 by tools/import-design/make_catalog_component.py;
// reskin/extend via the faithful-vector lane, never by hand-painting.
class KnobModulationView : public DesignFrameView {
public:
    KnobModulationView();
};

}  // namespace vellum::view
