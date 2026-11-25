// Native materializer behavior, part 1: the baked DesignIR lane that turns a
// captured design into a view tree.

#include "native_materializer_support.hpp"

FRAMEWORK_TEST(baked_native_materializer_matches_live_react_layout_parity_for_a_plugin_panel) {
    auto live = build_live_plugin_panel();
    auto ir = build_plugin_panel_ir_from_live(*live);

    std::vector<ImportDiagnostic> diagnostics;
    auto materialized = build_native_view_tree(ir, ir.asset_manifest, {.diagnostics_out = &diagnostics});
    require(materialized != nullptr);

    auto baked = std::make_unique<View>();
    baked->set_bounds({0, 0, 360, 160});
    baked->add_child(std::move(materialized));
    baked->layout_children();

    const LayoutTreeSnapshotOptions options{
        .surface = "phase4-baked-native",
        .fixture = "plugin-panel-live-react-vs-native",
        .viewport_width = 360,
        .viewport_height = 160,
    };
    const auto live_json = dump_layout_tree(*live, options);
    const auto baked_json = dump_layout_tree(*baked, options);

    LayoutTreeDiff diff;
    const bool equivalent = layout_tree_snapshots_equivalent(live_json, baked_json, {}, &diff);
    require(equivalent);
    require(!diagnostics_contain(diagnostics, "native-materialize-failed"));
}

FRAMEWORK_TEST(baked_native_materializer_accepts_rgb_rgba_on_every_paint_not_just_background) {
    // The CSS color fallback lived at ONE call site. `apply_visual_style` calls
    // the color parser at seven paint sites, background, text `color`,
    // `border_color`, and the four per-side border colors, and only background
    // fell back from the hex fast path to the CSS parser. So a design writing
    // `color: rgba(...)` rendered its background correctly and SILENTLY DROPPED
    // its text and every border. Figma emits exactly that: it demotes a hairline
    // stroke to a 1px frame whose fill is `rgba(171,171,171,0.1)`.
    //
    // Same shape as this branch's shadow bug (setBoxShadow emitted from the frame
    // branch alone → 16 declared shadows became 1): one branch learned the
    // lesson, its siblings never heard it. So this asserts the SIBLINGS, a test
    // that only checked background would have passed throughout the bug.
    DesignIR ir;
    ir.root.type = "text";
    ir.root.text_content = "Attack";
    ir.root.stable_anchor_id = "label";
    ir.root.style.width = 80.0f;
    ir.root.style.height = 20.0f;
    ir.root.style.background_color = "rgba(30, 30, 46, 1)";
    ir.root.style.color = "rgba(171, 171, 171, 0.5)";
    ir.root.style.border_width = 1.0f;
    ir.root.style.border_color = "rgb(137, 180, 250)";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);

    // Background: the one site that always worked, a control, so a regression
    // here reads as "the parser broke" rather than "the fallback is missing".
    require(root->background_color().a > 0.0f);

    // Text: dropped before this fix, the label rendered in the inherited color.
    const auto text = root->inheritable_text_color();
    require(text.has_value());
    CHECK(text->a == Approx(0.5f).margin(0.01f));
    CHECK(text->r == Approx(171.0f / 255.0f).margin(0.01f));

    // Border: dropped before this fix, Figma's hairline grid rendered invisible.
    const auto border = root->border_color();
    CHECK(border.b == Approx(250.0f / 255.0f).margin(0.01f));
    CHECK(border.a > 0.0f);
}

