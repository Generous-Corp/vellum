// Native materializer behavior, part 2: the baked DesignIR lane that turns a
// captured design into a view tree.

#include "native_materializer_support.hpp"

namespace {
// A trivial custom control used to prove the Tier-3 factory dispatch.
struct TestCustomControl : vellum::view::View {};
}  // namespace


FRAMEWORK_TEST(materializer_builds_a_custom_control_via_its_registered_factory_p7_tier_3) {
    clear_design_control_factories();
    bool called = false;
    DesignControlContext seen;
    register_design_control_factory("my.control", [&](const DesignControlContext& ctx) {
        called = true; seen = ctx;
        return std::make_unique<TestCustomControl>();
    });

    const std::string svg =
        R"(<svg width="120" height="80" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="10" y="10" width="100" height="60" fill="#1c1d1d"/></svg>)";
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg1";

    IRInteractiveElement c;
    c.kind = InteractiveElementKind::custom;
    c.x = 20; c.y = 20; c.w = 40; c.h = 20;
    c.factory_id = "my.control";
    c.custom_props = "{\"gain\":0.7}";
    c.default_value = 0.3f;
    c.source_node_id = "9:1";
    ir.root.interactive_elements.push_back(c);

    IRAssetRef asset;
    asset.asset_id = "svg1";
    asset.original_uri = "data:image/svg+xml;base64," + vellum::runtime::base64_encode(svg);
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, ir.asset_manifest,
                                       {.diagnostics_out = &diagnostics});
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    require(frame->element_count() == 1);
    CHECK(frame->element_kind(0) == DesignFrameElement::Kind::custom);
    CHECK(called);
    CHECK(seen.factory_id == "my.control");
    CHECK(seen.props == "{\"gain\":0.7}");
    CHECK(seen.source_node_id == "9:1");
    CHECK(seen.default_value == Approx(0.3f));
    // The factory's View is the live overlay.
    CHECK(dynamic_cast<TestCustomControl*>(frame->overlay_widget(0)) != nullptr);
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-custom-factory-unregistered"));
    clear_design_control_factories();
}

FRAMEWORK_TEST(an_unregistered_custom_factory_renders_inert_diagnoses_p7_tier_3) {
    clear_design_control_factories();  // ensure "missing" is not registered
    const std::string svg =
        R"(<svg width="120" height="80" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="10" y="10" width="100" height="60" fill="#1c1d1d"/></svg>)";
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg1";

    IRInteractiveElement c;
    c.kind = InteractiveElementKind::custom;
    c.x = 20; c.y = 20; c.w = 40; c.h = 20;
    c.factory_id = "missing";
    ir.root.interactive_elements.push_back(c);

    IRAssetRef asset;
    asset.asset_id = "svg1";
    asset.original_uri = "data:image/svg+xml;base64," + vellum::runtime::base64_encode(svg);
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, ir.asset_manifest,
                                       {.diagnostics_out = &diagnostics});
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    require(frame->element_count() == 1);          // the element still exists (SVG renders)
    CHECK(frame->element_kind(0) == DesignFrameElement::Kind::custom);
    CHECK(frame->overlay_widget(0) == nullptr);     // inert, no overlay widget
    CHECK(diagnostics_contain(diagnostics,
                              "native-materialize-custom-factory-unregistered"));
}

FRAMEWORK_TEST(baked_native_materializer_forwards_a_sampled_shape_fill_gradient) {
    // The importer samples a shape illustration's OWN gradient and stamps
    // shape_fill_gradient; the materializer forwards it to ImageView so a later
    // opt-in fill reveals the shape's real colors. Storing it is inert (no fill
    // value), so the image still renders plainly, the capability stays opt-in.
    TempDir resolved("materializer-shape-fill");
    const auto shape = write_asset_file(resolved.path / "shape.png");

    auto make = [&](const char* grad) {
        DesignIR ir;
        ir.source = DesignSource::figma_plugin;
        ir.root.type = "image";
        ir.root.attributes["asset_ref"] = "shape";
        ir.root.style.width = 80.0f;
        ir.root.style.height = 96.0f;
        if (grad) ir.root.attributes["shape_fill_gradient"] = grad;
        IRAssetRef asset;
        asset.asset_id = "shape";
        asset.local_path = shape.string();
        asset.mime = "image/png";
        ir.asset_manifest.assets.push_back(asset);
        std::vector<ImportDiagnostic> diag;
        return build_native_view_tree(ir, {}, {.diagnostics_out = &diag});
    };

    // A node carrying ≥2 stops materializes an ImageView with the gradient set,
    // but with NO fill value driven, so has_fill() stays false (opt-in intact).
    auto with_grad = make("#7a4cff,#c84cff,#b0d0ff");
    auto* img = dynamic_cast<ImageView*>(with_grad.get());
    require(img != nullptr);
    require(img->has_fill_gradient());
    require(img->fill_gradient().size() == 3);
    require(!img->has_fill());

    // A node without the attribute gets no gradient (logos/icons stay plain).
    auto plain = make(nullptr);
    auto* img2 = dynamic_cast<ImageView*>(plain.get());
    require(img2 != nullptr);
    require(!img2->has_fill_gradient());
}

