#pragma once

#include <vellum/graphics/lowering_diagnostic.hpp>
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
    std::string selection_change_action;
    std::string composition_start_action;
    std::string composition_update_action;
    std::string composition_end_action;
    /// UTF-16 code-unit offsets, matching JavaScript String and platform APIs.
    std::size_t selection_start = 0;
    std::size_t selection_end = 0;
    vellum::graphics::Rect bounds;
};

struct AccessibilityState final {
    bool disabled = false;
    bool selected = false;
    bool checked = false;
    bool mixed = false;
    bool expanded = false;
    bool has_checked = false;
    bool has_expanded = false;
};

/// A deterministic semantic projection of the retained tree. Platform hosts
/// expose these nodes through their native accessibility APIs; the browser host
/// mirrors the same projection into an explicit DOM accessibility overlay.
struct AccessibilityNode final {
    std::string node_id;
    std::string role;
    std::string label;
    std::string value;
    AccessibilityState state;
    std::vector<std::string> actions;
    vellum::graphics::Rect bounds;
};

/// Where an authored node came from, when the authoring tree says so. `file`
/// is empty and `line` and `column` are zero when the node recorded no source.
struct NodeSource final {
    std::string file;
    int line = 0;
    int column = 0;
    /// A stable identity the design the node was imported from gave it, such
    /// as `figma:<node id>`. Empty when the node carries none.
    std::string anchor;
};

/// One authored node with the absolute box it was laid out into, in document
/// order. `box` is the layout box before the node's own transform.
struct NodeRecord final {
    std::string node_id;
    std::string type;
    vellum::graphics::Rect box;
    NodeSource source;
};

struct RenderedApplication final {
    vellum::graphics::Scene scene;
    /// Every authored node in document order with its source and laid-out box.
    std::vector<NodeRecord> nodes;
    std::vector<Interaction> interactions;
    std::vector<TextInputControl> text_inputs;
    std::vector<AccessibilityNode> accessibility_nodes;
    /// Authored content the materializer could not carry into `scene`
    /// exactly, in document order. Only warnings and infos appear here, since
    /// an error fails materialization and leaves the previous output intact.
    std::vector<vellum::graphics::LoweringDiagnostic> diagnostics;
};

/// Serializes the authored nodes of a rendered tree as the
/// `vellum.tree-dump.v1` JSON document: per node its id, type, laid-out box,
/// and source location and anchor when the authoring tree recorded them.
[[nodiscard]] std::string tree_dump_json(const RenderedApplication& rendered);

struct JsApplicationOptions final {
    /// Replaces `Date.now()` and `performance.now()` with the native timer
    /// clock, which moves only when the host pumps it, so script-visible time
    /// is a function of the pump calls alone.
    bool manual_clock = false;
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
    static std::unique_ptr<JsApplication> create(
        std::string_view bundle,
        const JsApplicationOptions& options,
        std::string* error = nullptr);

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
    [[nodiscard]] bool configure_service_host(
        std::string_view capabilities_json,
        std::string* error = nullptr);
    [[nodiscard]] bool enqueue_service_response(
        std::string_view response_json,
        std::string* error = nullptr);
    [[nodiscard]] bool service_response_queue_empty(
        bool& empty,
        std::string* error = nullptr);
    [[nodiscard]] bool has_command(
        std::string_view command,
        bool& present,
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
    /// The native timer clock in milliseconds since creation.
    [[nodiscard]] std::uint64_t clock_milliseconds() const;
    /// How many times script read `Date.now()` or `performance.now()` through
    /// the manual clock. Zero when the manual clock is off.
    [[nodiscard]] std::uint64_t manual_clock_reads() const;
    /// Returns the last versioned authoring-host diagnostic emitted for a
    /// JavaScript exception. The value is empty before the first exception.
    [[nodiscard]] std::string last_diagnostic_json() const;

private:
    class Impl;
    explicit JsApplication(std::unique_ptr<Impl> impl) noexcept;
    std::unique_ptr<Impl> impl_;
};

}  // namespace vellum::authoring
