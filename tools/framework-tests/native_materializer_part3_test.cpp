// Native materializer behavior, part 3: the baked DesignIR lane that turns a
// captured design into a view tree.

#include "native_materializer_support.hpp"

FRAMEWORK_TEST(native_materializer_binding_helper_binds_routed_checkbox_metadata) {
    DesignIR ir;
    ir.root = frame("root", 120.0f, 80.0f, LayoutDirection::column);

    auto checkbox_node = frame("bypass-checkbox", 32.0f, 32.0f, LayoutDirection::column);
    checkbox_node.type = "input";
    checkbox_node.stable_anchor_id = "figma:bypass-checkbox";
    checkbox_node.attributes["type"] = "checkbox";
    checkbox_node.attributes["checked"] = "true";
    checkbox_node.attributes["pulpRouteId"] = "figma-plugin:bypass";
    checkbox_node.attributes["pulpParamKey"] = "filter.bypass";
    checkbox_node.attributes["pulpBindingModule"] = "filter";
    checkbox_node.attributes["pulpBindingParam"] = "bypass";
    checkbox_node.attributes["pulpEventContract"] = "onChange:set_param:filter.bypass";
    checkbox_node.attributes["pulpGestureContract"] = "click:toggle";
    ir.root.children.push_back(std::move(checkbox_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 1);

    auto* checkbox = dynamic_cast<Checkbox*>(root->child_at(0));
    require(checkbox != nullptr);
    require(checkbox->is_checked());

    BindingBackedKnobContext binding;
    std::vector<ImportDiagnostic> diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &diagnostics});

    require(binding.bound_checkboxes.size() == 1);
    require(binding.bound_checkboxes[0].route_id == "figma-plugin:bypass");
    require(binding.bound_checkboxes[0].param_key == "filter.bypass");
    require(binding.bound_checkboxes[0].binding_module == "filter");
    require(binding.bound_checkboxes[0].binding_param == "bypass");
    require(binding.bound_checkboxes[0].event_contract == "onChange:set_param:filter.bypass");
    require(binding.bound_checkboxes[0].gesture_contract == "click:toggle");
    require(diagnostics.empty());

    checkbox->on_mouse_down({16.0f, 16.0f});
    require(!checkbox->is_checked());
    require(binding.checkbox_changes.size() == 1);
    require(binding.checkbox_changes[0].first == "filter.bypass");
    require(binding.checkbox_changes[0].second == 0.0f);
}

FRAMEWORK_TEST(native_materializer_binding_helper_skips_repeated_calls_for_the_same_context_and_view) {
    DesignIR ir;
    ir.root = frame("root", 160.0f, 80.0f, LayoutDirection::column);

    auto checkbox_node = frame("bypass-checkbox", 32.0f, 32.0f, LayoutDirection::column);
    checkbox_node.type = "input";
    checkbox_node.stable_anchor_id = "figma:bypass-checkbox";
    checkbox_node.attributes["type"] = "checkbox";
    checkbox_node.attributes["pulpRouteId"] = "figma-plugin:bypass";
    checkbox_node.attributes["pulpParamKey"] = "filter.bypass";
    ir.root.children.push_back(std::move(checkbox_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);

    BindingBackedKnobContext binding;
    std::vector<ImportDiagnostic> first_diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &first_diagnostics});
    require(binding.bound_checkboxes.size() == 1);
    require(first_diagnostics.empty());

    std::vector<ImportDiagnostic> second_diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &second_diagnostics});
    require(binding.bound_checkboxes.size() == 1);
    require(diagnostics_count(second_diagnostics, "native-binding-already-applied") == 1);

    const auto first_checkbox_id = root->child_at(0)->import_binding_instance_id();
    root.reset();

    auto rebuilt_root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(rebuilt_root != nullptr);
    require(rebuilt_root->child_at(0)->import_binding_instance_id() != first_checkbox_id);

    std::vector<ImportDiagnostic> rebuilt_diagnostics;
    bind_native_view_tree(*rebuilt_root, ir, binding, {.diagnostics_out = &rebuilt_diagnostics});
    require(binding.bound_checkboxes.size() == 2);
    require(rebuilt_diagnostics.empty());

    BindingBackedKnobContext fresh_binding;
    std::vector<ImportDiagnostic> fresh_diagnostics;
    bind_native_view_tree(*rebuilt_root, ir, fresh_binding, {.diagnostics_out = &fresh_diagnostics});
    require(fresh_binding.bound_checkboxes.size() == 1);
    require(fresh_diagnostics.empty());
}

FRAMEWORK_TEST(native_materializer_binding_helper_ignores_non_binding_metadata) {
    DesignIR ir;
    ir.root = frame("root", 220.0f, 140.0f, LayoutDirection::column);

    auto route_only = frame("route-only", 80.0f, 40.0f, LayoutDirection::column);
    route_only.stable_anchor_id = "figma:route-only";
    route_only.attributes["pulpRouteId"] = "figma-plugin:route-only";
    route_only.attributes["pulpSourceFamily"] = "static-frame";

    auto initial_value_only = frame("initial-only-editor", 120.0f, 24.0f, LayoutDirection::column);
    initial_value_only.type = "input";
    initial_value_only.attributes["type"] = "text";
    initial_value_only.attributes["pulpInitialValue"] = "Init";

    ir.root.children.push_back(std::move(route_only));
    ir.root.children.push_back(std::move(initial_value_only));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);

    BindingBackedKnobContext binding;
    std::vector<ImportDiagnostic> diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &diagnostics});

    require(binding.bound_knobs.empty());
    require(diagnostics.empty());
}