FRAMEWORK_TEST(baked_native_materializer_accepts_the_oklab_colours_chromium_serializes) {
    // A captured design does not reach the materializer in the syntax it was
    // authored in: Chromium serializes lab/lch/oklab/oklch/color-mix and any
    // wide-gamut literal into `oklab()` or `oklch()`. The colour allowlist knew
    // only `rgb`/`hsl`, so those values were not parsed and never applied, the
    // view kept its DEFAULT colour, which is a silent loss rather than a visibly
    // wrong one. A real capture's accent label rendered in the inherited colour.
    //
    // Asserted on text, border and background together for the same reason the
    // rgba() case above does: the allowlist is one gate in front of seven paint
    // sites, so a single-site check passes throughout the bug.
    DesignIR ir;
    ir.root.type = "text";
    ir.root.text_content = "DRIVE";
    ir.root.stable_anchor_id = "label";
    ir.root.style.width = 80.0f;
    ir.root.style.height = 20.0f;
    // Chromium paints these exact strings as rgb(6, 8, 9), rgb(142, 255, 157)
    // and rgb(192, 255, 198); the values below are read off its own render.
    ir.root.style.background_color = "oklab(0.152 0 -0.005)";
    ir.root.style.color = "oklab(0.913804 -0.143517 0.0939829)";
    ir.root.style.border_width = 1.0f;
    ir.root.style.border_color = "oklch(0.7 0.2 145)";

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);

    const auto text = root->inheritable_text_color();
    require(text.has_value());
    CHECK(text->r == Approx(142.0f / 255.0f).margin(0.01f));
    CHECK(text->g == Approx(255.0f / 255.0f).margin(0.01f));
    CHECK(text->b == Approx(157.0f / 255.0f).margin(0.01f));

    const auto border = root->border_color();
    CHECK(border.r == Approx(48.0f / 255.0f).margin(0.01f));
    CHECK(border.g == Approx(189.0f / 255.0f).margin(0.01f));
    CHECK(border.a > 0.0f);

    // The background must be the design's near-black, not the parser's
    // opaque-white fallback for a token it does not know. Without this the case
    // passes when every value above resolves to white.
    CHECK(root->background_color().r < 0.2f);
    CHECK(root->background_color().g < 0.2f);
    CHECK(root->background_color().b < 0.2f);
}

FRAMEWORK_TEST(baked_native_materializer_carries_a_resolved_clip_rectangle_to_the_view) {
    // The importer resolves a node's real CSS clip chain to one rectangle in
    // the node's own space. It reaches the renderer through this slot and NOT
    // through `overflow`, because `overflow` clips whatever the node's children
    // turn out to be, DOM parentage, and that is the chain CSS does not use.
    // Two nodes, so the child proves the clip is per-node rather than something
    // the parent's rectangle happened to cover.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "panel";
    ir.root.style.width = 200.0f;
    ir.root.style.height = 200.0f;
    ir.root.style.clip_rect = IRStyle::ClipRect{5.0f, 5.0f, 90.0f, 90.0f};

    IRNode child;
    child.type = "frame";
    child.stable_anchor_id = "escapee";
    child.style.position = "absolute";
    child.style.left = 10.0f;
    child.style.top = 10.0f;
    child.style.width = 50.0f;
    child.style.height = 50.0f;
    ir.root.children.push_back(std::move(child));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    require(root->ancestor_clip_rect().has_value());
    CHECK(root->ancestor_clip_rect()->x == 5.0f);
    CHECK(root->ancestor_clip_rect()->width == 90.0f);

    // A node with no resolved clip gets none, it is not inherited from the
    // parent's rectangle, which is the whole reason the slot is per-node.
    require(root->child_count() == 1);
    CHECK_NOT(root->child_at(0)->ancestor_clip_rect().has_value());
}

