// Shared helpers for the native materializer tests: scratch directories, asset
// files, draw-command lookups and the check macros the ported cases use.
#pragma once

#include "framework_test.hpp"

#include <choc/text/choc_JSON.h>
#include <vellum/canvas/bundled_fonts.hpp>
#include <vellum/canvas/canvas.hpp>
#include <vellum/runtime/base64.hpp>
#include <vellum/state/store.hpp>
#include <vellum/view/buttons.hpp>
#include <vellum/view/control_painters.hpp>
#include <vellum/view/css_gradient.hpp>
#include <vellum/view/design_codegen.hpp>
#include <vellum/view/design_frame_view.hpp>
#include <vellum/view/design_import.hpp>
#include <vellum/view/design_sources.hpp>
#include <vellum/view/gap_widgets.hpp>
#include <vellum/view/input_events.hpp>
#include <vellum/view/layout_snapshot.hpp>
#include <vellum/view/screenshot.hpp>
#include <vellum/view/screenshot_compare.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/svg_path_widget.hpp>
#include <vellum/view/text_editor.hpp>
#include <vellum/view/ui_components.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>
#include <vellum/view/widgets.hpp>

#include <atomic>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <limits>
#include <map>
#include <memory>
#include <regex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace fs = std::filesystem;
using namespace vellum::view;

// A failed prerequisite aborts the case before the test uses its result.
#define require(expression) \
    do { \
        if (!(expression)) throw std::runtime_error("Required: " #expression); \
    } while (false)

#define CHECK_NOT(expression) CHECK(!(expression))

// A case that needs an optional backend reports a skip and passes.
#define SKIP(message) \
    do { \
        std::printf("SKIP %s\n", message); \
        return; \
    } while (false)

namespace framework_test {

// Float comparison with a relative tolerance, optionally widened by a margin.
class Approx {
public:
    explicit Approx(double value) : value_(value) {}
    Approx& margin(double margin) {
        margin_ = margin;
        return *this;
    }
    friend bool operator==(double actual, const Approx& expected) { return expected.matches(actual); }
    friend bool operator==(const Approx& expected, double actual) { return expected.matches(actual); }
    friend bool operator!=(double actual, const Approx& expected) { return !expected.matches(actual); }
    friend bool operator!=(const Approx& expected, double actual) { return !expected.matches(actual); }

private:
    bool matches(double actual) const {
        const double epsilon = std::numeric_limits<float>::epsilon() * 100.0;
        const double difference = std::fabs(actual - value_);
        return difference <= margin_ || difference <= epsilon * std::fabs(value_);
    }
    double value_;
    double margin_ = 0.0;
};

}  // namespace framework_test

using framework_test::Approx;

namespace vellum::test {

inline fs::path make_unique_temp_dir(const std::string& prefix) {
    static std::atomic<unsigned> counter{0};
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
    fs::path path = fs::temp_directory_path() /
        (prefix + "-" + std::to_string(stamp) + "-" + std::to_string(counter++));
    fs::create_directories(path);
    return path;
}

}  // namespace vellum::test

namespace materializer_test {

class TempDir {
public:
    explicit TempDir(const std::string& prefix) {
        path = vellum::test::make_unique_temp_dir(prefix);
    }

    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }

    fs::path path;
};

class ScopedCurrentPath {
public:
    explicit ScopedCurrentPath(const fs::path& path)
        : original_(fs::current_path()) {
        fs::current_path(path);
    }
    ~ScopedCurrentPath() {
        std::error_code ec;
        fs::current_path(original_, ec);
    }

private:
    fs::path original_;
};

// Real bytes on disk for a manifest asset. The materializer only emits a
// `file://` image source for a file that exists, a synthetic path resolves to
// nothing and materializes the unresolved-asset placeholder instead, so a test
// that asserts on a resolved image has to put a file where the manifest says.
fs::path write_asset_file(const fs::path& path) {
    fs::create_directories(path.parent_path());
    std::ofstream out(path, std::ios::binary);
    require(out.is_open());
    out << "test-asset-bytes";
    require(out.good());
    return path;
}

// [[maybe_unused]]: only the codegen-compile test cases use these helpers, and
// those compile legs are skipped on Windows (see the SKIP guards below), so the
// helpers are unreferenced in a Windows build.
[[maybe_unused]] void write_text(const fs::path& path, const std::string& text) {
    fs::create_directories(path.parent_path());
    std::ofstream out(path);
    require(out.is_open());
    out << text;
    require(out.good());
}

const vellum::canvas::DrawCommand* first_meter_fill_rect(const vellum::canvas::RecordingCanvas& canvas) {
    for (const auto& command : canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::fill_rect)
            return &command;
    }
    return nullptr;
}