FRAMEWORK_TEST(baked_native_materializer_preserves_figma_plugin_bleed_sprite_geometry) {
    DesignIR ir;
    ir.source = DesignSource::figma_plugin;
    ir.source_adapter = "figma-plugin";
    ir.root.type = "image";
    ir.root.name = "Imported Bleed Sprite";
    ir.root.attributes["asset_ref"] = "sprite";
    ir.root.attributes["png_natural_w"] = "420";
    ir.root.attributes["png_natural_h"] = "484";
    ir.root.attributes["art_core_x"] = "148";
    ir.root.attributes["art_core_y"] = "0";
    ir.root.attributes["art_core_w"] = "115";
    ir.root.attributes["art_core_h"] = "129";
    ir.root.style.width = 62.0f;
    ir.root.style.height = 68.0f;
    ir.root.style.position = "absolute";
    ir.root.style.left = 20.0f;
    ir.root.style.top = 30.0f;
    ir.root.style.render_bounds = IRStyle::RenderBounds{210.0f, 116.0f, -74.0f, 0.0f};

    TempDir bleed("materializer-bleed-sprite");
    const auto sprite_file = write_asset_file(bleed.path / "assets" / "sprite.png");

    IRAssetRef asset;
    asset.asset_id = "sprite";
    asset.original_uri = "figma://fixture/sprite";
    asset.local_path = sprite_file.string();
    asset.mime = "image/png";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, ir.asset_manifest, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* image = dynamic_cast<ImageView*>(root.get());
    require(image != nullptr);

    const float scale = 68.0f / 129.0f;
    const float expected_w = 420.0f * scale;
    const float expected_h = 484.0f * scale;
    const float expected_left =
        20.0f - 148.0f * scale + (62.0f - 115.0f * scale) * 0.5f;
    CHECK(image->flex().preferred_width == Approx(expected_w));
    CHECK(image->flex().preferred_height == Approx(expected_h));
    CHECK(image->flex().dim_width.value == Approx(expected_w));
    CHECK(image->flex().dim_height.value == Approx(expected_h));
    CHECK(image->left() == Approx(expected_left));
    CHECK(image->top() == Approx(30.0f));
    require(!diagnostics_contain(diagnostics, "native-materialize-unresolved-asset"));
}

FRAMEWORK_TEST(baked_native_materializer_returns_an_unresolved_asset_placeholder_with_diagnostics) {
    DesignIR ir;
    ir.root.type = "image";
    ir.root.stable_anchor_id = "missing-logo";
    ir.root.attributes["srcAssetId"] = "asset-missing";
    ir.root.style.width = 64.0f;
    ir.root.style.height = 32.0f;

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    require(dynamic_cast<ImageView*>(root.get()) == nullptr);
    require(root->id() == "missing-logo");
    require(root->flex().preferred_width == 64.0f);
    require(root->flex().preferred_height == 32.0f);
    require(diagnostics_contain(diagnostics, "native-missing-asset"));
    require(diagnostics_count(diagnostics, "native-materialize-unresolved-asset") == 0);
}

FRAMEWORK_TEST(baked_native_materializer_applies_token_theme_only_to_the_detached_root) {
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";
    ir.root.children.push_back(label("child", "Voice", 40.0f, 20.0f));
    ir.tokens.colors["accent.primary"] = "#112233";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->theme().colors.count("accent.primary") == 1);
    require(root->child_count() == 1);
    require(root->child_at(0)->theme().colors.empty());
}

FRAMEWORK_TEST(baked_native_materializer_applies_a_css_background_gradient) {
    // Real Figma imports paint their light "hero" panels and illustration fills
    // as CSS gradients (e.g. ELYSIUM's Rectangle 5). Dropping background_gradient
    // was the dominant dark/light parity gap; the materializer must route it
    // through the shared css_gradient helper. See css_gradient.cpp.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "panel";
    ir.root.style.width = 320.0f;
    ir.root.style.height = 200.0f;
    ir.root.style.background_color = "#1c1d1d";  // solid base, painted under
    ir.root.style.background_gradient =
        "linear-gradient(to bottom, #e4edf6, #b7c8db)";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->has_background_gradient());
    require(root->background_gradient_type() == 1);  // 1 = linear
}

FRAMEWORK_TEST(baked_native_materializer_reproduces_the_design_s_captured_knob_pointer) {
    // hoist_captured_art_knobs stamps the design's own pointer geometry; the
    // materializer must forward it to the Knob (set_captured_indicator) so the
    // renderer draws THAT pointer over the disc instead of a synthetic notch -
    // killing the double line and aligning to the disc's baked reference ticks.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";
    ir.root.style.width = 100.0f;
    ir.root.style.height = 100.0f;

    IRNode knob;
    knob.type = "frame";
    knob.stable_anchor_id = "knob";
    knob.audio_widget = AudioWidgetType::knob;
    knob.style.width = 30.0f;
    knob.style.height = 32.0f;
    // Post-hoist + post-enrich attribute contract for a captured-art knob.
    knob.attributes["asset_ref"] = "disc";
    knob.attributes["asset_path"] = "/resolved/disc.png";
    knob.attributes["png_natural_w"] = "420";
    knob.attributes["png_natural_h"] = "484";
    knob.attributes["captured_raster_origin_x_px"] = "48";
    knob.attributes["captured_raster_origin_y_px"] = "53";
    knob.attributes["captured_raster_dpr"] = "2";
    knob.attributes["browser_box_paint_left_px"] = "49";
    knob.attributes["browser_box_paint_top_px"] = "54";
    knob.attributes["browser_box_paint_right_px"] = "111";
    knob.attributes["browser_box_paint_bottom_px"] = "120";
    knob.attributes["browser_box_paint_dpr"] = "2";
    knob.attributes["knob_ind_r_in"] = "0.604";
    knob.attributes["knob_ind_r_out"] = "0.936";
    knob.attributes["knob_ind_w"] = "0.05";
    knob.attributes["knob_ind_color"] = "#ffffff";
    ir.root.children.push_back(std::move(knob));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->child_count() == 1);
    auto* k = dynamic_cast<Knob*>(root->child_at(0));
    require(k != nullptr);
    require(k->has_captured_indicator());
    require(k->has_captured_box_paint_rect());
    CHECK(k->captured_box_paint_left() == Approx(24.5f));
    CHECK(k->captured_box_paint_top() == Approx(27.0f));
    CHECK(k->captured_box_paint_right() == Approx(55.5f));
    CHECK(k->captured_box_paint_bottom() == Approx(60.0f));
    CHECK(k->captured_raster_scale() == Approx(2.0f));
    CHECK(k->captured_raster_origin_x() == Approx(24.0f));
    CHECK(k->captured_raster_origin_y() == Approx(26.5f));
    CHECK(k->captured_indicator_r_out() == Approx(0.936f));
    CHECK(k->captured_indicator_r_in() == Approx(0.604f));
}

