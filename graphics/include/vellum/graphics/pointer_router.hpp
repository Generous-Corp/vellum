#pragma once

#include <vellum/graphics/hit_test.hpp>

#include <cstdint>
#include <functional>
#include <optional>
#include <vector>

namespace vellum::graphics {

struct PointerModifiers final {
    bool shift = false;
    bool control = false;
    bool alt = false;
    bool meta = false;
};

enum class PointerButton : std::uint8_t { primary, secondary, other };

/// What a host is asking the picker for, so each can use its own set of
/// interactive nodes.
enum class PointerTarget : std::uint8_t { press, hover, wheel };

/// One typed event for a node.
///
/// `position` is the pointer in frame coordinates. `start` is where the
/// capturing press happened (equal to `position` on a press). `delta` is the
/// movement since the previous drag event for a `drag`, and the scroll
/// amount for a `wheel`. `over` says whether the pointer is currently over
/// the captured node, for `drag`, `release` and `click`.
struct PointerEvent final {
    enum class Kind : std::uint8_t {
        press,
        release,
        click,
        drag,
        cancel,
        hover_enter,
        hover_leave,
        wheel,
    };

    Kind kind = Kind::press;
    NodeId node_id;
    Point position{};
    Point start{};
    Point delta{};
    PointerModifiers modifiers{};
    PointerButton button = PointerButton::primary;
    bool over = true;
};

/// A host-independent state machine for one pointer. The host feeds it raw
/// input and maps the typed events it returns to actions; the router decides
/// which node each event belongs to by asking the `Picker`.
///
/// - `down` picks a press target and captures the pointer on it. Until every
///   button is up, `move` produces `drag` events for the captured node, even
///   when the pointer leaves it, and hover does not change. A press that
///   picks nothing captures nothing and produces no events.
/// - `up` produces `release` for the captured node, and `click` when the
///   button that started the capture is released over the same node. When
///   the last button is released, capture ends and hover is re-evaluated at
///   the release point.
/// - `move` without capture produces `hover_leave` and `hover_enter` as the
///   hover target changes, leave first.
/// - `wheel` goes to the node under the pointer, captured or not.
/// - `cancel` ends capture with a `cancel` event and no click. `leave` is
///   `cancel` followed by a `hover_leave`, for a pointer that left the
///   window.
class PointerRouter final {
public:
    using Picker = std::function<std::optional<NodeId>(Point, PointerTarget)>;

    explicit PointerRouter(Picker picker);

    [[nodiscard]] std::vector<PointerEvent> down(
        Point position, PointerButton button, PointerModifiers modifiers = {});
    [[nodiscard]] std::vector<PointerEvent> move(
        Point position, PointerModifiers modifiers = {});
    [[nodiscard]] std::vector<PointerEvent> up(
        Point position, PointerButton button, PointerModifiers modifiers = {});
    [[nodiscard]] std::vector<PointerEvent> wheel(
        Point position, Point delta, PointerModifiers modifiers = {});
    [[nodiscard]] std::vector<PointerEvent> cancel(PointerModifiers modifiers = {});
    [[nodiscard]] std::vector<PointerEvent> leave(PointerModifiers modifiers = {});

    [[nodiscard]] const std::optional<NodeId>& captured() const noexcept { return captured_; }
    [[nodiscard]] const std::optional<NodeId>& hovered() const noexcept { return hovered_; }

private:
    void update_hover(Point position, PointerModifiers modifiers, std::vector<PointerEvent>& out);

    Picker picker_;
    std::optional<NodeId> captured_;
    std::optional<NodeId> hovered_;
    std::uint8_t buttons_down_ = 0U;
    PointerButton capture_button_ = PointerButton::primary;
    Point start_{};
    Point last_{};
    Point drag_from_{};
};

}  // namespace vellum::graphics