FRAMEWORK_TEST(native_text_clipping_invariant_catches_separator_sized_label_ancestors) {
    const std::vector<std::pair<std::string, float>> labels{
        {"LIVE", 48.0f}, {"PRECISION", 84.0f}, {"IIR", 41.0f}, {"FFT", 42.0f}, {"HYBRID", 63.0f}};

    auto make_fixture = [&](bool planted_clip, bool planted_ancestor_clip) {
        DesignIR ir;
        ir.root = frame("text-clip-root", 320.0f, 40.0f, LayoutDirection::row);

        auto rail =
            frame("segmented-rail", planted_clip ? 2.0f : 278.0f, 22.0f, LayoutDirection::row);
        if (planted_clip)
            rail.style.overflow = "hidden";

        for (const auto& [text, width] : labels) {
            auto caption = label("label-" + text, text, planted_clip ? 0.0f : width, 15.0f);
            if (text == "HYBRID") {
                // Exercise the soft-wrapping path: intrinsic_width() is zero
                // for this label, so the invariant must use max-content or
                // painted extents to remain sensitive to clipped ink.
                caption.style.white_space = "normal";
                caption.style.height = 30.0f;
            }
            rail.children.push_back(std::move(caption));
        }
        ir.root.children.push_back(std::move(rail));

        std::vector<ImportDiagnostic> diagnostics;
        auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
        require(root != nullptr);
        require(!diagnostics_contain(diagnostics, "native-materialize-failed"));
        // The importer hands back a detached tree; establish the host bounds
        // before running the production Yoga path used by native materializer
        // callers.
        root->set_bounds({0, 0, 320, 40});
        root->layout_children();

        // Keep construction and layout on the production materializer path,
        // then plant the exact post-layout defect for the negative controls.
        // This avoids teaching the importer to accept an impossible CSS width
        // of zero while still proving the checker reaches native View objects.
        auto* materialized_rail = root->child_at(0);
        require(materialized_rail != nullptr);
        if (planted_clip) {
            float x = 0.0f;
            for (std::size_t i = 0; i < labels.size(); ++i) {
                auto* caption = materialized_rail->child_at(i);
                require(caption != nullptr);
                caption->set_bounds({x + 10.0f, 4.0f, 0.0f, 15.0f});
                x += labels[i].second;
            }
        }
        if (planted_ancestor_clip) {
            auto* caption = materialized_rail->child_at(0);
            require(caption != nullptr);
            caption->set_ancestor_clip_rect({0.0f, 0.0f, 2.0f, 15.0f});
            require(caption->ancestor_clip_rect().has_value());
        }
        return root;
    };

    const auto positive = make_fixture(false, false);
    const auto positive_failures = text_clip_failures(*positive);
    require(positive_failures.empty());

    // This is a failure-proving control: if the checker cannot see an
    // explicitly planted 2px overflow-hidden ancestor, it cannot protect an
    // imported header from the exact regression found in the Spectr artifact.
    const auto planted = make_fixture(true, false);
    const auto planted_failures = text_clip_failures(*planted);
    require(planted_failures.size() == labels.size());
    for (const auto& failure : planted_failures) {
        CHECK(failure.measured_width > 0.0f);
        CHECK(failure.node_width == Approx(0.0f));
        CHECK(failure.clip_width == Approx(2.0f));
    }

    // Also exercise the importer-owned ancestor clip channel. It clips a
    // label's own ink without becoming an overflow ancestor for its children,
    // so the checker must account for it separately from the propagated clip.
    const auto ancestor_planted = make_fixture(false, true);
    const auto ancestor_failures = text_clip_failures(*ancestor_planted);
    require(ancestor_failures.size() == 1);
    CHECK(ancestor_failures.front().text == "LIVE");
    CHECK(ancestor_failures.front().node_width > 0.0f);
    CHECK(ancestor_failures.front().clip_width == Approx(2.0f));
}

FRAMEWORK_TEST(baked_native_materializer_applies_the_svg_fill_rule_to_the_path_widget) {
    // The winding rule decides which regions of a multi-subpath path are
    // holes; Figma bakes subtracted icons as same-direction contours under
    // evenodd, so a rule that stops at the IR renders those solid. Both
    // spellings must land: `svg_fill_rule` is the IR-canonical key
    // (design_ir_json), `fill-rule` the raw SVG/JSX one this lane's other
    // paint attributes already use.
    auto make_path = [](const char* rule_key, const char* rule) {
        DesignIR ir;
        ir.root.type = "path";
        ir.root.style.width = 32.0f;
        ir.root.style.height = 32.0f;
        ir.root.attributes["d"] =
            "M2 2 L30 2 L30 30 L2 30 Z M10 10 L22 10 L22 22 L10 22 Z";
        ir.root.attributes["viewBox"] = "0 0 32 32";
        ir.root.attributes["fill"] = "#ff0000";
        if (rule_key) ir.root.attributes[rule_key] = rule;
        return build_native_view_tree(ir, {}, {});
    };

    auto canonical = make_path("svg_fill_rule", "evenodd");
    auto* path = dynamic_cast<SvgPathWidget*>(canonical.get());
    require(path != nullptr);
    require(path->fill_rule() == vellum::canvas::FillRule::evenodd);

    auto raw_svg = make_path("fill-rule", "evenodd");
    require(dynamic_cast<SvgPathWidget*>(raw_svg.get())->fill_rule() ==
            vellum::canvas::FillRule::evenodd);

    // Unset stays on the widget default.
    auto unset = make_path(nullptr, "");
    require(dynamic_cast<SvgPathWidget*>(unset.get())->fill_rule() ==
            vellum::canvas::FillRule::nonzero);
}