FRAMEWORK_TEST(imported_text_vertically_centers_in_a_slot_taller_than_its_line) {
    // Figma centers text in a fixed-height frame, but the IR carries no
    // textAlignVertical. A single-line label given a slot taller than its font
    // (e.g. an 8px "SEARCH" in a 17px box) must center; a tight box stays top.
    // The web-compat codegen emits verticalAlign:middle under the SAME rule, so
    // the two render paths agree (screenshot-parity invariant). Label default top.
    {
        DesignIR ir;
        ir.root = label("search", "SEARCH", 80.0f, 17.0f);
        ir.root.style.font_size = 8.0f;
        auto root = build_native_view_tree(ir, {}, {});
        auto* lbl = dynamic_cast<Label*>(root.get());
        require(lbl != nullptr);
        require(lbl->vertical_align() == vellum::canvas::TextVerticalAlign::center);
    }
    {
        DesignIR ir;
        ir.root = label("tight", "Hz", 40.0f, 9.0f);
        ir.root.style.font_size = 8.0f;  // 9 <= 8 * 1.15 → not centered
        auto root = build_native_view_tree(ir, {}, {});
        auto* lbl = dynamic_cast<Label*>(root.get());
        require(lbl != nullptr);
        require(lbl->vertical_align() == vellum::canvas::TextVerticalAlign::top);
    }
}

FRAMEWORK_TEST(rasterized_vector_image_does_not_redraw_its_baked_stroke_as_a_box_border) {
    // A Figma vector exported as a PNG carries its stroke as border_color /
    // border_width, but the stroke is already in the raster. Drawing it again
    // paints a spurious outline, the visible bug was a purple rectangle around
    // the FILTER & EQ curve. Image views must suppress the CSS border.
    DesignIR ir;
    ir.source = DesignSource::figma_plugin;
    ir.root.type = "image";
    ir.root.stable_anchor_id = "curve";
    ir.root.attributes["asset_ref"] = "3:188";
    ir.root.style.width = 177.0f;
    ir.root.style.height = 26.0f;
    ir.root.style.border_color = "#7e6aff";
    ir.root.style.border_width = 1.0f;

    TempDir rasterized("materializer-rasterized-vector");
    const auto rasterized_file =
        write_asset_file(rasterized.path / "assets" / "3_188.png");

    IRAssetRef asset;
    asset.asset_id = "3:188";
    asset.original_uri = "figma://fixture/3:188";
    asset.local_path = rasterized_file.string();
    asset.mime = "image/png";
    ir.asset_manifest.assets.push_back(asset);

    auto root = build_native_view_tree(ir, ir.asset_manifest, {});
    require(root != nullptr);
    auto* image = dynamic_cast<ImageView*>(root.get());
    require(image != nullptr);
    CHECK(image->border_width() == Approx(0.0f));
}

FRAMEWORK_TEST(baked_native_materializer_resolves_an_rgba_background_color) {
    // Figma demotes a hairline stroke (the FILTER & EQ grid Line images) to a
    // 1px frame whose fill is the stroke color, typically rgba(171,171,171,0.1).
    // parse_hex_color drops rgba(), so the grid painted nothing; apply_visual_style
    // now falls back to the shared CSS color parser. Without this the EQ grid
    // (and any rgba fill) is invisible.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "grid-line";
    ir.root.style.width = 177.0f;
    ir.root.style.height = 1.0f;
    ir.root.style.background_color = "rgba(171, 171, 171, 0.1)";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->has_background_color());
    const auto c = root->background_color();
    CHECK(c.r == Approx(171.0f / 255.0f).margin(0.01f));
    CHECK(c.a == Approx(0.1f).margin(0.01f));
}

FRAMEWORK_TEST(baked_native_materializer_preserves_per_layer_background_position) {
    // The Dilla footer offsets its second checkerboard layer by `2px 2px`.
    // Carry the declaration through DesignIR into the same View slot the
    // native painter resolves by original CSS layer index.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "footer-dither";
    ir.root.style.width = 160.0f;
    ir.root.style.height = 12.0f;
    ir.root.style.background_gradient =
        "linear-gradient(45deg, #fff 25%, transparent 25%, transparent 75%, #fff 75%), "
        "linear-gradient(45deg, #fff 25%, transparent 25%, transparent 75%, #fff 75%)";
    ir.root.style.background_size = "4px 4px";
    ir.root.style.background_repeat = "repeat";
    ir.root.style.background_position = "0 0, 2px 2px";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    CHECK(root->background_position() == "0 0, 2px 2px");
}

