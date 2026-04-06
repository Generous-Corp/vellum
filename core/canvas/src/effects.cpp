#include <pulp/canvas/effects.hpp>

#ifdef PULP_HAS_SKIA

#include "include/core/SkCanvas.h"
#include "include/core/SkPaint.h"
#include "include/core/SkColorFilter.h"
#include "include/effects/SkImageFilters.h"

// Access the underlying SkCanvas — requires SkiaCanvas friend or we use
// the save/restore layer approach via Canvas API
// For now, effects work via the Canvas save/restore with paint flags

#endif

namespace pulp::canvas {

// ── Generic effect implementations (works with any Canvas backend) ───────────

void apply_blur(Canvas& canvas, const BlurEffect& effect) {
    // For non-Skia backends, blur is a no-op (CoreGraphics doesn't have
    // a simple blur-everything API). The Skia backend will use SkImageFilters.
    (void)canvas;
    (void)effect;
}

void apply_shadow(Canvas& canvas, const ShadowEffect& effect) {
    // Draw a shadow version offset from the original position
    // This is a simplified implementation — real shadow uses image filters
    canvas.save();
    canvas.translate(effect.offset_x, effect.offset_y);
    canvas.set_fill_color(effect.color);
    // The caller should draw the same shapes again for the shadow
    // This is just setting up the transform
    (void)effect.blur_radius; // Blur not available in generic Canvas
}

void apply_color_adjust(Canvas& canvas, const ColorAdjust& adjust) {
    // Color adjustment is backend-specific
    // For generic Canvas, we adjust fill/stroke colors manually
    (void)canvas;
    (void)adjust;
}

void begin_effect_layer(Canvas& canvas, const BlurEffect& effect) {
    canvas.save();
    // In Skia, this would use saveLayer with an SkPaint containing
    // an SkImageFilter::MakeBlur. For generic Canvas, just save state.
    (void)effect;
}

void begin_effect_layer(Canvas& canvas, const ShadowEffect& effect) {
    canvas.save();
    (void)effect;
}

void end_effect_layer(Canvas& canvas) {
    canvas.restore();
}

} // namespace pulp::canvas