FRAMEWORK_TEST(baked_native_materializer_resolves_image_sources_through_the_asset_manifest) {
    DesignIR ir;
    ir.root.type = "image";
    ir.root.stable_anchor_id = "logo";
    ir.root.attributes["src"] = "/raw/should-not-be-used.png";
    ir.root.attributes["srcAssetId"] = "asset-logo";
    ir.root.style.width = 64.0f;
    ir.root.style.height = 32.0f;

    TempDir cache("materializer-asset-manifest");
    const auto logo = write_asset_file(cache.path / "cache" / "logo.png");

    IRAssetRef asset;
    asset.asset_id = "asset-logo";
    asset.original_uri = "/raw/source-logo.png";
    asset.local_path = logo.string();
    asset.content_hash = "sha256:test";
    asset.mime = "image/png";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* image = dynamic_cast<ImageView*>(root.get());
    require(image != nullptr);
    require(image->image_source() ==
            "file://" + logo.lexically_normal().generic_string());
    require(!diagnostics_contain(diagnostics, "native-materialize-unresolved-asset"));
}

FRAMEWORK_TEST(baked_native_materializer_resolves_figma_plugin_asset_ref_image_sources) {
    DesignIR ir;
    ir.source = DesignSource::figma_plugin;
    ir.source_adapter = "figma-plugin";
    ir.root.type = "image";
    ir.root.name = "Imported Figma Image";
    ir.root.attributes["asset_ref"] = "3:43";
    ir.root.style.width = 64.0f;
    ir.root.style.height = 32.0f;

    TempDir imported("materializer-figma-asset-ref");
    const auto sprite = write_asset_file(imported.path / "assets" / "3_43.png");

    IRAssetRef asset;
    asset.asset_id = "3:43";
    asset.original_uri = "figma://KCKIyZoWXjde6qVNCm4qPa/3:43";
    asset.local_path = sprite.string();
    asset.mime = "image/png";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* image = dynamic_cast<ImageView*>(root.get());
    require(image != nullptr);
    require(image->image_source() ==
            "file://" + sprite.lexically_normal().generic_string());
    require(!diagnostics_contain(diagnostics, "native-materialize-unresolved-asset"));
}

FRAMEWORK_TEST(baked_native_materializer_renders_a_faithful_svg_node_as_a_designframeview) {
    // Plan B / B3: a faithful_svg node materializes to a DesignFrameView that
    // renders the node's own SVG (resolved from a data: asset) and overlays the
    // typed interactive elements. The view auto-crops to the panel (the largest
    // in-frame rect) and holds the knob overlay.
    // A percent-encoded data: payload (the form a URL-safe SVG export emits) -
    // exercises the resolver's percent-decode path. Decodes to a 100x100 frame
    // with an 80x80 panel rect, a dome circle, and a needle path at "M50 38L50 30".
    const std::string encoded_payload =
        "%3Csvg%20width%3D%22100%22%20height%3D%22100%22%20"
        "xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E"
        "%3Crect%20x%3D%2210%22%20y%3D%2210%22%20width%3D%2280%22%20height%3D%2280%22%20"
        "rx%3D%224%22%20fill%3D%22%23cccccc%22%2F%3E"
        "%3Ccircle%20cx%3D%2250%22%20cy%3D%2250%22%20r%3D%2220%22%20fill%3D%22%238a97a6%22%2F%3E"
        "%3Cpath%20d%3D%22M50%2038L50%2030%22%20stroke%3D%22white%22%20stroke-width%3D%223%22%2F%3E"
        "%3C%2Fsvg%3E";

    DesignIR ir;
    ir.source = DesignSource::figma_plugin;
    ir.root.type = "frame";
    ir.root.name = "ELYSIUM";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "asset-frame-svg";

    IRInteractiveElement knob;
    knob.kind = InteractiveElementKind::knob;
    knob.cx = 50.0f;
    knob.cy = 50.0f;
    knob.hit_radius = 22.0f;
    knob.svg_patch_d = "M50 38L50 30";
    knob.default_value = 0.5f;
    ir.root.interactive_elements.push_back(knob);

    IRAssetRef asset;
    asset.asset_id = "asset-frame-svg";
    asset.original_uri = "data:image/svg+xml," + encoded_payload;
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    require(frame->element_count() == 1);
    CHECK(frame->panel_width() == 80.0f);   // the 80x80 panel, not the 100x100 frame
    CHECK(frame->panel_height() == 80.0f);
    CHECK(frame->element_value(0) == 0.5f);
    // No param_key on the knob → the frame stays inert w.r.t. host params: the
    // element carries no binding key and gestures do NOT self-route to the
    // HostParamSurface (they fall back to on_value_changed).
    CHECK(frame->element_param_key(0).empty());
    CHECK_NOT(frame->routes_changes_to_host_params());
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-faithful-svg-unresolved"));
}