FRAMEWORK_TEST(background_origin_reaches_the_view_and_every_emitted_form_of_the_ir) {
    // The origin decides the box a tile is sized and positioned in, so a lane
    // that drops it paints a border-box tile as padding-box, or the reverse.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "bar";
    ir.root.style.width = 400.0f;
    ir.root.style.height = 45.0f;
    ir.root.style.background_gradient =
        "linear-gradient(rgba(10, 14, 20, 0.8), rgba(10, 14, 20, 0))";
    ir.root.style.background_origin = "border-box";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    CHECK(root->background_origin() == "border-box");

    const auto parsed = parse_design_ir_json(serialize_design_ir(ir));
    require(parsed.root.style.background_origin);
    CHECK(*parsed.root.style.background_origin == "border-box");

    CodeGenOptions web;
    web.mode = CodeGenMode::web_compat;
    CHECK(generate_pulp_js(ir, web).find("backgroundOrigin") != std::string::npos);
    CHECK(generate_pulp_cpp(ir, ir.asset_manifest, {})
              .source.find("set_background_origin(\"border-box\")") != std::string::npos);

    // Control: absent stays absent, the initial padding-box.
    ir.root.style.background_origin.reset();
    CHECK_NOT(parse_design_ir_json(serialize_design_ir(ir)).root.style.background_origin);
    CHECK(generate_pulp_js(ir, web).find("backgroundOrigin") == std::string::npos);
}

FRAMEWORK_TEST(baked_native_materializer_maps_a_dropdown_frame_to_an_interactive_combobox) {
    // ELYSIUM's FX RACK ships explicit "Dropdown" frames (a selected-value text
    // + a chevron image). Recognized by layer name → a functional ComboBox whose
    // first item is the captured selection; the source has no option list, so
    // stub options demonstrate the popup. The text/chevron children are
    // suppressed (the ComboBox paints its own display).
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";

    IRNode dropdown;
    dropdown.type = "frame";
    dropdown.name = "Dropdown";
    dropdown.stable_anchor_id = "fx-delay";
    IRNode value;
    value.type = "text";
    value.text_content = "1/4 Delay";
    value.stable_anchor_id = "fx-delay-text";
    IRNode chevron;                  // a SQUARE down-chevron marks a real dropdown
    chevron.type = "image";
    chevron.name = "expand_more";
    chevron.attributes["asset_ref"] = "chevron";
    chevron.style.width = 16.0f;
    chevron.style.height = 16.0f;
    chevron.stable_anchor_id = "fx-delay-chevron";
    dropdown.children.push_back(value);
    dropdown.children.push_back(chevron);
    ir.root.children.push_back(std::move(dropdown));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->child_count() == 1);
    auto* combo = dynamic_cast<ComboBox*>(root->child_at(0));
    require(combo != nullptr);
    require(!combo->items().empty());
    CHECK(combo->items().front() == "1/4 Delay");
    CHECK(combo->selected_text() == "1/4 Delay");
    CHECK(combo->items().size() == 1);           // only the real shown value (no fabricated stubs)
    CHECK(combo->child_count() == 0);            // text + chevron suppressed
}

FRAMEWORK_TEST(baked_native_materializer_leaves_prev_next_cyclers_and_templates_alone) {
    // Not every "Dropdown"-named frame is a dropdown. A prev/next preset CYCLER
    // (ELYSIUM's ENVELOPE/FILTER/FX-RACK headers) uses a WIDE "< >" icon, and an
    // unconfigured design-system TEMPLATE shows the literal word "Dropdown".
    // Neither must become a ComboBox, they stay plain frames.
    auto make_dropdown = [](const std::string& value_text, float icon_w,
                            float icon_h) {
        IRNode dd;
        dd.type = "frame";
        dd.name = "Dropdown";
        dd.stable_anchor_id = "dd";
        IRNode v;
        v.type = "text";
        v.text_content = value_text;
        v.stable_anchor_id = "dd-text";
        IRNode icon;
        icon.type = "image";
        icon.attributes["asset_ref"] = "icon";
        icon.style.width = icon_w;
        icon.style.height = icon_h;
        icon.stable_anchor_id = "dd-icon";
        dd.children.push_back(v);
        dd.children.push_back(icon);
        return dd;
    };

    {
        DesignIR ir;
        ir.root = make_dropdown("Short Plucks", 42.0f, 16.0f);  // aspect 2.6
        auto root = build_native_view_tree(ir, {}, {});
        require(root != nullptr);
        require(dynamic_cast<ComboBox*>(root.get()) == nullptr);
    }
    {
        DesignIR ir;
        ir.root = make_dropdown("Dropdown", 16.0f, 16.0f);  // square but template
        auto root = build_native_view_tree(ir, {}, {});
        require(root != nullptr);
        // Not a ComboBox, and inert: a hidden placeholder with no children, so it
        // can't surface as a stray dropdown between panels.
        require(dynamic_cast<ComboBox*>(root.get()) == nullptr);
        CHECK(root->child_count() == 0);
        CHECK_NOT(root->hit_testable());
    }
}

