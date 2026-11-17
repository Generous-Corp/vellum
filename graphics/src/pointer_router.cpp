#include <vellum/graphics/pointer_router.hpp>

#include <utility>

namespace vellum::graphics {

namespace {

std::uint8_t button_bit(PointerButton button) noexcept {
    return static_cast<std::uint8_t>(1U << static_cast<std::uint8_t>(button));
}

}  // namespace

PointerRouter::PointerRouter(Picker picker) : picker_(std::move(picker)) {}

void PointerRouter::update_hover(
    Point position, PointerModifiers modifiers, std::vector<PointerEvent>& out) {
    auto target = picker_ ? picker_(position, PointerTarget::hover) : std::nullopt;
    if (target == hovered_) return;
    if (hovered_) {
        out.push_back({.kind = PointerEvent::Kind::hover_leave,
                       .node_id = *hovered_,
                       .position = position,
                       .start = position,
                       .modifiers = modifiers});
    }
    hovered_ = std::move(target);
    if (hovered_) {
        out.push_back({.kind = PointerEvent::Kind::hover_enter,
                       .node_id = *hovered_,
                       .position = position,
                       .start = position,
                       .modifiers = modifiers});
    }
}

std::vector<PointerEvent> PointerRouter::down(
    Point position, PointerButton button, PointerModifiers modifiers) {
    std::vector<PointerEvent> out;
    if (captured_) {
        buttons_down_ = static_cast<std::uint8_t>(buttons_down_ | button_bit(button));
        out.push_back({.kind = PointerEvent::Kind::press,
                       .node_id = *captured_,
                       .position = position,
                       .start = start_,
                       .modifiers = modifiers,
                       .button = button});
        return out;
    }
    last_ = position;
    drag_from_ = position;
    update_hover(position, modifiers, out);
    if (!picker_) return out;
    auto target = picker_(position, PointerTarget::press);
    if (!target) return out;
    captured_ = std::move(target);
    capture_button_ = button;
    buttons_down_ = button_bit(button);
    start_ = position;
    out.push_back({.kind = PointerEvent::Kind::press,
                   .node_id = *captured_,
                   .position = position,
                   .start = position,
                   .modifiers = modifiers,
                   .button = button});
    return out;
}

std::vector<PointerEvent> PointerRouter::move(Point position, PointerModifiers modifiers) {
    std::vector<PointerEvent> out;
    if (!captured_) {
        last_ = position;
        update_hover(position, modifiers, out);
        return out;
    }
    const Point delta{position.x - drag_from_.x, position.y - drag_from_.y};
    last_ = position;
    drag_from_ = position;
    const auto under = picker_ ? picker_(position, PointerTarget::press) : std::nullopt;
    out.push_back({.kind = PointerEvent::Kind::drag,
                   .node_id = *captured_,
                   .position = position,
                   .start = start_,
                   .delta = delta,
                   .modifiers = modifiers,
                   .button = capture_button_,
                   .over = under == captured_});
    return out;
}

std::vector<PointerEvent> PointerRouter::up(
    Point position, PointerButton button, PointerModifiers modifiers) {
    std::vector<PointerEvent> out;
    if (!captured_ || (buttons_down_ & button_bit(button)) == 0U) {
        if (!captured_) {
            last_ = position;
            update_hover(position, modifiers, out);
        }
        return out;
    }
    const auto under = picker_ ? picker_(position, PointerTarget::press) : std::nullopt;
    const bool over = under == captured_;
    out.push_back({.kind = PointerEvent::Kind::release,
                   .node_id = *captured_,
                   .position = position,
                   .start = start_,
                   .modifiers = modifiers,
                   .button = button,
                   .over = over});
    if (over && button == capture_button_) {
        out.push_back({.kind = PointerEvent::Kind::click,
                       .node_id = *captured_,
                       .position = position,
                       .start = start_,
                       .modifiers = modifiers,
                       .button = button,
                       .over = true});
    }
    buttons_down_ = static_cast<std::uint8_t>(buttons_down_ & ~button_bit(button));
    if (buttons_down_ == 0U) {
        captured_.reset();
        update_hover(position, modifiers, out);
    }
    return out;
}

std::vector<PointerEvent> PointerRouter::wheel(
    Point position, Point delta, PointerModifiers modifiers) {
    std::vector<PointerEvent> out;
    if (!captured_) last_ = position;
    const auto target = picker_ ? picker_(position, PointerTarget::wheel) : std::nullopt;
    if (!target) return out;
    out.push_back({.kind = PointerEvent::Kind::wheel,
                   .node_id = *target,
                   .position = position,
                   .start = position,
                   .delta = delta,
                   .modifiers = modifiers});
    return out;
}

std::vector<PointerEvent> PointerRouter::cancel(PointerModifiers modifiers) {
    std::vector<PointerEvent> out;
    if (!captured_) return out;
    out.push_back({.kind = PointerEvent::Kind::cancel,
                   .node_id = *captured_,
                   .position = last_,
                   .start = start_,
                   .modifiers = modifiers,
                   .button = capture_button_,
                   .over = false});
    captured_.reset();
    buttons_down_ = 0U;
    return out;
}

std::vector<PointerEvent> PointerRouter::leave(PointerModifiers modifiers) {
    auto out = cancel(modifiers);
    if (hovered_) {
        out.push_back({.kind = PointerEvent::Kind::hover_leave,
                       .node_id = *hovered_,
                       .position = last_,
                       .start = last_,
                       .modifiers = modifiers});
        hovered_.reset();
    }
    return out;
}

}  // namespace vellum::graphics