const vellum::canvas::DrawCommand* stroke_color_for_first_line(
    const vellum::canvas::RecordingCanvas& canvas) {
    const vellum::canvas::DrawCommand* current_color = nullptr;
    for (const auto& command : canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::set_stroke_color)
            current_color = &command;
        if (command.type == vellum::canvas::DrawCommand::Type::stroke_line)
            return current_color;
    }
    return nullptr;
}

const vellum::canvas::DrawCommand* stroke_color_for_last_line(
    const vellum::canvas::RecordingCanvas& canvas) {
    const vellum::canvas::DrawCommand* current_color = nullptr;
    const vellum::canvas::DrawCommand* painted_color = nullptr;
    for (const auto& command : canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::set_stroke_color)
            current_color = &command;
        if (command.type == vellum::canvas::DrawCommand::Type::stroke_line)
            painted_color = current_color;
    }
    return painted_color;
}

const vellum::canvas::DrawCommand* fill_color_for_last_rounded_rect(
    const vellum::canvas::RecordingCanvas& canvas) {
    const vellum::canvas::DrawCommand* current_color = nullptr;
    const vellum::canvas::DrawCommand* painted_color = nullptr;
    for (const auto& command : canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::set_fill_color)
            current_color = &command;
        if (command.type == vellum::canvas::DrawCommand::Type::fill_rounded_rect)
            painted_color = current_color;
    }
    return painted_color;
}

std::string minimal_live_react_shim() {
    return R"JS(
(function() {
  function flatten(input, out) {
    if (Array.isArray(input)) {
      for (var i = 0; i < input.length; i++) flatten(input[i], out);
    } else if (input !== null && input !== undefined && input !== false && input !== true) {
      out.push(input);
    }
  }

  function createElement(type, props) {
    var children = [];
    for (var i = 2; i < arguments.length; i++) flatten(arguments[i], children);
    return { type: type, props: props || {}, children: children };
  }

  function cssValue(key, value) {
    if (value == null) return "";
    if (typeof value === "number") {
      if (key === "flexGrow" || key === "flexShrink" || key === "opacity" ||
          key === "zIndex" || key === "lineHeight") {
        return String(value);
      }
      return String(value) + "px";
    }
    return String(value);
  }

  function applyProps(el, props) {
    props = props || {};
    for (var key in props) {
      if (key === "children" || key === "key") continue;
      var value = props[key];
      if (key === "style" && value) {
        for (var styleKey in value) el.style[styleKey] = cssValue(styleKey, value[styleKey]);
      } else if (key === "id") {
        el.id = String(value);
        el.setAttribute("id", String(value));
      } else if (value !== false && value != null) {
        el.setAttribute(key, String(value));
      }
    }
  }

  function renderNode(node) {
    if (node == null || node === false || node === true) return null;
    if (typeof node === "string" || typeof node === "number") {
      return document.createTextNode(String(node));
    }
    if (typeof node.type === "function") {
      var props = Object.assign({}, node.props || {});
      props.children = node.children;
      return renderNode(node.type(props));
    }
    var el = document.createElement(node.type);
    applyProps(el, node.props);
    var scalarText = "";
    var nonScalarChildren = [];
    for (var i = 0; i < node.children.length; i++) {
      var child = node.children[i];
      if (typeof child === "string" || typeof child === "number") {
        scalarText += String(child);
      } else {
        nonScalarChildren.push(child);
      }
    }
    if (scalarText) el.textContent = scalarText;
    for (var j = 0; j < nonScalarChildren.length; j++) {
      var rendered = renderNode(nonScalarChildren[j]);
      if (rendered) el.appendChild(rendered);
    }
    return el;
  }

  globalThis.React = { createElement: createElement };
  globalThis.ReactDOM = {
    createRoot: function(mount) {
      return {
        render: function(element) {
          var node = renderNode(element);
          if (node) mount.appendChild(node);
        }
      };
    },
    flushSync: function(fn) { return fn(); }
  };
})();
)JS";
}