FRAMEWORK_TEST(baked_native_materializer_maps_a_search_container_to_a_full_box_field) {
    // ELYSIUM's "Search" field is a CONTAINER (a background pill + a "Search"
    // placeholder text + a magnifier icon). The WHOLE box becomes a TAPPABLE
    // input (not just the tiny text cell): the placeholder is "SEARCH", the icon
    // is kept as an overlay, and the placeholder text + bg chrome are dropped.
    DesignIR ir;
    ir.root.type = "frame";          // the search container (e.g. "Group 59")
    ir.root.stable_anchor_id = "search-box";
    ir.root.style.width = 184.0f;
    ir.root.style.height = 26.0f;

    IRNode bg;                        // background pill, dropped
    bg.type = "frame";
    bg.name = "Rectangle 66";
    bg.stable_anchor_id = "search-bg";
    IRNode placeholder;              // the placeholder text, becomes the editor's
    placeholder.type = "text";
    placeholder.name = "Search";
    placeholder.text_content = "SEARCH";
    placeholder.style.font_size = 8.0f;
    placeholder.stable_anchor_id = "search-text";
    IRNode icon;                     // magnifier, kept as an overlay child
    icon.type = "image";
    icon.name = "ic:round-search";
    icon.attributes["asset_ref"] = "search-icon";
    icon.style.left = 6.0f;
    icon.style.width = 15.0f;
    icon.stable_anchor_id = "search-icon";
    ir.root.children.push_back(bg);
    ir.root.children.push_back(placeholder);
    ir.root.children.push_back(icon);

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    auto* editor = dynamic_cast<TextEditor*>(root.get());
    require(editor != nullptr);
    CHECK(editor->placeholder == "SEARCH");
    CHECK(editor->content_inset_left() > 15.0f);   // text cleared past the icon
    CHECK(root->child_count() == 1);               // only the icon kept (no text/bg)

    // Tappable + editable: focus then type enters text over the placeholder.
    editor->on_focus_changed(true);
    TextInputEvent te;
    te.text = "drums";
    editor->on_text_input(te);
    CHECK(editor->text() == "drums");
}

FRAMEWORK_TEST(hoist_captured_art_knobs_promotes_a_body_disc_pointer_to_an_interactive_skin) {
    // A captured-art knob ships a body disc image + a ~0-area pointer hairline
    // (the native notch replaces the pointer). Hoist the disc's asset_ref onto
    // the knob, drop the captured children, keep it a knob (interactive).
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";

    IRNode knob;
    knob.type = "frame";
    knob.stable_anchor_id = "knob";
    knob.audio_widget = AudioWidgetType::knob;
    IRNode body;       // the disc, at (0,0), 30x32 → center (15,16), half-extent 15
    body.type = "image";
    body.stable_anchor_id = "body";
    body.attributes["asset_ref"] = "disc-asset";
    body.style.left = 0.0f;
    body.style.top = 0.0f;
    body.style.width = 30.0f;
    body.style.height = 32.0f;
    IRNode pointer;    // a ~0-width stroked pointer hairline near top-center
    pointer.type = "image";
    pointer.stable_anchor_id = "ptr";
    pointer.attributes["asset_ref"] = "ptr-asset";
    pointer.style.left = 14.0f;   // ~centered on the 30-wide disc
    pointer.style.top = 2.0f;
    pointer.style.width = 0.0f;
    pointer.style.height = 5.0f;  // spans y 2..7
    pointer.style.border_width = 1.5f;
    pointer.style.border_color = "#ffffff";
    knob.children.push_back(body);
    knob.children.push_back(pointer);
    ir.root.children.push_back(std::move(knob));

    hoist_captured_art_knobs(ir);

    const auto& k = ir.root.children.at(0);
    require(k.audio_widget == AudioWidgetType::knob);          // stays interactive
    require(k.attributes.at("asset_ref") == "disc-asset");     // disc hoisted
    require(k.attributes.at("sprite_strip_frame_count") == "1");
    // Captured layers (disc + pointer) are gone, the design's pointer geometry
    // is stamped instead, so the renderer reproduces the real indicator.
    require(k.children.empty());
    // pointer ends (14,2)/(14,7) from disc center (15,16): far ≈ 14.04, near ≈ 9.06;
    // half-extent 15 → r_out ≈ 0.936, r_in ≈ 0.604.
    require(k.attributes.count("knob_ind_r_out") == 1);
    const float r_out = std::stof(k.attributes.at("knob_ind_r_out"));
    const float r_in = std::stof(k.attributes.at("knob_ind_r_in"));
    CHECK(r_out == Approx(0.936f).margin(0.02f));
    CHECK(r_in == Approx(0.604f).margin(0.02f));
    CHECK(r_out > r_in);
    require(k.attributes.at("knob_ind_color") == "#ffffff");
}

