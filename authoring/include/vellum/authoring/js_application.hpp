#pragma once

#include <vellum/graphics/scene.hpp>

#include <memory>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::authoring {

inline constexpr std::string_view kAuthoringHostProtocol =
    "vellum.authoring-host.v1";

struct Interaction final {
    std::string node_id;
    std::string event;
    std::string action;
    vellum::graphics::Rect bounds;
};

struct RenderedApplication final {
    vellum::graphics::Scene scene;
    std::vector<Interaction> interactions;
};

/// Executes a bundled Vellum JavaScript/TypeScript application through the
/// system JavaScriptCore engine and materializes its retained JSON tree.
///
/// The bundle must mount `@vellum/ui` and expose the versioned `__vellum`
/// bridge. No DOM, WebView, or browser globals are provided.
///
/// Instances are confined to the thread on which `create()` succeeds.
class JsApplication final {
public:
    static std::unique_ptr<JsApplication> create(
        std::string_view bundle, std::string* error = nullptr);

    ~JsApplication();
    JsApplication(JsApplication&&) noexcept;
    JsApplication& operator=(JsApplication&&) noexcept;
    JsApplication(const JsApplication&) = delete;
    JsApplication& operator=(const JsApplication&) = delete;

    [[nodiscard]] bool render(
        RenderedApplication& output, std::string* error = nullptr);
    [[nodiscard]] bool dispatch(
        std::string_view action,
        std::string_view payload_json,
        RenderedApplication& output,
        std::string* error = nullptr);
    [[nodiscard]] bool snapshot_state(
        std::string& output_json, std::string* error = nullptr);
    [[nodiscard]] bool restore_state(
        std::string_view snapshot_json,
        RenderedApplication& output,
        std::string* error = nullptr);

private:
    class Impl;
    explicit JsApplication(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vellum::authoring