FRAMEWORK_TEST(faithful_svg_knob_with_a_param_key_self_wires_to_the_host_param_surface) {
    // A geometry-detected knob that carries a host-param binding key materializes
    // with that key on its DesignFrameElement, and the frame enables host-param
    // routing so a user gesture drives the framework-agnostic HostParamSurface
    // directly (element_for_param_key resolves it). This is the binding channel
    // for interactive elements that are NOT recognized Pulp-Library widgets.
    const std::string encoded_payload =
        "%3Csvg%20width%3D%22100%22%20height%3D%22100%22%20"
        "xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%3E"
        "%3Crect%20x%3D%2210%22%20y%3D%2210%22%20width%3D%2280%22%20height%3D%2280%22%20"
        "rx%3D%224%22%20fill%3D%22%23cccccc%22%2F%3E"
        "%3Ccircle%20cx%3D%2250%22%20cy%3D%2250%22%20r%3D%2220%22%20fill%3D%22%238a97a6%22%2F%3E"
        "%3Cpath%20d%3D%22M50%2038L50%2030%22%20stroke%3D%22white%22%20stroke-width%3D%223%22%2F%3E"
        "%3C%2Fsvg%3E";

    DesignIR ir;
    ir.source = DesignSource::figma_plugin;
    ir.root.type = "frame";
    ir.root.name = "ELYSIUM";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "asset-frame-svg";

    IRInteractiveElement knob;
    knob.kind = InteractiveElementKind::knob;
    knob.cx = 50.0f;
    knob.cy = 50.0f;
    knob.hit_radius = 22.0f;
    knob.svg_patch_d = "M50 38L50 30";
    knob.default_value = 0.5f;
    knob.param_key = "filter.cutoff";
    ir.root.interactive_elements.push_back(knob);

    IRAssetRef asset;
    asset.asset_id = "asset-frame-svg";
    asset.original_uri = "data:image/svg+xml," + encoded_payload;
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    require(frame->element_count() == 1);
    // The binding key rode the IR → DesignFrameElement mapping.
    CHECK(frame->element_param_key(0) == "filter.cutoff");
    CHECK(frame->element_for_param_key("filter.cutoff") == 0);
    // Presence of a key auto-enables host-param routing for the frame.
    CHECK(frame->routes_changes_to_host_params());
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-faithful-svg-unresolved"));
}

FRAMEWORK_TEST(render_patch_golden_a_knob_s_needle_visibly_rotates_with_value_p3) {
    // P3 render-patch golden-gate: the faithful-vector lane rotates ONLY the
    // knob's needle path (svg_patch_d) around (cx,cy) by value and re-renders the
    // SVG, the chrome stays pixel-exact. Render the SAME frame at value 0.05 vs
    // 0.95 and prove the raster differs (the needle moved), the render-patch is
    // live, not a static repaint. Mirrors the tab-group live-pill golden.
    const std::string svg =
        R"(<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="10" y="10" width="80" height="80" rx="4" fill="#cccccc"/>)"
        R"(<circle cx="50" cy="50" r="20" fill="#8a97a6"/>)"
        R"(<path d="M50 38L50 30" stroke="white" stroke-width="3"/></svg>)";

    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg1";
    IRInteractiveElement knob;
    knob.kind = InteractiveElementKind::knob;
    knob.cx = 50; knob.cy = 50; knob.hit_radius = 22;
    knob.svg_patch_d = "M50 38L50 30";   // the needle the patch rotates
    knob.default_value = 0.5f;
    ir.root.interactive_elements.push_back(knob);
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
    frame->set_bounds({0, 0, frame->panel_width(), frame->panel_height()});
    frame->layout_children();

    auto render_at = [&](float v) {
        frame->set_element_value(0, v);
        return render_to_png(*frame, static_cast<int>(frame->panel_width()),
                             static_cast<int>(frame->panel_height()), 2.0f,
                             ScreenshotBackend::skia);
    };
    const auto lo = render_at(0.05f);
    if (lo.empty()) SKIP("Skia raster screenshot backend unavailable");
    const auto hi = render_at(0.95f);
    require(!hi.empty());
    const auto cmp = compare_screenshots(lo, hi);
    require(cmp.valid);
    if (cmp.similarity >= 0.999f) SKIP("native raster unavailable in this build");
    CHECK(cmp.similarity < 0.999f);   // the needle visibly rotated between the two values
}