FRAMEWORK_TEST(hoist_captured_art_knobs_recognizes_a_stroke_demoted_pointer_frame) {
    // The stroke→fill demotion pass rewrites a hairline stroke vector (Figma
    // "Vector 7") into a 1px-wide frame whose stroke color lives on
    // background_color, NOT an asset image. The old hoist scanned only
    // asset-backed image children, so it missed this pointer entirely: the knob
    // fell back to a synthetic notch AND the demoted 1px frame rendered as a
    // stuck second line. The pointer scan now walks every thin non-body child.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";

    IRNode knob;
    knob.type = "frame";
    knob.stable_anchor_id = "knob";
    knob.audio_widget = AudioWidgetType::knob;
    IRNode body;       // disc at (0,0) 30x32 → center (15,16), half-extent 15
    body.type = "image";
    body.stable_anchor_id = "body";
    body.attributes["asset_ref"] = "disc-asset";
    body.style.left = 0.0f;
    body.style.top = 0.0f;
    body.style.width = 30.0f;
    body.style.height = 32.0f;
    IRNode pointer;    // a DEMOTED 1px frame, not an image, color on background
    pointer.type = "frame";
    pointer.stable_anchor_id = "ptr";
    pointer.attributes["__stroke_demoted"] = "1";
    pointer.style.left = 14.0f;
    pointer.style.top = 2.0f;
    pointer.style.width = 1.0f;   // demoted hairline = 1px, not 0
    pointer.style.height = 5.0f;  // spans y 2..7
    pointer.style.background_color = "#ff4400";
    knob.children.push_back(body);
    knob.children.push_back(pointer);
    ir.root.children.push_back(std::move(knob));

    hoist_captured_art_knobs(ir);

    const auto& k = ir.root.children.at(0);
    require(k.audio_widget == AudioWidgetType::knob);          // stays interactive
    require(k.attributes.at("asset_ref") == "disc-asset");     // disc hoisted
    // The demoted pointer is recognized, its geometry + color stamped, and the
    // stray 1px frame erased so it can't render as a stuck second line.
    require(k.children.empty());
    require(k.attributes.count("knob_ind_r_out") == 1);
    CHECK(std::stof(k.attributes.at("knob_ind_r_out")) >
          std::stof(k.attributes.at("knob_ind_r_in")));
    // Color comes from background_color (where the demotion pass put the stroke).
    require(k.attributes.at("knob_ind_color") == "#ff4400");
}

FRAMEWORK_TEST(hoist_captured_art_knobs_demotes_a_multi_layer_knob_to_a_static_container) {
    // Two SUBSTANTIAL captured layers (e.g. body + highlight) can't fit one
    // single-frame skin, so the knob demotes to a plain container, every layer
    // renders as an image (faithful but not turnable), no silent layer loss.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";

    IRNode knob;
    knob.type = "frame";
    knob.stable_anchor_id = "knob";
    knob.audio_widget = AudioWidgetType::knob;
    for (const char* id : {"body", "highlight"}) {
        IRNode layer;
        layer.type = "image";
        layer.stable_anchor_id = id;
        layer.attributes["asset_ref"] = std::string(id) + "-asset";
        layer.style.width = 40.0f;
        layer.style.height = 40.0f;
        knob.children.push_back(std::move(layer));
    }
    ir.root.children.push_back(std::move(knob));

    hoist_captured_art_knobs(ir);

    const auto& k = ir.root.children.at(0);
    require(k.audio_widget == AudioWidgetType::none);   // demoted to container
    require(k.children.size() == 2);                    // both layers preserved
    require(k.attributes.count("asset_ref") == 0);
}

FRAMEWORK_TEST(baked_native_materializer_leaves_background_gradient_unset_without_one) {
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "panel";
    ir.root.style.width = 100.0f;
    ir.root.style.height = 100.0f;
    ir.root.style.background_color = "#202020";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(!root->has_background_gradient());
}

FRAMEWORK_TEST(baked_native_materializer_maps_fill_sizing_by_parent_axis) {
    DesignIR ir;
    ir.root = frame("row-root", 300.0f, 120.0f, LayoutDirection::row);

    auto main_axis_fill = frame("main-fill", 0.0f, 40.0f, LayoutDirection::column);
    main_axis_fill.style.width.reset();
    main_axis_fill.layout.width_mode = SizingMode::fill;

    auto cross_axis_fill = frame("cross-fill", 64.0f, 0.0f, LayoutDirection::column);
    cross_axis_fill.style.height.reset();
    cross_axis_fill.layout.height_mode = SizingMode::fill;

    ir.root.children.push_back(std::move(main_axis_fill));
    ir.root.children.push_back(std::move(cross_axis_fill));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->child_count() == 2);

    const auto& main_flex = root->child_at(0)->flex();
    const auto& cross_flex = root->child_at(1)->flex();
    require(main_flex.flex_grow == 1.0f);
    require(cross_flex.flex_grow == 0.0f);
    require(cross_flex.align_self == FlexAlign::stretch);
}

FRAMEWORK_TEST(baked_native_materializer_routes_promoted_widget_hits_over_decorative_descendants) {
    DesignIR ir;
    ir.root = frame("root", 120.0f, 120.0f, LayoutDirection::column);

    auto knob_node = frame("gain-knob", 80.0f, 80.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.audio_label = "Gain";
    knob_node.attributes["value"] = "0.5";

    auto decorative_art = frame("gain-knob-imported-art", 80.0f, 80.0f, LayoutDirection::column);
    decorative_art.style.background_color = "#ff00ff";
    knob_node.children.push_back(std::move(decorative_art));

    auto nested_button = frame("gain-knob-nested-button", 60.0f, 20.0f, LayoutDirection::column);
    nested_button.type = "button";
    nested_button.text_content = "Fine";
    knob_node.children.push_back(std::move(nested_button));
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 1);

    root->set_bounds({0, 0, 120.0f, 120.0f});
    root->layout_children();

    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    require(knob->child_count() == 2);
    require(!knob->child_at(0)->hit_testable());
    require(knob->child_at(1)->hit_testable());

    auto* hit = root->hit_test({40.0f, 40.0f});
    require(hit == knob);
}

