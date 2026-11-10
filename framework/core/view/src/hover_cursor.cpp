#include <vellum/view/hover_cursor.hpp>

#include <vellum/view/overlay_dismissal.hpp>

namespace vellum::view {

View::CursorStyle hover_cursor_at(View& root, Point p) {
    View* target = hover_target_at(root, p);
    return target ? target->cursor() : View::CursorStyle::default_;
}

std::optional<View::CursorStyle> HoverCursorTracker::poll_resolved(
    View::CursorStyle style) {
    if (published_ && style == last_) return std::nullopt;
    last_ = style;
    published_ = true;
    return style;
}

std::optional<View::CursorStyle> HoverCursorTracker::poll(View& root) {
    if (!has_pointer_) return std::nullopt;
    return poll_resolved(hover_cursor_at(root, pointer_));
}

}  // namespace vellum::view