FRAMEWORK_TEST(baked_native_materializer_resolves_a_faithful_svg_base64_data_asset) {
    // The same path, but the SVG arrives base64-encoded (the form the REST/SVG
    // export lane emits). Resolver must decode it before handing to the view.
    const std::string svg =
        R"(<svg width="40" height="40" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="4" y="4" width="32" height="32" fill="#222"/></svg>)";
    const std::string b64 = vellum::runtime::base64_encode(svg);

    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg1";

    IRAssetRef asset;
    asset.asset_id = "svg1";
    asset.original_uri = "data:image/svg+xml;base64," + b64;
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    CHECK(frame->panel_width() == 32.0f);
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-faithful-svg-unresolved"));
}

FRAMEWORK_TEST(baked_native_materializer_reads_a_faithful_svg_asset_from_local_path) {
    // The asset's SVG lives on disk (the form the CLI asset-resolution pass
    // stamps). Resolver must read the file before handing it to the view.
    const std::string svg =
        R"(<svg width="50" height="50" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="5" y="5" width="40" height="40" fill="#333"/></svg>)";
    const auto dir = fs::temp_directory_path();
    const auto file = dir / "faithful-svg-test.svg";
    { std::ofstream(file, std::ios::binary) << svg; }

    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg-on-disk";

    IRAssetRef asset;
    asset.asset_id = "svg-on-disk";
    asset.original_uri = "figma://file/node";  // not a data: URI, must use local_path
    asset.local_path = file.string();
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    CHECK(frame->panel_width() == 40.0f);
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-faithful-svg-unresolved"));
    fs::remove(file);
}

FRAMEWORK_TEST(baked_native_materializer_falls_back_when_a_faithful_svg_asset_is_unresolved) {
    // A faithful_svg node whose SVG asset is missing must NOT blank out: it
    // emits a diagnostic and falls back to normal materialization (a plain View
    // for a frame), so the rest of the tree still renders.
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.name = "Broken";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "missing-asset";  // not in the manifest

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, {}, {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    CHECK(dynamic_cast<DesignFrameView*>(root.get()) == nullptr);  // fell back
    CHECK(diagnostics_contain(diagnostics,
                              "native-materialize-faithful-svg-unresolved"));
}

FRAMEWORK_TEST(materializer_overlays_a_texteditor_for_a_faithful_svg_text_field_element) {
    // Plan B full-A slice 2: a text_field interactive element on a faithful_svg
    // node materializes (via to_frame_elements) into a DesignFrameView that hosts
    // a native TextEditor overlay, not a knob.
    const std::string svg =
        R"(<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="10" y="10" width="80" height="80" fill="#1c1d1d"/></svg>)";

    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg1";

    IRInteractiveElement search;
    search.kind = InteractiveElementKind::text_field;
    search.x = 16; search.y = 16; search.w = 60; search.h = 16;
    search.placeholder = "Search";
    ir.root.interactive_elements.push_back(search);

    IRAssetRef asset;
    asset.asset_id = "svg1";
    asset.original_uri = "data:image/svg+xml;base64," + vellum::runtime::base64_encode(svg);
    asset.mime = "image/svg+xml";
    ir.asset_manifest.assets.push_back(asset);

    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, ir.asset_manifest, {.diagnostics_out = &diagnostics});
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    require(frame->element_count() == 1);
    auto* editor = dynamic_cast<TextEditor*>(frame->overlay_widget(0));
    require(editor != nullptr);
    CHECK(editor->placeholder == "Search");
}