FRAMEWORK_TEST(native_materializer_binding_helper_binds_routed_initial_value_text_editors) {
    DesignIR ir;
    ir.root = frame("root", 160.0f, 80.0f, LayoutDirection::column);

    auto editor_node = frame("preset-name", 140.0f, 24.0f, LayoutDirection::column);
    editor_node.type = "input";
    editor_node.stable_anchor_id = "figma:preset-name";
    editor_node.attributes["type"] = "text";
    editor_node.attributes["pulpRouteId"] = "figma-plugin:preset-name";
    editor_node.attributes["pulpInitialValue"] = "Init";
    editor_node.attributes["pulpPlaceholder"] = "Preset";
    editor_node.attributes["pulpEventContract"] = "input:onChange:setState";
    editor_node.attributes["pulpFocusContract"] = "input:focus";
    ir.root.children.push_back(std::move(editor_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 1);

    auto* editor = dynamic_cast<TextEditor*>(root->child_at(0));
    require(editor != nullptr);
    require(editor->text() == "Init");

    BindingBackedKnobContext binding;
    std::vector<ImportDiagnostic> diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &diagnostics});

    require(binding.bound_text_editors.size() == 1);
    require(binding.bound_text_editors[0].route_id == "figma-plugin:preset-name");
    require(binding.bound_text_editors[0].value_key.empty());
    require(binding.bound_text_editors[0].initial_value == "Init");
    require(binding.bound_text_editors[0].placeholder == "Preset");
    require(binding.bound_text_editors[0].event_contract == "input:onChange:setState");
    require(binding.bound_text_editors[0].focus_contract == "input:focus");
    require(diagnostics.empty());

    editor->set_text("Edited");
    require(binding.text_changes.size() == 1);
    require(binding.text_changes[0] == "Edited");
}

FRAMEWORK_TEST(baked_native_materializer_lets_explicit_hit_test_metadata_override_promoted_widget_defaults) {
    DesignIR ir;
    ir.root = frame("root", 120.0f, 120.0f, LayoutDirection::column);

    auto knob_node = frame("gain-knob", 80.0f, 80.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.audio_label = "Gain";

    auto child = frame("explicit-child", 80.0f, 80.0f, LayoutDirection::column);
    child.attributes["pulpHitTestable"] = "true";
    knob_node.children.push_back(std::move(child));
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);

    root->set_bounds({0, 0, 120.0f, 120.0f});
    root->layout_children();

    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    require(knob->child_count() == 1);
    require(knob->child_at(0)->hit_testable());

    auto* hit = root->hit_test({40.0f, 40.0f});
    require(hit == knob->child_at(0));
}

FRAMEWORK_TEST(baked_native_materializer_preserves_interactive_descendants_under_promoted_widgets) {
    DesignIR ir;
    ir.root = frame("root", 120.0f, 120.0f, LayoutDirection::column);

    auto knob_node = frame("gain-knob", 80.0f, 80.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.audio_label = "Gain";

    auto container = frame("interactive-container", 80.0f, 40.0f, LayoutDirection::column);
    auto button_node = frame("nested-fine-button", 60.0f, 20.0f, LayoutDirection::column);
    button_node.type = "button";
    button_node.text_content = "Fine";
    container.children.push_back(std::move(button_node));
    knob_node.children.push_back(std::move(container));
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);

    root->set_bounds({0, 0, 120.0f, 120.0f});
    root->layout_children();

    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    require(knob->child_count() == 1);
    auto* container_view = knob->child_at(0);
    require(container_view != nullptr);
    require(container_view->hit_testable());
    require(container_view->pointer_events() == View::PointerEvents::box_none);
    require(container_view->child_count() == 1);
    auto* button_view = dynamic_cast<TextButton*>(container_view->child_at(0));
    require(button_view != nullptr);
    require(button_view->hit_testable());

    auto* button_hit = root->hit_test({30.0f, 10.0f});
    require(button_hit == button_view);

    auto* body_hit = root->hit_test({70.0f, 30.0f});
    require(body_hit == knob);
}

FRAMEWORK_TEST(baked_native_materializer_honors_explicit_hit_test_metadata) {
    DesignIR ir;
    ir.root = frame("root", 120.0f, 120.0f, LayoutDirection::column);

    auto decorative = frame("decorative-layer", 80.0f, 80.0f, LayoutDirection::column);
    decorative.attributes["pulpHitTestable"] = "false";
    ir.root.children.push_back(std::move(decorative));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 1);

    root->set_bounds({0, 0, 120.0f, 120.0f});
    root->layout_children();

    auto* child = root->child_at(0);
    require(child != nullptr);
    require(!child->hit_testable());

    auto* hit = root->hit_test({40.0f, 40.0f});
    require(hit == root.get());
}

