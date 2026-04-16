#include <pulp/view/modulation_matrix_widget.hpp>

#include <algorithm>
#include <cmath>

namespace pulp::view {

float ModulationMatrixWidget::row_height_() const {
    const auto rows = std::max<std::size_t>(sources_.size(), destinations_.size());
    if (rows == 0) return 24.0f;
    return std::max(14.0f, local_bounds().height / static_cast<float>(rows));
}

float ModulationMatrixWidget::source_column_x_() const { return 12.0f; }
float ModulationMatrixWidget::dest_column_x_()   const { return local_bounds().width - 12.0f; }

void ModulationMatrixWidget::paint(canvas::Canvas& canvas) {
    const auto b = local_bounds();

    // Background
    canvas.set_fill_color(resolve_color("bg.surface", canvas::Color::rgba8(26, 26, 34)));
    canvas.fill_rect(0, 0, b.width, b.height);

    const float row = row_height_();
    const auto col_source = source_column_x_();
    const auto col_dest   = dest_column_x_();

    // Source labels (left column, left-aligned)
    canvas.set_font("Inter", 11);
    canvas.set_text_align(canvas::TextAlign::left);
    for (std::size_t i = 0; i < sources_.size(); ++i) {
        const float y = row * (i + 0.5f);
        const bool pending = (pending_source_ >= 0 &&
                              static_cast<std::size_t>(pending_source_) == i);
        canvas.set_fill_color(resolve_color(
            pending ? "text.accent" : "text.primary",
            pending ? canvas::Color::rgba8(200, 200, 80)
                    : canvas::Color::rgba8(220, 220, 230)));
        canvas.fill_text(sources_[i].label, col_source, y + 4);
    }

    // Destination labels (right column, right-aligned)
    canvas.set_text_align(canvas::TextAlign::right);
    canvas.set_fill_color(resolve_color("text.primary",
        canvas::Color::rgba8(220, 220, 230)));
    for (std::size_t i = 0; i < destinations_.size(); ++i) {
        const float y = row * (i + 0.5f);
        canvas.fill_text(destinations_[i].label, col_dest, y + 4);
    }

    if (!matrix_) return;

    // Draw one line per active route.
    for (const auto& route : matrix_->routes()) {
        int src_idx = -1;
        int dst_idx = -1;
        for (std::size_t i = 0; i < sources_.size(); ++i)
            if (sources_[i].id == route.source) { src_idx = static_cast<int>(i); break; }
        for (std::size_t i = 0; i < destinations_.size(); ++i)
            if (destinations_[i].id == route.destination) { dst_idx = static_cast<int>(i); break; }
        if (src_idx < 0 || dst_idx < 0) continue;

        const float y0 = row * (src_idx + 0.5f);
        const float y1 = row * (dst_idx + 0.5f);
        const float depth = std::clamp(std::fabs(route.depth), 0.05f, 1.0f);
        canvas.set_line_width(1.0f + 2.0f * depth);
        const uint8_t intensity = static_cast<uint8_t>(80 + 175 * depth);
        canvas.set_stroke_color(route.depth < 0
            ? canvas::Color::rgba8(intensity, 120, 120)
            : canvas::Color::rgba8(120, intensity, 200));
        canvas.stroke_line(col_source + 60, y0, col_dest - 60, y1);
    }
}

void ModulationMatrixWidget::on_mouse_down(Point pos) {
    if (!matrix_ || sources_.empty() || destinations_.empty()) return;
    const float row = row_height_();
    const float mid = local_bounds().width * 0.5f;

    if (pos.x < mid) {
        // Left column — pick a pending source.
        const int idx = static_cast<int>(pos.y / row);
        if (idx >= 0 && idx < static_cast<int>(sources_.size())) {
            pending_source_ = idx;
        }
    } else if (pending_source_ >= 0) {
        // Right column — complete a route to destination.
        const int idx = static_cast<int>(pos.y / row);
        if (idx >= 0 && idx < static_cast<int>(destinations_.size())) {
            ModRoute route;
            route.source      = sources_[pending_source_].id;
            route.destination = destinations_[idx].id;
            route.depth       = 1.0f;
            const auto added = matrix_->add(route);
            selected_route_ = static_cast<int>(added);
            pending_source_ = -1;
        }
    } else {
        // Middle area — click an existing route line to select it.
        const int idx = route_at_(pos);
        if (idx >= 0) selected_route_ = idx;
    }
}

int ModulationMatrixWidget::route_at_(Point pos) const {
    if (!matrix_) return -1;
    const float row = row_height_();
    const float col_source = source_column_x_();
    const float col_dest   = dest_column_x_();
    constexpr float kHitTolerance = 4.0f;  // px, perpendicular distance

    int best = -1;
    float best_d = kHitTolerance;
    for (std::size_t i = 0; i < matrix_->routes().size(); ++i) {
        const auto& route = matrix_->routes()[i];
        int src_idx = -1, dst_idx = -1;
        for (std::size_t s = 0; s < sources_.size(); ++s)
            if (sources_[s].id == route.source) { src_idx = static_cast<int>(s); break; }
        for (std::size_t d = 0; d < destinations_.size(); ++d)
            if (destinations_[d].id == route.destination) { dst_idx = static_cast<int>(d); break; }
        if (src_idx < 0 || dst_idx < 0) continue;
        const float x0 = col_source + 60, y0 = row * (src_idx + 0.5f);
        const float x1 = col_dest   - 60, y1 = row * (dst_idx + 0.5f);
        // Distance from pos to segment (x0,y0)-(x1,y1).
        const float dx = x1 - x0, dy = y1 - y0;
        const float len2 = dx * dx + dy * dy;
        if (len2 < 1e-6f) continue;
        float t = ((pos.x - x0) * dx + (pos.y - y0) * dy) / len2;
        t = std::clamp(t, 0.0f, 1.0f);
        const float px = x0 + t * dx, py = y0 + t * dy;
        const float d = std::hypot(pos.x - px, pos.y - py);
        if (d < best_d) { best_d = d; best = static_cast<int>(i); }
    }
    return best;
}

float ModulationMatrixWidget::set_selected_route_depth(float depth) {
    if (!matrix_ || selected_route_ < 0 ||
        selected_route_ >= static_cast<int>(matrix_->routes().size())) return 0.0f;
    depth = std::clamp(depth, -1.0f, 1.0f);
    // matrix_->routes() is const; we need a mutable handle. The data
    // model exposes routes via a vector that can be rebuilt by add().
    // Re-add with the same source/destination overwrites in-place.
    auto route = matrix_->routes()[selected_route_];
    route.depth = depth;
    route.bipolar = (depth < 0);
    matrix_->add(route);
    return depth;
}

void ModulationMatrixWidget::set_selected_route_curve(ModCurve curve) {
    if (!matrix_ || selected_route_ < 0 ||
        selected_route_ >= static_cast<int>(matrix_->routes().size())) return;
    auto route = matrix_->routes()[selected_route_];
    route.curve = curve;
    matrix_->add(route);
}

void ModulationMatrixWidget::remove_selected_route() {
    if (!matrix_ || selected_route_ < 0 ||
        selected_route_ >= static_cast<int>(matrix_->routes().size())) return;
    matrix_->remove(static_cast<std::size_t>(selected_route_));
    selected_route_ = -1;
}

}  // namespace pulp::view