FRAMEWORK_TEST(faithful_svg_flows_producer_envelope_parse_materialize_designframeview) {
    // The whole-chain proof (Plan B / B5): an envelope shaped EXACTLY like the
    // faithful-vector producers emit (figma_rest_export.py --faithful-vector and
    // the Figma plugin's faithfulVector lane) must survive parse_figma_plugin_json
    // -> parse_ir_node -> build_native_view_tree and become a DesignFrameView with
    // the producer's typed knob. This pins the producer<->C++ contract that the
    // TS/Python producer unit tests can't reach.
    const std::string svg =
        R"(<svg width="100" height="100" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="10" y="10" width="80" height="80" fill="#1c1d1d"/>)"
        R"(<circle cx="50" cy="50" r="20" fill="#8a97a6"/>)"
        R"(<path d="M50 38L50 30" stroke="white" stroke-width="3"/></svg>)";
    const std::string b64 = vellum::runtime::base64_encode(svg);

    // Byte-for-byte the shape apply_faithful_vector / serialize.ts produce.
    const std::string envelope = std::string(R"json({
      "$schema": "https://pulp.dev/schemas/figma-plugin-export-v1.json",
      "format_version": "2026.05-figma-plugin-v1",
      "provenance": {"adapter": "figma-plugin", "version": "test"},
      "asset_manifest": {"version": 1, "assets": [
        {"asset_id": "frame-svg-3:42",
         "original_uri": "data:image/svg+xml;base64,)json") + b64 + R"json(",
         "mime": "image/svg+xml"}
      ]},
      "root": {
        "type": "frame", "name": "ELYSIUM", "figma_node_id": "3:42",
        "render_mode": "faithful_svg", "svg_asset_id": "frame-svg-3:42",
        "interactive_elements": [
          {"kind": "knob", "cx": 50, "cy": 50, "hit_radius": 22,
           "svg_patch_d": "M50 38L50 30", "default_value": 0.5,
           "source_node_id": "3:225"}
        ]
      }
    })json";

    // 1) The producer envelope parses into an IR that kept the faithful fields.
    const auto ir = parse_figma_plugin_json(envelope);
    require(ir.root.render_mode == NodeRenderMode::faithful_svg);
    require(ir.root.svg_asset_id == "frame-svg-3:42");
    require(ir.root.interactive_elements.size() == 1);
    require(ir.root.interactive_elements[0].svg_patch_d == "M50 38L50 30");
    require(ir.root.interactive_elements[0].source_node_id == "3:225");
    require(ir.asset_manifest.resolve("frame-svg-3:42") != nullptr);

    // 2) Materializing that IR yields a DesignFrameView with the typed knob.
    std::vector<ImportDiagnostic> diagnostics;
    auto root = build_native_view_tree(ir, ir.asset_manifest,
                                       {.diagnostics_out = &diagnostics});
    require(root != nullptr);
    auto* frame = dynamic_cast<DesignFrameView*>(root.get());
    require(frame != nullptr);
    require(frame->element_count() == 1);
    CHECK(frame->panel_width() == 80.0f);   // panel auto-cropped from the 100x100 frame
    CHECK(frame->element_value(0) == 0.5f);
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-faithful-svg-unresolved"));
}

FRAMEWORK_TEST(materializer_maps_every_interactive_kind_to_its_designframeelement_kind) {
    // P1a acceptance: fader + toggle + the already-emitted overlays each
    // materialize (JSON -> IR -> to_frame_elements -> DesignFrameView) to the
    // matching runtime Kind, no silent collapse to knob. The SVG underneath
    // always renders; this asserts the INTERACTION wired on top is correct.
    const std::string svg =
        R"(<svg width="240" height="200" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="10" y="10" width="220" height="180" fill="#1c1d1d"/></svg>)";

    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg1";

    auto add = [&](InteractiveElementKind k, float x, float y, float value) {
        IRInteractiveElement el;
        el.kind = k;
        el.x = x; el.y = y; el.w = 40; el.h = 20;
        el.cx = x + 6; el.cy = y + 6; el.hit_radius = 10;
        el.svg_patch_d = "M0 0L0 1";
        el.default_value = value;
        if (k == InteractiveElementKind::dropdown ||
            k == InteractiveElementKind::tab_group ||
            k == InteractiveElementKind::stepper) {
            el.options = {"A", "B"};
        }
        if (k == InteractiveElementKind::text_field) el.placeholder = "Search";
        if (k == InteractiveElementKind::toggle) el.flash = true;
        ir.root.interactive_elements.push_back(el);
    };
    using K = InteractiveElementKind;
    // Distinct (non-default) values so a dropped field-copy in to_frame_elements
    // would FAIL the value assertions below, not just the Kind mapping.
    add(K::knob, 20, 20, 0.1f);
    add(K::fader, 20, 50, 0.2f);
    add(K::toggle, 20, 80, 0.3f);
    add(K::dropdown, 20, 110, 0.4f);
    add(K::text_field, 90, 20, 0.6f);
    add(K::tab_group, 90, 50, 0.7f);
    add(K::stepper, 90, 80, 0.8f);

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
    require(frame->element_count() == 7);
    using FK = DesignFrameElement::Kind;
    CHECK(frame->element_kind(0) == FK::knob);
    CHECK(frame->element_kind(1) == FK::fader);
    CHECK(frame->element_kind(2) == FK::toggle);
    CHECK(frame->element_kind(3) == FK::dropdown);
    CHECK(frame->element_kind(4) == FK::text_field);
    CHECK(frame->element_kind(5) == FK::tab_group);
    CHECK(frame->element_kind(6) == FK::stepper);
    // Carried render fields survive the IR -> DesignFrameElement copy (so the
    // overlay actually renders, not just types correctly). A regression that
    // dropped `el.value = e.default_value` would redden these.
    CHECK(frame->element_value(0) == Approx(0.1f));  // knob
    CHECK(frame->element_value(1) == Approx(0.2f));  // fader thumb position
    CHECK(frame->element_value(2) == Approx(0.3f));  // toggle on/off state
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-faithful-svg-unresolved"));
}