std::unique_ptr<View> build_live_plugin_panel() {
    auto root = std::make_unique<View>();
    root->set_bounds({0, 0, 360, 160});

    ScriptEngine engine;
    vellum::state::StateStore store;
    WidgetBridge bridge(engine, *root, store);

    bridge.load_script(minimal_live_react_shim());
    bridge.load_script(R"JS(
function Control(props) {
  return React.createElement(
    'div',
    {
      style: {
        display: 'flex',
        flexDirection: 'column',
        width: 104,
        height: 90,
        padding: 6,
        gap: 4
      }
    },
    React.createElement('span', { style: { width: 92, height: 20 } }, props.label),
    React.createElement('span', { style: { width: 92, height: 20 } }, props.value)
  );
}

function Panel() {
  return React.createElement(
    'div',
    {
      id: 'phase-four-plugin-panel',
      style: {
        display: 'flex',
        flexDirection: 'column',
        width: 360,
        height: 160,
        padding: 12,
        gap: 10,
        overflow: 'hidden'
      }
    },
    React.createElement(
      'div',
      {
        style: {
          display: 'flex',
          flexDirection: 'row',
          width: 336,
          height: 24,
          gap: 96
        }
      },
      React.createElement('span', { style: { width: 160, height: 24 } }, 'Cloud Chorus'),
      React.createElement('span', { style: { width: 80, height: 24 } }, '-12 dB')
    ),
    React.createElement(
      'div',
      {
        style: {
          display: 'flex',
          flexDirection: 'row',
          width: 336,
          height: 90,
          gap: 12
        }
      },
      React.createElement(Control, { label: 'Depth', value: '67%' }),
      React.createElement(Control, { label: 'Rate', value: '1.8 Hz' }),
      React.createElement(Control, { label: 'Mix', value: '42%' })
    )
  );
}

ReactDOM.createRoot(document.body).render(React.createElement(Panel));
layout();
)JS");
    root->layout_children();
    return root;
}

IRNode frame(std::string id, float width, float height, LayoutDirection direction) {
    IRNode node;
    node.type = "frame";
    node.stable_anchor_id = std::move(id);
    node.style.width = width;
    node.style.height = height;
    node.layout.direction = direction;
    return node;
}

IRNode label(std::string id, std::string text, float width, float height) {
    IRNode node;
    node.type = "text";
    node.stable_anchor_id = std::move(id);
    node.text_content = std::move(text);
    node.style.width = width;
    node.style.height = height;
    return node;
}

IRNode control_from_live(const View& card, std::string label_text, std::string value_text) {
    require(card.child_count() == 2);
    auto node = frame(card.id(), 104.0f, 90.0f, LayoutDirection::column);
    node.layout.padding_top = 6.0f;
    node.layout.padding_right = 6.0f;
    node.layout.padding_bottom = 6.0f;
    node.layout.padding_left = 6.0f;
    node.layout.gap = 4.0f;
    node.children.push_back(label(card.child_at(0)->id(), std::move(label_text), 92.0f, 20.0f));
    node.children.push_back(label(card.child_at(1)->id(), std::move(value_text), 92.0f, 20.0f));
    return node;
}

DesignIR build_plugin_panel_ir_from_live(const View& live_root) {
    require(live_root.child_count() == 1);
    const auto* panel = live_root.child_at(0);
    require(panel != nullptr);
    require(panel->child_count() == 2);
    const auto* header = panel->child_at(0);
    const auto* controls = panel->child_at(1);
    require(header != nullptr);
    require(controls != nullptr);
    require(header->child_count() == 2);
    require(controls->child_count() == 3);

    // This IR is the baked snapshot of the same JSX fixture rendered by
    // build_live_plugin_panel(). The parity oracle compares native View IDs,
    // so the baked fixture mirrors the bridge-assigned live IDs.
    DesignIR ir;
    ir.source = DesignSource::jsx;
    ir.source_adapter = "live-react-fixture";
    ir.root = frame(panel->id(), 360.0f, 160.0f, LayoutDirection::column);
    ir.root.layout.padding_top = 12.0f;
    ir.root.layout.padding_right = 12.0f;
    ir.root.layout.padding_bottom = 12.0f;
    ir.root.layout.padding_left = 12.0f;
    ir.root.layout.gap = 10.0f;
    ir.root.style.overflow = "hidden";

    auto header_ir = frame(header->id(), 336.0f, 24.0f, LayoutDirection::row);
    header_ir.layout.gap = 96.0f;
    header_ir.children.push_back(label(header->child_at(0)->id(), "Cloud Chorus", 160.0f, 24.0f));
    header_ir.children.push_back(label(header->child_at(1)->id(), "-12 dB", 80.0f, 24.0f));
    ir.root.children.push_back(std::move(header_ir));

    auto controls_ir = frame(controls->id(), 336.0f, 90.0f, LayoutDirection::row);
    controls_ir.layout.gap = 12.0f;
    controls_ir.children.push_back(control_from_live(*controls->child_at(0), "Depth", "67%"));
    controls_ir.children.push_back(control_from_live(*controls->child_at(1), "Rate", "1.8 Hz"));
    controls_ir.children.push_back(control_from_live(*controls->child_at(2), "Mix", "42%"));
    ir.root.children.push_back(std::move(controls_ir));

    return ir;
}