FRAMEWORK_TEST(baked_native_materializer_only_treats_display_text_as_editor_value_for_textarea) {
    // Regression: the text_value fallback used incidental node text for any
    // editor, so a label/heading captured as text_content was injected as the
    // editor's contents. Only a <textarea> body is genuinely the value.
    DesignIR ir;
    ir.root = frame("root", 200.0f, 120.0f, LayoutDirection::column);

    // <input type=text> with incidental label text, no explicit value.
    auto input_node = frame("name-input", 96.0f, 24.0f, LayoutDirection::column);
    input_node.type = "input";
    input_node.attributes["type"] = "text";
    input_node.text_content = "Preset Name";

    // <textarea> whose body IS the value.
    auto area_node = frame("notes", 120.0f, 60.0f, LayoutDirection::column);
    area_node.type = "textarea";
    area_node.attributes["pulpSourceFamily"] = "textarea";
    area_node.text_content = "hello world";

    ir.root.children.push_back(std::move(input_node));
    ir.root.children.push_back(std::move(area_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 2);

    auto* input_editor = dynamic_cast<TextEditor*>(root->child_at(0));
    require(input_editor != nullptr);
    require(input_editor->text().empty());

    auto* area_editor = dynamic_cast<TextEditor*>(root->child_at(1));
    require(area_editor != nullptr);
    require(area_editor->text() == "hello world");
}


FRAMEWORK_TEST(baked_native_materializer_makes_a_faithful_svg_tab_group_an_interactive_overlay) {
    // End-to-end codification of the design-import tab-group contract, the exact
    // path the standalone host runs (parse IR -> build_native_view_tree). A
    // faithful_svg node carrying a typed tab_group element must materialize to a
    // LIVE, clickable DesignTabGroup whose selection pill sits on the design's
    // selected cell, AND the design's BAKED selected-tab highlight must be
    // suppressed so only the live pill shows (no double-pill). Design-agnostic: a
    // synthetic 4-slot strip, not any one source file.
    //
    // The tab group spans x=20,w=56 over a 4-slot strip (slot_w=14). selected=2,
    // so the design's baked highlight is the slot-2 rect at x=48,w=14; the
    // materializer must strip it and stand up a live pill in its place.
    const std::string svg =
        R"(<svg width="80" height="80" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="6" y="6" width="68" height="68" rx="4" fill="#1c1d1d"/>)"
        R"(<rect x="20" y="14" width="56" height="14" rx="2" fill="#252626"/>)"
        R"(<rect x="48" y="14" width="14" height="14" rx="2" fill="#3c3d3d"/>)"
        R"(</svg>)";

    DesignIR ir;
    ir.source = DesignSource::figma_plugin;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "frame-svg";

    IRInteractiveElement tg;
    tg.kind = InteractiveElementKind::tab_group;
    tg.x = 20; tg.y = 14; tg.w = 56; tg.h = 14;
    tg.options = {"1", "2", "3", "4"};
    tg.selected_index = 2;
    ir.root.interactive_elements.push_back(tg);

    IRAssetRef asset;
    asset.asset_id = "frame-svg";
    asset.original_uri = "data:image/svg+xml;base64," + vellum::runtime::base64_encode(svg);
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, ir.asset_manifest,
                                       {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    require(frame->element_count() == 1);

    // The overlay is a LIVE tab widget carrying the source selection, not a
    // static repaint of the design. (Regression target: "the tab group came
    // through non-interactive".)
    auto* tabs = dynamic_cast<DesignTabGroup*>(frame->overlay_widget(0));
    require(tabs != nullptr);
    require(tabs->tab_count() == 4);
    CHECK(tabs->selected() == 2);

    // Clickable end-to-end: clicking a slot moves the live selection.
    frame->set_bounds({0, 0, frame->panel_width(), frame->panel_height()});
    frame->layout_children();
    const auto b = tabs->bounds();
    const float slot = b.width / 4.0f;
    tabs->on_mouse_down({slot * 0.5f, b.height * 0.5f});   // slot 0
    CHECK(tabs->selected() == 0);

    // The baked slot-2 highlight was suppressed in the ctor, so moving the LIVE
    // selection is the only thing that repaints the strip, proving the live pill
    // owns the highlight (no leftover baked rect / double-pill).
    auto render_sel = [&](int sel) {
        tabs->set_selected_silent(sel);
        return render_to_png(*frame, static_cast<int>(frame->panel_width()),
                             static_cast<int>(frame->panel_height()), 2.0f,
                             ScreenshotBackend::skia);
    };
    const auto at2 = render_sel(2);
    if (at2.empty()) SKIP("Skia raster screenshot backend unavailable");
    const auto at0 = render_sel(0);
    require(!at0.empty());
    const auto cmp = compare_screenshots(at2, at0);
    require(cmp.valid);
    if (cmp.similarity >= 0.999f) SKIP("native raster unavailable in this build");
    CHECK(cmp.similarity < 0.999f);   // the live pill visibly moved between slots
}

FRAMEWORK_TEST(a_promoted_textbutton_still_reaches_an_interactive_descendant) {
    // TextButton defaults to PointerEvents::box_only so a centred icon cannot
    // swallow its own click. That default would otherwise make the promoted-
    // child hit policy dead for buttons: hit_test() stops at the button and
    // never descends, so `pass_through_self` (box_none on the child) and
    // `unchanged` (the child is itself interactive) become unreachable, and
    // pulpHitTestable="true" silently stops meaning anything.
    //
    // The sibling case above pins the same contract with a Knob parent, Knob
    // has no box_only default, which is exactly why that test kept passing
    // while the button path broke.
    DesignIR ir;
    ir.root = frame("root", 120.0f, 120.0f, LayoutDirection::column);

    auto button_node = frame("outer-button", 80.0f, 80.0f, LayoutDirection::column);
    button_node.type = "button";
    button_node.text_content = "Outer";

    auto container = frame("interactive-container", 80.0f, 40.0f, LayoutDirection::column);
    auto nested = frame("nested-fine-button", 60.0f, 20.0f, LayoutDirection::column);
    nested.type = "button";
    nested.text_content = "Fine";
    container.children.push_back(std::move(nested));
    button_node.children.push_back(std::move(container));
    ir.root.children.push_back(std::move(button_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    root->set_bounds({0, 0, 120.0f, 120.0f});
    root->layout_children();

    auto* outer = dynamic_cast<TextButton*>(root->child_at(0));
    require(outer != nullptr);
    // Re-opened for descent, because this button asked for a reachable child.
    require(outer->pointer_events() == View::PointerEvents::auto_);

    auto* container_view = outer->child_at(0);
    require(container_view != nullptr);
    require(container_view->pointer_events() == View::PointerEvents::box_none);
    auto* nested_view = dynamic_cast<TextButton*>(container_view->child_at(0));
    require(nested_view != nullptr);

    // The nested button is reachable...
    require(root->hit_test({30.0f, 10.0f}) == nested_view);
    // ...and the outer button still owns its own body.
    require(root->hit_test({70.0f, 60.0f}) == outer);
}

namespace {

/// Counts the delegate hooks the widgets actually reach, so a test can tell
/// "the skin was installed" apart from "the skin was installed AND consulted".
class HookCountingPainter final : public WidgetPainter {
public:
    bool paint_rotary(vellum::canvas::Canvas&, const RotaryPaintState&, View&) override {
        ++rotary;
        return true;
    }
    bool paint_linear(vellum::canvas::Canvas&, const LinearPaintState& state, View&) override {
        ++linear;
        last_horizontal = state.horizontal;
        last_thumb_pos = state.thumb_pos;
        return true;
    }
    int rotary = 0;
    int linear = 0;
    bool last_horizontal = false;
    float last_thumb_pos = -1.0f;
};

}  // namespace


FRAMEWORK_TEST(a_designed_fader_gets_the_value_only_skin_like_a_designed_knob) {
    // The design already painted the track, so the widget must contribute only
    // the value layer. apply_designed_body_skin installs that contract, and it
    // was wired into the knob case only, a designed fader kept its full stock
    // body and drew a track, a fill and an oversized thumb over the design's own.
    //
    // The knob leg is the CONTROL. It passes before this change, which is what
    // proves the fixture can report success rather than being a probe that never
    // fires on either leg.
    DesignIR ir;
    ir.root = frame("designed-root", 320.0f, 200.0f, LayoutDirection::row);

    auto knob_node = frame("cutoff", 160.0f, 160.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.attributes["designed_body"] = "underlay";
    knob_node.attributes["design_accent"] = "#C4622A";
    knob_node.attributes["design_track"] = "rgba(60,50,30,0.18)";
    knob_node.attributes["design_indicator"] = "#2A2418";

    auto fader_node = frame("drive", 10.0f, 96.0f, LayoutDirection::column);
    fader_node.audio_widget = AudioWidgetType::fader;
    fader_node.attributes["designed_body"] = "underlay";
    fader_node.attributes["design_accent"] = "#C4622A";
    fader_node.attributes["design_track"] = "rgba(60,50,30,0.18)";
    fader_node.attributes["design_indicator"] = "#2A2418";

    ir.root.children.push_back(std::move(knob_node));
    ir.root.children.push_back(std::move(fader_node));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->child_count() == 2);

    // painter(), not effective_painter(): the latter walks up to an ancestor, so
    // it would pass on a widget that was never skinned itself.
    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    require(knob->painter() != nullptr);

    auto* fader = dynamic_cast<Fader*>(root->child_at(1));
    require(fader != nullptr);
    require(fader->painter() != nullptr);
}

FRAMEWORK_TEST(designed_discrete_controls_materialize_as_value_only_overlays) {
    DesignIR ir;
    ir.root = frame("root", 320.0f, 200.0f, LayoutDirection::row);

    auto toggle = frame("toggle", 48.0f, 24.0f, LayoutDirection::row);
    toggle.audio_widget = AudioWidgetType::toggle;
    toggle.attributes["designed_body"] = "underlay";
    auto selector = frame("selector", 120.0f, 28.0f, LayoutDirection::row);
    selector.audio_widget = AudioWidgetType::selector;
    selector.attributes["designed_body"] = "capture";
    selector.attributes["pulpChoices"] = "A|B";
    auto stepper = frame("stepper", 96.0f, 28.0f, LayoutDirection::row);
    stepper.audio_widget = AudioWidgetType::stepper;
    stepper.attributes["designed_body"] = "underlay";
    ir.root.children.push_back(std::move(toggle));
    ir.root.children.push_back(std::move(selector));
    ir.root.children.push_back(std::move(stepper));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    auto* toggle_view = dynamic_cast<ToggleButton*>(root->child_at(0));
    auto* selector_view = dynamic_cast<SegmentedControl*>(root->child_at(1));
    auto* stepper_view = dynamic_cast<Stepper*>(root->child_at(2));
    require(toggle_view != nullptr);
    require(selector_view != nullptr);
    require(stepper_view != nullptr);
    CHECK(toggle_view->designed_overlay());
    CHECK(selector_view->designed_overlay());
    CHECK(stepper_view->designed_overlay());
}

FRAMEWORK_TEST(fader_paint_consults_its_paint_delegate_as_knob_paint_does) {
    // Installing the delegate is not enough. WidgetPainter::paint_linear had no
    // caller anywhere, so a skinned fader painted its stock body regardless and
    // the install moved zero pixels. Assert the hook is reached, and assert the
    // knob's alongside it so a silent regression in either is visible.
    vellum::canvas::RecordingCanvas canvas;
    auto painter = std::make_shared<HookCountingPainter>();

    Knob knob;
    knob.set_bounds({0, 0, 64.0f, 64.0f});
    knob.set_painter(painter);
    knob.paint(canvas);
    require(painter->rotary == 1);

    Fader fader;
    fader.set_bounds({0, 0, 10.0f, 96.0f});
    fader.set_orientation(Fader::Orientation::vertical);
    fader.set_value(0.25f);
    fader.set_painter(painter);
    fader.paint(canvas);
    require(painter->linear == 1);
    require(painter->last_horizontal == false);
    require(painter->last_thumb_pos == Approx(0.25f));
}

FRAMEWORK_TEST(a_lowered_control_does_not_repaint_the_box_shadow_its_underlay_drew) {
    // A bound dial is lowered into a CONTROL node placed over the native node
    // it came from, and Chrome's computed appearance is recorded on that
    // control so its geometry survives a round-trip. `designed_body` states
    // that the BODY is the layer beneath, but the materializer installed the
    // recorded shadow stack on the control as well, so every layer composited
    // twice. Two composites of one translucent layer are not one composite: a
    // 0.5-alpha offset layer reaches 0.75 and its tail stretches, which on a
    // real panel reads as a dark crescent under the dial where the browser
    // leaves a clean gap.
    DesignIR ir;
    ir.root = frame("root", 200.0f, 200.0f, LayoutDirection::column);

    const auto stack = parse_css_box_shadow(
        "rgba(0, 0, 0, 0.5) 0px 18px 36px 0px, "
        "rgba(0, 0, 0, 0.34) 0px 4px 9px 0px");
    require(stack.size() == 2);

    // The lowered native node the dial was hoisted out of. It draws the body.
    auto underlay = frame("dial", 160.0f, 160.0f, LayoutDirection::column);
    underlay.style.background_color = "rgb(247, 240, 224)";
    underlay.style.box_shadow = stack;
    ir.root.children.push_back(std::move(underlay));

    // The control hoisted over it, carrying the same recorded appearance.
    auto control = frame("CUTOFF", 160.0f, 160.0f, LayoutDirection::column);
    control.audio_widget = AudioWidgetType::knob;
    control.attributes["binding"] = "cutoff";
    control.attributes["designed_body"] = "underlay";
    control.style.background_color = "rgb(247, 240, 224)";
    control.style.box_shadow = stack;
    ir.root.children.push_back(std::move(control));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 2);

    // The underlay still paints the design's shadow: the control must lose its
    // duplicate, not the design its depth.
    CHECK(root->child_at(0)->box_shadows().size() == 2);
    CHECK(root->child_at(1)->box_shadows().empty());
}

FRAMEWORK_TEST(a_designed_knob_without_an_authored_value_layer_paints_none) {
    // The authored contract: the design owns the control's entire appearance.
    // This knob is a plain gradient dial, no ring, no pointer, and the
    // browser capture shows exactly that. The skin used to synthesize an
    // accent value arc, a grey track and a derived tick anyway, riding
    // OUTSIDE the authored dial, so every captured knob came back wearing arcs
    // its own reference image does not contain, the largest single bucket of
    // a strict-fidelity diff on a real generated panel.
    //
    // Assert the PAINT STREAM, not a skin field: no arcs, no lines. The body
    // is the underlay's to draw; the widget contributes nothing.
    DesignIR ir;
    ir.root = frame("designed-root", 320.0f, 320.0f, LayoutDirection::row);

    auto knob_node = frame("cutoff", 160.0f, 160.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.attributes["designed_body"] = "underlay";
    knob_node.attributes["design_accent"] = "#C4622A";
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    knob->set_bounds({0, 0, 160.0f, 160.0f});

    vellum::canvas::RecordingCanvas canvas;
    knob->paint(canvas);

    for (const auto& cmd : canvas.commands()) {
        CHECK(cmd.type != vellum::canvas::DrawCommand::Type::stroke_arc);
        CHECK(cmd.type != vellum::canvas::DrawCommand::Type::stroke_line);
    }

    // Control for the harness: silence above must be the painter's doing, not
    // a recording failure. The same recorder capturing a stock arc-gauge paint
    // proves the instrument can see arcs when they are drawn.
    vellum::canvas::RecordingCanvas control_canvas;
    painters::paint_mod_ring_knob(control_canvas,
                                  Rect{0.0f, 0.0f, 160.0f, 160.0f}, 0.5f);
    bool control_saw_arc = false;
    for (const auto& cmd : control_canvas.commands())
        if (cmd.type == vellum::canvas::DrawCommand::Type::stroke_arc)
            control_saw_arc = true;
    require(control_saw_arc);
}

FRAMEWORK_TEST(a_design_can_declare_where_its_value_ring_belongs) {
    // design_ring_radius is the refinement, not the mechanism: a design that
    // drew its own decorative ring can put the value exactly on it. Absent, the
    // derivation above runs, which is why the common case needs no new data.
    DesignIR ir;
    ir.root = frame("designed-root", 320.0f, 320.0f, LayoutDirection::row);

    auto knob_node = frame("cutoff", 160.0f, 160.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.attributes["designed_body"] = "underlay";
    knob_node.attributes["design_accent"] = "#C4622A";
    // kelvin's authored tick ring: radius 98 on a 160 box.
    knob_node.attributes["design_ring_radius"] = "0.6125";
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    knob->set_bounds({0, 0, 160.0f, 160.0f});

    vellum::canvas::RecordingCanvas canvas;
    knob->paint(canvas);

    const vellum::canvas::DrawCommand* arc = nullptr;
    for (const auto& cmd : canvas.commands())
        if (cmd.type == vellum::canvas::DrawCommand::Type::stroke_arc) { arc = &cmd; break; }
    require(arc != nullptr);
    require(arc->f[2] == Approx(98.0f));
}

FRAMEWORK_TEST(a_designed_knob_s_pointer_starts_at_the_body_edge_not_the_centre) {
    // The other half of the same contract. Giving the RING a design-derived
    // radius stopped the arc crossing the cap and left the pointer running from
    // the box centre, so the spoke that used to end on the cap now crosses the
    // cap AND the gap. Whatever a reviewer calls it, a black line across a
    // brushed dial is the design's body being painted over by the widget.
    //
    // The value layer is authored-gated now, so this design DECLARES its ring -
    // at 83/160, the same radius the old body-edge derivation produced, and
    // the derived tick is the one that reads against it. The geometry under
    // test is unchanged: the tick spans the ring's own layer, not the cap.
    //
    // Asserted on the PAINTED endpoints, because the endpoints are what a
    // reviewer sees and the skin fields are private to the painter.
    DesignIR ir;
    ir.root = frame("designed-root", 320.0f, 320.0f, LayoutDirection::row);

    auto knob_node = frame("cutoff", 160.0f, 160.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.attributes["designed_body"] = "underlay";
    knob_node.attributes["design_accent"] = "#C4622A";
    knob_node.attributes["design_ring_radius"] = "0.51875";
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    knob->set_bounds({0, 0, 160.0f, 160.0f});

    vellum::canvas::RecordingCanvas canvas;
    knob->paint(canvas);

    const vellum::canvas::DrawCommand* line = nullptr;
    for (const auto& cmd : canvas.commands())
        if (cmd.type == vellum::canvas::DrawCommand::Type::stroke_line) line = &cmd;
    require(line != nullptr);

    // kelvin's dial: a 160px box, so the body edge is at radius 80.
    const float cx = 80.0f, cy = 80.0f;
    const auto radius_of = [&](float x, float y) {
        return std::sqrt((x - cx) * (x - cx) + (y - cy) * (y - cy));
    };
    const float r_in = radius_of(line->f[0], line->f[1]);
    const float r_out = radius_of(line->f[2], line->f[3]);
    // The whole point: it does not start at the centre, and it starts OUTSIDE
    // the body rather than on it.
    CHECK(r_in > 80.0f);
    // And it still points outward, ending on the ring's outer edge.
    CHECK(r_out > r_in);
    CHECK(r_out == Approx(85.0f).margin(0.5f));
}

FRAMEWORK_TEST(a_stock_knob_s_pointer_still_starts_at_the_centre) {
    // The control this change could break. `paint_mod_ring_knob` is shared with
    // every un-designed knob in Pulp, whose box IS its own body and whose
    // pointer is meant to be a spoke. The new scales default to zero precisely
    // so those callers are untouched, and that default is worth pinning: a
    // regression here is invisible in the design fixtures and visible in every
    // stock UI.
    vellum::canvas::RecordingCanvas canvas;
    painters::paint_mod_ring_knob(canvas, Rect{0.0f, 0.0f, 100.0f, 100.0f}, 0.5f);

    const vellum::canvas::DrawCommand* line = nullptr;
    for (const auto& cmd : canvas.commands())
        if (cmd.type == vellum::canvas::DrawCommand::Type::stroke_line) line = &cmd;
    require(line != nullptr);
    CHECK(line->f[0] == Approx(50.0f));
    CHECK(line->f[1] == Approx(50.0f));
}

FRAMEWORK_TEST(a_design_s_declared_pointer_overrides_the_derived_tick) {
    // The captured-indicator path, reached from the browser-capture lane.
    //
    // The consumer half already existed for the Figma lane
    // (hoist_captured_art_knobs -> knob_ind_*), but it hung off an ASSET-backed
    // disc, so a CSS-designed knob could never reach it: apply_captured_art_
    // knob_skin returns immediately without an asset_path. The browser-capture
    // lane's knobs are pure CSS, so a design that drew its own pointer had it
    // ignored and got our derived tick instead.
    //
    // knob_ind_* are fractions of the disc HALF-extent. On a 160px box the
    // half-extent is 80, so r_out 1.15 is radius 92 and r_in 0.80 is radius 64.
    DesignIR ir;
    ir.root = frame("designed-root", 320.0f, 320.0f, LayoutDirection::row);

    auto knob_node = frame("cutoff", 160.0f, 160.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.attributes["designed_body"] = "underlay";
    knob_node.attributes["design_accent"] = "#C4622A";
    knob_node.attributes["knob_ind_r_in"] = "0.80";
    knob_node.attributes["knob_ind_r_out"] = "1.15";
    knob_node.attributes["knob_ind_w"] = "0.05";
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    knob->set_bounds({0, 0, 160.0f, 160.0f});

    vellum::canvas::RecordingCanvas canvas;
    knob->paint(canvas);

    const vellum::canvas::DrawCommand* line = nullptr;
    bool saw_arc = false;
    for (const auto& cmd : canvas.commands()) {
        if (cmd.type == vellum::canvas::DrawCommand::Type::stroke_line) line = &cmd;
        if (cmd.type == vellum::canvas::DrawCommand::Type::stroke_arc) saw_arc = true;
    }
    require(line != nullptr);
    // An authored pointer authorizes only the pointer: the design drew no value
    // ring, so none may appear around it.
    CHECK_NOT(saw_arc);

    const float cx = 80.0f, cy = 80.0f;
    const auto radius_of = [&](float x, float y) {
        return std::sqrt((x - cx) * (x - cx) + (y - cy) * (y - cy));
    };
    // The declared geometry, not the derived 81..85. A units slip here (using
    // the fraction as-is instead of halving it) doubles both radii, so the
    // margins are tight enough to catch it.
    CHECK(radius_of(line->f[0], line->f[1]) == Approx(64.0f).margin(0.5f));
    CHECK(radius_of(line->f[2], line->f[3]) == Approx(92.0f).margin(0.5f));
}

FRAMEWORK_TEST(a_lowered_control_sits_above_the_decoration_it_was_drawn_over) {
    // The other half of the same failure, and the half that reached real
    // panels: a design need not say anything at all for its controls to become
    // unusable.
    //
    // Lowering assigns every composed node a z-index from Chrome's paint order.
    // The synthesized controls were appended AFTERWARDS with no z-index, so
    // they defaulted to `auto` and sat beneath the decoration. Appending them
    // last does not help, z-index beats document order, so the topmost
    // decorative band answered every press meant for a control.
    //
    // This asserts the ORDERING, not a magic number: the control must outrank
    // the decoration, whatever the decoration happens to be numbered.
    DesignIR ir;
    ir.root = frame("panel", 200.0f, 200.0f, LayoutDirection::column);

    auto decoration = frame("html", 200.0f, 200.0f, LayoutDirection::column);
    decoration.style.z_index = 0;
    ir.root.children.push_back(std::move(decoration));

    auto knob_node = frame("cutoff", 100.0f, 100.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.style.position = "absolute";
    knob_node.style.left = 50.0f;
    knob_node.style.top = 50.0f;
    knob_node.style.z_index = 1;   // what the importer now assigns
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    root->set_bounds({0, 0, 200.0f, 200.0f});
    root->layout_children();

    // Press the knob's centre, the point the operability gate drags.
    View* hit = root->hit_test({100.0f, 100.0f});
    require(hit != nullptr);
    CHECK(dynamic_cast<Knob*>(hit) != nullptr);
}

FRAMEWORK_TEST(a_decorative_layer_that_opts_out_of_hit_testing_stops_eating_presses) {
    // The shape of a real failure: a generated panel rendered correctly and
    // responded to nothing, because the design stacks full-width decorative
    // bands (glows, gradient washes, vignettes) over its controls.
    //
    // `pointer-events` was in the capture's property list and reached no IR
    // field, so it was dropped at the parse boundary. The design's only way to
    // say "this band is decoration" did nothing, and hit-testing follows paint
    // order, so the band above the knob won every press meant for it.
    //
    // The band is deliberately declared AFTER the knob and given a positive
    // z-index, which is what the failing panel did: a positioned element with a
    // positive z-index paints above z-auto content regardless of document
    // order, so appending controls last does not save them.
    DesignIR ir;
    ir.root = frame("panel", 200.0f, 200.0f, LayoutDirection::column);

    auto knob_node = frame("cutoff", 200.0f, 200.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    ir.root.children.push_back(std::move(knob_node));

    auto glow = frame("glow", 200.0f, 200.0f, LayoutDirection::column);
    glow.style.position = "absolute";
    glow.style.top = 0.0f;
    glow.style.left = 0.0f;
    glow.style.z_index = 5;
    glow.style.pointer_events = "none";
    ir.root.children.push_back(std::move(glow));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    root->set_bounds({0, 0, 200.0f, 200.0f});
    root->layout_children();

    auto* band = root->child_at(1);
    require(band != nullptr);
    // The property has to survive capture -> IR -> View. Asserting the View
    // state rather than the IR field is deliberate: the IR carrying it while
    // the materializer ignores it is exactly the failure mode this covers, and
    // an IR-only assertion passes against it.
    CHECK(band->pointer_events() == View::PointerEvents::none);

    // The discriminating check. Press the knob's centre, the exact point the
    // operability gate drags, and require the knob to receive it. Without the
    // opt-out the band is hit-testable, sits above, and answers instead.
    View* hit = root->hit_test({100.0f, 100.0f});
    require(hit != nullptr);
    CHECK(dynamic_cast<Knob*>(hit) != nullptr);
}

// Figma resize constraints reach the live View tree, not just the script lane.
//
// `IRLayout::h_constraint` / `v_constraint` are a codegen-wide contract, so a
// right-pinned or centered node has to land in the same place whether it
// renders through a script bundle or through the native tree. A surface that
// skips the mapping anchors the node flush-left and reports nothing.
// The assertions below are on FlexStyle rather than on the IR on purpose, the
// IR carrying the token while the materializer ignores it is the failure this
// covers, and an IR-only assertion passes against it.
FRAMEWORK_TEST(native_materializer_lowers_figma_resize_constraints_onto_flex) {
    auto frame_with = [](const char* h, const char* v) {
        DesignIR ir;
        ir.root.type = "frame";
        ir.root.stable_anchor_id = "root";
        IRNode child;
        child.type = "frame";
        child.stable_anchor_id = "pinned";
        child.style.width = 40.0f;
        child.style.height = 20.0f;
        if (h) child.layout.h_constraint = h;
        if (v) child.layout.v_constraint = v;
        ir.root.children.push_back(std::move(child));
        return ir;
    };

    {
        auto root = build_native_view_tree(frame_with("center", nullptr), {}, {});
        require(root != nullptr);
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().dim_margin_left.unit == DimensionUnit::auto_);
        CHECK(child->flex().dim_margin_right.unit == DimensionUnit::auto_);
        // The untouched axis stays put, a horizontal constraint must not
        // silently centre the node vertically as well.
        CHECK(child->flex().dim_margin_top.unit != DimensionUnit::auto_);
        CHECK(child->flex().dim_margin_bottom.unit != DimensionUnit::auto_);
    }

    {
        auto root = build_native_view_tree(frame_with("right", nullptr), {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().dim_margin_left.unit == DimensionUnit::auto_);
        CHECK(child->flex().dim_margin_right.unit != DimensionUnit::auto_);
    }

    {
        auto root = build_native_view_tree(frame_with(nullptr, "bottom"), {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().dim_margin_top.unit == DimensionUnit::auto_);
        CHECK(child->flex().dim_margin_bottom.unit != DimensionUnit::auto_);
    }

    {
        // min-width:100% is what keeps stretch effective on a node that also
        // has a width, Yoga clamps the final size up to min-width. Without it
        // the constraint is a silent no-op precisely on sized nodes, which is
        // most of them in an imported design.
        auto root = build_native_view_tree(frame_with("stretch", nullptr), {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().align_self == FlexAlign::stretch);
        CHECK(child->flex().dim_min_width.unit == DimensionUnit::percent);
        CHECK(child->flex().dim_min_width.value == Approx(100.0f));
    }

    {
        auto ir = frame_with("stretch", nullptr);
        ir.root.children[0].style.min_width = 500.0f;
        auto root = build_native_view_tree(ir, {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().align_self == FlexAlign::stretch);
        CHECK(child->flex().dim_min_width.unit == DimensionUnit::px);
        CHECK(child->flex().dim_min_width.value == Approx(500.0f));
    }

    {
        auto ir = frame_with("scale", nullptr);
        ir.root.layout.direction = LayoutDirection::row;
        ir.root.children[0].layout.flex_grow = 3.0f;
        auto root = build_native_view_tree(ir, {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().flex_grow == Approx(3.0f));
    }

    {
        auto ir = frame_with("scale", nullptr);
        ir.root.layout.direction = LayoutDirection::column;
        auto root = build_native_view_tree(ir, {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().align_self != FlexAlign::stretch);
        CHECK(child->flex().flex_grow == Approx(0.0f));
    }

    {
        auto ir = frame_with("stretch", nullptr);
        ir.root.children[0].layout.align_self = "center";
        auto root = build_native_view_tree(ir, {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().align_self == FlexAlign::center);
    }

    {
        auto root = build_native_view_tree(frame_with("left", "top"), {}, {});
        auto* child = root->child_at(0);
        require(child != nullptr);
        CHECK(child->flex().dim_margin_left.unit != DimensionUnit::auto_);
        CHECK(child->flex().dim_margin_top.unit != DimensionUnit::auto_);
        CHECK(child->flex().align_self != FlexAlign::stretch);
    }
}

// Track flow direction reaches the live grid. Without it a `grid-auto-flow:
// column` design fills row-first and every implicitly-placed child lands
// transposed, a whole-panel scramble that still renders, so nothing looks
// broken enough to investigate.
FRAMEWORK_TEST(native_materializer_lowers_grid_auto_flow) {
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";
    ir.root.layout.display = "grid";
    ir.root.layout.grid_template_columns = "1fr 1fr";
    ir.root.layout.grid_auto_flow = "column";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    CHECK(root->grid().auto_flow == GridStyle::AutoFlow::column);

    // A grid that does not declare a flow keeps the CSS default rather than
    // inheriting whatever the previous node set.
    DesignIR plain = ir;
    plain.root.layout.grid_auto_flow.reset();
    auto plain_root = build_native_view_tree(plain, {}, {});
    require(plain_root != nullptr);
    CHECK(plain_root->grid().auto_flow == GridStyle::AutoFlow::row);
}


int main() { return framework_test::run_all(); }