FRAMEWORK_TEST(materializer_maps_swap_action_xy_pad_value_label_with_their_fields) {
    // P1b acceptance: each of the four new kinds materializes (JSON -> IR ->
    // to_frame_elements -> DesignFrameView) to the matching runtime Kind AND
    // carries its typed data (target_frame / action / value_y / text).
    const std::string svg =
        R"(<svg width="240" height="240" xmlns="http://www.w3.org/2000/svg">)"
        R"(<rect x="10" y="10" width="220" height="220" fill="#1c1d1d"/></svg>)";

    DesignIR ir;
    ir.root.type = "frame";
    ir.root.render_mode = NodeRenderMode::faithful_svg;
    ir.root.svg_asset_id = "svg1";

    IRInteractiveElement swap;
    swap.kind = InteractiveElementKind::swap;
    swap.x = 20; swap.y = 20; swap.w = 60; swap.h = 24; swap.target_frame = 2;
    ir.root.interactive_elements.push_back(swap);

    IRInteractiveElement act;
    act.kind = InteractiveElementKind::action;
    act.x = 20; act.y = 60; act.w = 30; act.h = 24; act.action = "octave_up";
    ir.root.interactive_elements.push_back(act);

    IRInteractiveElement pad;
    pad.kind = InteractiveElementKind::xy_pad;
    pad.x = 20; pad.y = 100; pad.w = 100; pad.h = 100;
    pad.default_value = 0.3f; pad.default_value_y = 0.7f;
    ir.root.interactive_elements.push_back(pad);

    IRInteractiveElement lbl;
    lbl.kind = InteractiveElementKind::value_label;
    lbl.x = 20; lbl.y = 210; lbl.w = 80; lbl.h = 16; lbl.text = "-6.0 dB";
    lbl.value_left_align = true;
    ir.root.interactive_elements.push_back(lbl);

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
    require(frame->element_count() == 4);
    using FK = DesignFrameElement::Kind;
    CHECK(frame->element_kind(0) == FK::swap);
    CHECK(frame->element_kind(1) == FK::action);
    CHECK(frame->element_kind(2) == FK::xy_pad);
    CHECK(frame->element_kind(3) == FK::value_label);
    // Every typed payload survives the IR -> DesignFrameElement copy, not just
    // the Kind. A dropped field-copy in to_frame_elements would redden these.
    CHECK(frame->element_target_frame(0) == 2);             // swap link target
    CHECK(frame->element_action(1) == "octave_up");         // action command id
    CHECK(frame->element_value(2) == Approx(0.3f));  // xy_pad X axis
    CHECK(frame->element_value_y(2) == Approx(0.7f));// xy_pad Y axis
    CHECK(frame->element_text(3) == "-6.0 dB");             // value_label readout
    CHECK(frame->element_left_align(3) == true);            // value_label alignment
    require(!diagnostics_contain(diagnostics,
                                      "native-materialize-faithful-svg-unresolved"));
}


int main() { return framework_test::run_all(); }