std::string diff_messages(const LayoutTreeDiff& diff) {
    std::ostringstream out;
    for (const auto& message : diff.messages) out << message << '\n';
    return out.str();
}

bool diagnostics_contain(const std::vector<ImportDiagnostic>& diagnostics,
                         std::string_view code) {
    for (const auto& diagnostic : diagnostics) {
        if (diagnostic.code == code) return true;
    }
    return false;
}

std::size_t diagnostics_count(const std::vector<ImportDiagnostic>& diagnostics,
                              std::string_view code) {
    std::size_t count = 0;
    for (const auto& diagnostic : diagnostics) {
        if (diagnostic.code == code) ++count;
    }
    return count;
}

struct TextClipFailure {
    std::string id;
    std::string text;
    float measured_width = 0.0f;
    float node_width = 0.0f;
    float clip_width = 0.0f;
};

Rect intersect_text_clip_rect(Rect a, Rect b) {
    const float right = std::min(a.x + a.width, b.x + b.width);
    const float bottom = std::min(a.y + a.height, b.y + b.height);
    a.x = std::max(a.x, b.x);
    a.y = std::max(a.y, b.y);
    a.width = std::max(0.0f, right - a.x);
    a.height = std::max(0.0f, bottom - a.y);
    return a;
}

void collect_text_clip_failures(const View& view, Rect parent_abs, Rect inherited_clip,
                                std::vector<TextClipFailure>& failures) {
    const auto bounds = view.bounds();
    const Rect abs{parent_abs.x + bounds.x, parent_abs.y + bounds.y, bounds.width, bounds.height};
    const Rect clip =
        view.overflow() == View::Overflow::hidden || view.overflow() == View::Overflow::scroll
            ? intersect_text_clip_rect(inherited_clip, abs)
            : inherited_clip;
    if (const auto* label = dynamic_cast<const Label*>(&view)) {
        // `intrinsic_width()` is intentionally zero for a soft-wrapping
        // label. Use the max-content/painted extent instead so this invariant
        // remains useful for both single-line segmented captions and wrapped
        // imported copy.
        float measured = label->max_content_width();
        const auto painted = label->painted_text_extents(std::max(abs.width, 1.0f));
        if (painted.measured)
            measured = std::max(measured, painted.width);
        Rect own_clip = clip;
        if (const auto& ancestor = label->ancestor_clip_rect()) {
            // The importer stores this rectangle in the label's local space;
            // translate it before intersecting with the inherited clip.
            own_clip = intersect_text_clip_rect(own_clip, {abs.x + ancestor->x, abs.y + ancestor->y,
                                                           ancestor->width, ancestor->height});
        }
        // A zero (or narrower-than-ink) solved box under a tiny inherited clip
        // is the exact failure seen in the Spectr header: the text shaper has
        // a useful width, while Yoga/native placement leaves the label inside
        // a 2px separator clip. Keep this checker independent of the native
        // analyzer so a planted failure remains observable.
        if (!label->text().empty() && measured > 0.0f &&
            (abs.width + 0.01f < measured || own_clip.width + 0.01f < measured) &&
            own_clip.width <= 2.01f) {
            failures.push_back({label->id(), label->text(), measured, abs.width, own_clip.width});
        }
    }
    for (auto* child : view.sorted_children_by_z_index())
        collect_text_clip_failures(*child, abs, clip, failures);
}