FRAMEWORK_TEST(binding_backed_imported_knob_drag_updates_from_the_visible_body) {
    DesignIR ir;
    ir.root = frame("root", 160.0f, 120.0f, LayoutDirection::column);

    auto knob_node = frame("bound-gain-knob", 80.0f, 80.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.audio_label = "Gain";
    knob_node.stable_anchor_id = "figma:bound-gain-knob";
    knob_node.attributes["value"] = "0.4";
    knob_node.attributes["pulpParamKey"] = "filter.gain";
    knob_node.attributes["pulpRouteId"] = "figma-plugin:filter.gain";
    knob_node.attributes["pulpBindingModule"] = "filter";
    knob_node.attributes["pulpBindingParam"] = "gain";
    knob_node.attributes["pulpEventContract"] = "onChange:set_param:filter.gain";
    knob_node.attributes["pulpGestureContract"] = "rotary_drag:begin/update/end";

    auto decorative_art = frame("imported-knob-art", 80.0f, 80.0f, LayoutDirection::column);
    decorative_art.style.background_color = "#ff00ff";
    auto value_label = label("imported-value-label", "80%", 80.0f, 20.0f);
    knob_node.children.push_back(std::move(decorative_art));
    knob_node.children.push_back(std::move(value_label));
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 1);

    root->set_bounds({0, 0, 160.0f, 120.0f});
    root->layout_children();

    auto* knob = dynamic_cast<Knob*>(root->child_at(0));
    require(knob != nullptr);
    require(knob->child_count() == 2);
    require(!knob->child_at(0)->hit_testable());
    require(!knob->child_at(1)->hit_testable());

    auto* body_hit = root->hit_test({40.0f, 40.0f});
    require(body_hit == knob);

    BindingBackedKnobContext binding;
    bind_native_view_tree(*root, ir, binding);
    require(binding.bound_knobs.size() == 1);
    require(binding.bound_knobs[0].route_id == "figma-plugin:filter.gain");
    require(binding.bound_knobs[0].param_key == "filter.gain");
    require(binding.bound_knobs[0].binding_module == "filter");
    require(binding.bound_knobs[0].binding_param == "gain");
    require(binding.bound_knobs[0].event_contract == "onChange:set_param:filter.gain");
    require(binding.bound_knobs[0].gesture_contract == "rotary_drag:begin/update/end");

    const float before = knob->value();
    require(before == 0.4f);
    require(binding.normalized_value() == before);

    root->simulate_drag({40.0f, 40.0f}, {40.0f, 10.0f}, 3);

    require(knob->value() > before);
    require(binding.normalized_value() == knob->value());
    require(!binding.changes.empty());
    require(binding.changes.back() == knob->value());
    require(binding.gesture_begin_count == 1);
    require(binding.gesture_end_count == 1);
}

FRAMEWORK_TEST(imported_fader_button_and_text_input_respond_to_interaction) {
    // Prove non-knob imported widgets are interactive, not just
    // rendered. The GRAINS knob already has a GPU-harness hit+drag proof; ELYSIUM
    // itself is knob-only (its "Search" is a static text label), so this covers
    // the remaining interactive widget classes, fader (drag), toggle button
    // (click), text input (type), on a synthetic imported fixture through the
    // real materialize → hit-test → event path.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";
    ir.root.style.width = 200.0f;
    ir.root.style.height = 200.0f;
    ir.root.layout.direction = LayoutDirection::column;
    ir.root.layout.gap = 8.0f;

    auto fader = frame("mix", 160.0f, 24.0f, LayoutDirection::column);
    fader.audio_widget = AudioWidgetType::fader;
    fader.audio_label = "Mix";
    fader.attributes["value"] = "0.1";
    fader.attributes["orientation"] = "horizontal";

    auto toggle = frame("sel", 160.0f, 24.0f, LayoutDirection::column);
    toggle.type = "toggle_button";
    toggle.text_content = "ON";
    toggle.attributes["checked"] = "false";

    auto input = frame("name", 160.0f, 24.0f, LayoutDirection::column);
    input.type = "input";
    input.attributes["type"] = "text";
    input.attributes["pulpInitialValue"] = "ab";

    ir.root.children.push_back(std::move(fader));
    ir.root.children.push_back(std::move(toggle));
    ir.root.children.push_back(std::move(input));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->child_count() == 3);
    root->set_bounds({0.0f, 0.0f, 200.0f, 200.0f});
    root->layout_children();

    auto* f = dynamic_cast<Fader*>(root->child_at(0));
    auto* t = dynamic_cast<ToggleButton*>(root->child_at(1));
    auto* e = dynamic_cast<TextEditor*>(root->child_at(2));
    require(f != nullptr);
    require(t != nullptr);
    require(e != nullptr);

    // Fader: dragging along its axis raises the value (routed through hit-test).
    const Rect fb = f->bounds();
    const float fader_before = f->value();
    root->simulate_drag({fb.x + fb.width * 0.15f, fb.y + fb.height * 0.5f},
                        {fb.x + fb.width * 0.85f, fb.y + fb.height * 0.5f}, 6);
    require(f->value() > fader_before);

    // Toggle button: a click flips its state, it is a target, not a picture.
    const Rect tb = t->bounds();
    const bool toggle_before = t->is_on();
    root->simulate_click({tb.x + tb.width * 0.5f, tb.y + tb.height * 0.5f});
    require(t->is_on() != toggle_before);

    // Text input: focusing and typing changes the committed text.
    const std::string text_before = e->text();
    e->on_focus_changed(true);
    TextInputEvent typed;
    typed.text = "c";
    e->on_text_input(typed);
    require(e->text() != text_before);
}

