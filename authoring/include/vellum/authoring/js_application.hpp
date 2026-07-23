#pragma once

#include <vellum/graphics/scene.hpp>

#include <memory>
#include <cstddef>
#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace vellum::authoring {

inline constexpr std::string_view kAuthoringHostProtocol =
    "vellum.authoring-host.v1";
inline constexpr std::string_view kAsyncAuthoringHostProtocol =
    "vellum.authoring-host.v2";

struct PumpResult final {
    bool rendered = false;
    bool idle = true;
    std::size_t tasks_executed = 0;
};

struct Interaction final {
    std::string node_id;
    std::string event;
    std::string action;
    vellum::graphics::Rect bounds;
};

/// The exact controlled TextInput v1 surface exposed to a native host.
/// Empty optional action strings mean that event was not authored.
struct TextInputControl final {
    std::string node_id;
    std::string value;
    std::string placeholder;
    std::string change_action;
    std::string submit_action;
    std::string key_down_action;
    vellum::graphics::Rect bounds;
};

struct RenderedApplication final {
    vellum::graphics::Scene scene;
    std::vector<Interaction> interactions;
    std::vector<TextInputControl> text_inputs;
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
    /// Advances the deterministic native timer clock, settles ready JavaScript
    /// work, and materializes a dirty v2 bridge tree.
    [[nodiscard]] bool pump(
        std::uint64_t advance_milliseconds,
        std::size_t maximum_tasks,
        RenderedApplication& output,
        PumpResult& result,
        std::string* error = nullptr);
    /// Advances to each next timer deadline until no work remains.
    [[nodiscard]] bool wait_for_idle(
        std::size_t maximum_tasks,
        RenderedApplication& output,
        PumpResult& result,
        std::string* error = nullptr);
    /// Returns the last versioned authoring-host diagnostic emitted for a
    /// JavaScript exception. The value is empty before the first exception.
    [[nodiscard]] std::string last_diagnostic_json() const;

private:
    class Impl;
    explicit JsApplication(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vellum::authoring
