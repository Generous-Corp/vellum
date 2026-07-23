#pragma once

#include <vellum/authoring/js_application.hpp>

#include <functional>
#include <memory>
#include <string_view>

#ifdef __OBJC__
@class NSArray;
@class NSView;
#else
class NSArray;
class NSView;
#endif

namespace vellum::app_host {

class MacAccessibilityBridge final {
public:
    using Action = std::function<bool(std::string_view)>;
    using ValueAction =
        std::function<bool(std::string_view, std::string_view)>;

    MacAccessibilityBridge(
        NSView* owner, Action press, Action focus, ValueAction set_value);
    ~MacAccessibilityBridge();
    MacAccessibilityBridge(MacAccessibilityBridge&&) noexcept;
    MacAccessibilityBridge& operator=(MacAccessibilityBridge&&) noexcept;
    MacAccessibilityBridge(const MacAccessibilityBridge&) = delete;
    MacAccessibilityBridge& operator=(const MacAccessibilityBridge&) = delete;

    void sync(const std::vector<authoring::AccessibilityNode>& nodes);
    [[nodiscard]] NSArray* children() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vellum::app_host