FRAMEWORK_TEST(native_materializer_binding_helper_requires_anchors_and_routes) {
    DesignIR ir;
    ir.root = frame("root", 320.0f, 260.0f, LayoutDirection::column);

    auto eligible = frame("eligible-knob", 80.0f, 80.0f, LayoutDirection::column);
    eligible.audio_widget = AudioWidgetType::knob;
    eligible.stable_anchor_id = "figma:eligible-knob";
    eligible.attributes["value"] = "0.25";
    eligible.attributes["pulpRouteId"] = "figma-plugin:eligible";
    eligible.attributes["pulpParamKey"] = "filter.eligible";
    eligible.attributes["pulpBindingModule"] = "filter";
    eligible.attributes["pulpBindingParam"] = "eligible";

    auto missing_anchor = frame("missing-anchor-knob", 80.0f, 80.0f, LayoutDirection::column);
    missing_anchor.audio_widget = AudioWidgetType::knob;
    missing_anchor.stable_anchor_id.reset();
    missing_anchor.attributes["value"] = "0.5";
    missing_anchor.attributes["pulpRouteId"] = "figma-plugin:missing-anchor";
    missing_anchor.attributes["pulpParamKey"] = "filter.missing_anchor";

    auto missing_route = frame("missing-route-knob", 80.0f, 80.0f, LayoutDirection::column);
    missing_route.audio_widget = AudioWidgetType::knob;
    missing_route.stable_anchor_id = "figma:missing-route-knob";
    missing_route.attributes["value"] = "0.75";
    missing_route.attributes["pulpParamKey"] = "filter.missing_route";

    auto wrong_type = label("wrong-type-label", "Wrong", 80.0f, 24.0f);
    wrong_type.attributes["pulpRouteId"] = "figma-plugin:wrong-type";
    wrong_type.attributes["pulpParamKey"] = "filter.wrong_type";

    ir.root.children.push_back(std::move(eligible));
    ir.root.children.push_back(std::move(missing_anchor));
    ir.root.children.push_back(std::move(missing_route));
    ir.root.children.push_back(std::move(wrong_type));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);

    BindingBackedKnobContext binding;
    std::vector<ImportDiagnostic> diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &diagnostics});

    require(binding.bound_knobs.size() == 1);
    require(binding.bound_knobs[0].route_id == "figma-plugin:eligible");
    require(binding.bound_knobs[0].param_key == "filter.eligible");
    require(diagnostics_count(diagnostics, "native-binding-missing-anchor") == 1);
    require(diagnostics_count(diagnostics, "native-binding-missing-route") == 1);
    require(diagnostics_count(diagnostics, "native-binding-not-applied") == 1);
    require(diagnostics_count(diagnostics, "native-binding-anchor-not-found") == 0);
}

FRAMEWORK_TEST(native_materializer_binding_helper_reports_anchors_missing_from_the_view_tree) {
    DesignIR ir;
    ir.root = frame("root", 120.0f, 120.0f, LayoutDirection::column);

    auto knob_node = frame("orphaned-knob", 80.0f, 80.0f, LayoutDirection::column);
    knob_node.audio_widget = AudioWidgetType::knob;
    knob_node.stable_anchor_id = "figma:orphaned-knob";
    knob_node.attributes["value"] = "0.5";
    knob_node.attributes["pulpRouteId"] = "figma-plugin:orphaned";
    knob_node.attributes["pulpParamKey"] = "filter.orphaned";
    ir.root.children.push_back(std::move(knob_node));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);
    require(root->child_count() == 1);
    root->child_at(0)->set_anchor_id("figma:different-view");

    BindingBackedKnobContext binding;
    std::vector<ImportDiagnostic> diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &diagnostics});

    require(binding.bound_knobs.empty());
    require(diagnostics_count(diagnostics, "native-binding-anchor-not-found") == 1);
    require(diagnostics[0].anchor_id == "figma:orphaned-knob");
}

FRAMEWORK_TEST(native_materializer_binding_helper_refuses_duplicate_materialized_anchors) {
    DesignIR ir;
    ir.root = frame("root", 220.0f, 120.0f, LayoutDirection::row);

    auto first = frame("first-duplicate-knob", 80.0f, 80.0f, LayoutDirection::column);
    first.audio_widget = AudioWidgetType::knob;
    first.stable_anchor_id = "figma:duplicate-knob";
    first.attributes["value"] = "0.25";
    first.attributes["pulpRouteId"] = "figma-plugin:first-duplicate";
    first.attributes["pulpParamKey"] = "filter.first";

    auto second = frame("second-duplicate-knob", 80.0f, 80.0f, LayoutDirection::column);
    second.audio_widget = AudioWidgetType::knob;
    second.stable_anchor_id = "figma:duplicate-knob";
    second.attributes["value"] = "0.75";
    second.attributes["pulpRouteId"] = "figma-plugin:second-duplicate";
    second.attributes["pulpParamKey"] = "filter.second";

    ir.root.children.push_back(std::move(first));
    ir.root.children.push_back(std::move(second));

    auto root = build_native_view_tree(ir, {}, {.preview_mode = true});
    require(root != nullptr);

    BindingBackedKnobContext binding;
    std::vector<ImportDiagnostic> diagnostics;
    bind_native_view_tree(*root, ir, binding, {.diagnostics_out = &diagnostics});

    require(binding.bound_knobs.empty());
    require(diagnostics_count(diagnostics, "native-binding-duplicate-anchor") == 2);
    require(diagnostics_count(diagnostics, "native-binding-not-applied") == 0);
    require(diagnostics_count(diagnostics, "native-binding-anchor-not-found") == 0);
}


int main() { return framework_test::run_all(); }