std::vector<TextClipFailure> text_clip_failures(const View& root) {
    const auto root_bounds = root.bounds();
    const Rect root_abs{root_bounds.x, root_bounds.y, root_bounds.width, root_bounds.height};
    return [&] {
        std::vector<TextClipFailure> failures;
        collect_text_clip_failures(root, {0, 0, 0, 0}, root_abs, failures);
        return failures;
    }();
}

struct BoundKnobDescriptor {
    std::string route_id;
    std::string param_key;
    std::string binding_module;
    std::string binding_param;
    std::string event_contract;
    std::string gesture_contract;
};

struct BoundCheckboxDescriptor {
    std::string route_id;
    std::string param_key;
    std::string binding_module;
    std::string binding_param;
    std::string event_contract;
    std::string gesture_contract;
};

struct BoundTextDescriptor {
    std::string route_id;
    std::string value_key;
    std::string initial_value;
    std::string placeholder;
    std::string event_contract;
    std::string focus_contract;
};

class BindingBackedKnobContext final : public NativeImportBindingContext {
public:
    BindingBackedKnobContext() {
        store_.set_gesture_callbacks(
            [this](vellum::state::ParamID) { ++gesture_begin_count; },
            [this](vellum::state::ParamID) { ++gesture_end_count; });
    }

    void bind_knob(Knob& knob, const NativeImportBindingDescriptor& descriptor) override {
        bound_knobs.push_back(BoundKnobDescriptor{
            .route_id = std::string(descriptor.route_id),
            .param_key = std::string(descriptor.param_key),
            .binding_module = std::string(descriptor.binding_module),
            .binding_param = std::string(descriptor.binding_param),
            .event_contract = std::string(descriptor.event_contract),
            .gesture_contract = std::string(descriptor.gesture_contract)});

        vellum::state::ParamInfo info;
        info.id = param_id_;
        info.name = std::string(descriptor.param_key);
        info.range = {0.0f, 1.0f, knob.value()};
        store_.add_parameter(info);
        store_.set_normalized(param_id_, knob.value());

        knob.on_gesture_begin = [this] {
            store_.begin_gesture(param_id_);
        };
        knob.on_change = [this](float normalized) {
            store_.set_normalized(param_id_, normalized);
            changes.push_back(normalized);
        };
        knob.on_gesture_end = [this] {
            store_.end_gesture(param_id_);
        };
    }

    void bind_checkbox(Checkbox& checkbox, const NativeImportBindingDescriptor& descriptor) override {
        bound_checkboxes.push_back(BoundCheckboxDescriptor{
            .route_id = std::string(descriptor.route_id),
            .param_key = std::string(descriptor.param_key),
            .binding_module = std::string(descriptor.binding_module),
            .binding_param = std::string(descriptor.binding_param),
            .event_contract = std::string(descriptor.event_contract),
            .gesture_contract = std::string(descriptor.gesture_contract)});

        const auto param_key = std::string(descriptor.param_key);
        checkbox.on_change = [this, param_key](bool checked) {
            checkbox_changes.push_back({param_key, checked ? 1.0f : 0.0f});
        };
    }

    void bind_text_editor(TextEditor& editor, const NativeImportTextBindingDescriptor& descriptor) override {
        bound_text_editors.push_back(BoundTextDescriptor{
            .route_id = std::string(descriptor.route_id),
            .value_key = std::string(descriptor.value_key),
            .initial_value = std::string(descriptor.initial_value),
            .placeholder = std::string(descriptor.placeholder),
            .event_contract = std::string(descriptor.event_contract),
            .focus_contract = std::string(descriptor.focus_contract)});

        editor.on_change = [this](const std::string& text) {
            text_changes.push_back(text);
        };
    }

    float normalized_value() const {
        return store_.get_normalized(param_id_);
    }

    std::vector<BoundKnobDescriptor> bound_knobs;
    std::vector<BoundCheckboxDescriptor> bound_checkboxes;
    std::vector<BoundTextDescriptor> bound_text_editors;
    std::vector<float> changes;
    std::vector<std::pair<std::string, float>> checkbox_changes;
    std::vector<std::string> text_changes;
    int gesture_begin_count = 0;
    int gesture_end_count = 0;

private:
    vellum::state::StateStore store_;
    vellum::state::ParamID param_id_ = 1;
};

}  // namespace materializer_test

using namespace materializer_test;
