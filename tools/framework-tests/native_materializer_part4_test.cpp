// Native materializer behavior, part 4: the baked DesignIR lane that turns a
// captured design into a view tree.

#include "native_materializer_support.hpp"

// Precedence when a node carries BOTH an explicit margin and a constraint that
// wants an auto margin on the same side.
//
// The script lane's comment asserts this combination "never arises in practice
//, a flowing auto-layout child never carries constraints, and a constrained
// child never carries these margins." That claim is inherited here, not
// verified: nothing in the IR, the ingest normalizers, or the materializer
// prevents a producer from emitting both. So rather than trust it, this pins
// what actually happens. It DOCUMENTS the precedence; it does not endorse the
// combination. If the claim is ever false the behavior is at least specified,
// instead of an authored margin vanishing with nothing said.
FRAMEWORK_TEST(a_resize_constraint_wins_over_an_explicit_margin_on_the_same_side) {
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";
    IRNode child;
    child.type = "frame";
    child.stable_anchor_id = "both";
    child.style.width = 40.0f;
    child.style.height = 20.0f;
    child.layout.margin_left = 12.0f;
    child.layout.margin_right = 12.0f;
    child.layout.h_constraint = "center";
    ir.root.children.push_back(std::move(child));

    auto root = build_native_view_tree(ir, {}, {});
    require(root != nullptr);
    auto* view = root->child_at(0);
    require(view != nullptr);

    // The constraint is applied last and wins on both sides. The float side is
    // cleared to -1 so FlexStyle falls through to the Dimension rather than
    // reading the authored 12.
    CHECK(view->flex().dim_margin_left.unit == DimensionUnit::auto_);
    CHECK(view->flex().dim_margin_right.unit == DimensionUnit::auto_);
    CHECK(view->flex().margin_left == Approx(-1.0f));
    CHECK(view->flex().margin_right == Approx(-1.0f));

    // An authored margin on an axis the constraint does NOT touch survives
    // intact, the constraint must not clear margins it has no opinion about.
    DesignIR vertical = ir;
    vertical.root.children[0].layout.margin_top = 9.0f;
    auto vroot = build_native_view_tree(vertical, {}, {});
    require(vroot != nullptr);
    auto* vview = vroot->child_at(0);
    require(vview != nullptr);
    CHECK(vview->flex().margin_top == Approx(9.0f));
    CHECK(vview->flex().dim_margin_top.unit != DimensionUnit::auto_);
}

// Pixel comparison remains the acceptance test for these patterns, but the
// pre-push coverage configuration intentionally uses the portable recording
// canvas rather than Skia. Exercise the same shared View paint contract here:
// CSS list values keep their declared image index through `none`, px tile
// stops normalize against the tile, and a captured paint rect adjusts only the
// element's own outer paint geometry.
FRAMEWORK_TEST(native_background_layers_retain_css_list_pairing_in_portable_paint) {
    View layered;
    layered.set_bounds({0.0f, 0.0f, 16.0f, 12.0f});
    layered.set_background_color(Color::rgba8(16, 20, 24, 255));
    layered.set_background_size("3px 4px, 8px 6px");
    layered.set_background_repeat("no-repeat, repeat");
    layered.set_background_position("1px 2px, 2px 1px");
    require(apply_css_background_gradient(
        layered,
        "none, linear-gradient(to right, #ff0000 1px, transparent 1px)"));

    vellum::canvas::RecordingCanvas layer_canvas;
    layered.paint_all(layer_canvas);

    int linear_shaders = 0;
    int tiled_fills = 0;
    bool saw_second_layer_origin = false;
    for (const auto& command : layer_canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::set_fill_gradient_linear)
            ++linear_shaders;
        if (command.type == vellum::canvas::DrawCommand::Type::fill_rect &&
            command.f[2] == 8.0f && command.f[3] == 6.0f) {
            ++tiled_fills;
            saw_second_layer_origin = saw_second_layer_origin ||
                (command.f[0] == -6.0f && command.f[1] == -5.0f);
        }
    }
    // The first declared image is `none`; the visible gradient must instead
    // pair with size/repeat/position entry 1 and fill its 8x6 repeated tile.
    CHECK(linear_shaders == 9);
    CHECK(tiled_fills == 9);
    CHECK(saw_second_layer_origin);

    View dots;
    dots.set_bounds({0.0f, 0.0f, 16.0f, 16.0f});
    dots.set_background_size("8px 8px");
    dots.set_background_repeat("repeat");
    require(apply_css_background_gradient(
        dots, "radial-gradient(circle, #ffffff 1px, transparent 1px)"));

    vellum::canvas::RecordingCanvas dot_canvas;
    dots.paint_all(dot_canvas);
    int radial_shaders = 0;
    for (const auto& command : dot_canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::set_fill_gradient_radial)
            ++radial_shaders;
    }
    // One shader per 8px tile proves radial px stops enter the shared tiled
    // gradient paint path rather than collapsing to an untiled solid fill.
    CHECK(radial_shaders == 4);

    View snapped;
    snapped.set_bounds({0.0f, 0.0f, 10.0f, 10.5f});
    snapped.set_background_color(Color::rgba8(0, 255, 0, 255));
    snapped.set_border(Color::rgba8(255, 0, 0, 255), 1.0f);
    snapped.set_overflow(View::Overflow::hidden);
    snapped.set_captured_box_paint_rect(0.0f, 0.0f, 10.0f, 11.0f, 2.0f);

    vellum::canvas::RecordingCanvas snap_canvas;
    snapped.paint_all(snap_canvas);
    bool saw_snapped_fill = false;
    bool saw_deferred_overflow = false;
    for (const auto& command : snap_canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::fill_rect &&
            command.f[2] == 10.0f && command.f[3] == 11.0f)
            saw_snapped_fill = true;
        if (command.type == vellum::canvas::DrawCommand::Type::clip_rect &&
            command.f[2] == 10.0f && command.f[3] == 10.5f)
            saw_deferred_overflow = true;
    }
    CHECK(saw_snapped_fill);
    CHECK(saw_deferred_overflow);

    View equal_size;
    equal_size.set_bounds({0.0f, 0.0f, 16.0f, 12.0f});
    equal_size.set_background_size("16px 12px");
    equal_size.set_background_repeat("repeat");
    equal_size.set_background_position("2px 0");
    require(apply_css_background_gradient(
        equal_size, "linear-gradient(to right, #ff0000, #0000ff)"));
    vellum::canvas::RecordingCanvas equal_canvas;
    equal_size.paint_all(equal_canvas);
    bool saw_equal_phase = false;
    for (const auto& command : equal_canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::fill_rect &&
            command.f[0] == 2.0f && command.f[1] == 0.0f &&
            command.f[2] == 16.0f && command.f[3] == 12.0f)
            saw_equal_phase = true;
    }
    CHECK(saw_equal_phase);

    View oversized;
    oversized.set_bounds({0.0f, 0.0f, 16.0f, 12.0f});
    oversized.set_background_size("24px 18px");
    oversized.set_background_repeat("repeat");
    oversized.set_background_position("2px 3px");
    require(apply_css_background_gradient(
        oversized, "linear-gradient(to right, #ff0000, #0000ff)"));
    vellum::canvas::RecordingCanvas oversized_canvas;
    oversized.paint_all(oversized_canvas);
    bool saw_oversized_phase = false;
    for (const auto& command : oversized_canvas.commands()) {
        if (command.type == vellum::canvas::DrawCommand::Type::fill_rect &&
            command.f[0] == 2.0f && command.f[1] == 3.0f &&
            command.f[2] == 24.0f && command.f[3] == 18.0f)
            saw_oversized_phase = true;
    }
    CHECK(saw_oversized_phase);

    const auto has_no_repeat_tile = [](std::string_view position,
                                       float wanted_x, float wanted_y) {
        View view;
        view.set_bounds({0.0f, 0.0f, 16.0f, 12.0f});
        view.set_background_size("8px 6px");
        view.set_background_repeat("no-repeat");
        view.set_background_position(std::string(position));
        require(apply_css_background_gradient(
            view, "linear-gradient(to right, #ff0000, #0000ff)"));
        vellum::canvas::RecordingCanvas canvas;
        view.paint_all(canvas);
        for (const auto& command : canvas.commands()) {
            if (command.type == vellum::canvas::DrawCommand::Type::fill_rect &&
                command.f[0] == wanted_x && command.f[1] == wanted_y &&
                command.f[2] == 8.0f && command.f[3] == 6.0f)
                return true;
        }
        return false;
    };
    CHECK(has_no_repeat_tile("right 2px bottom 3px", 6.0f, 3.0f));
    CHECK(has_no_repeat_tile("calc(100% - 2px) calc(100% - 3px)", 6.0f, 3.0f));
    CHECK(has_no_repeat_tile("calc(100% - 1px - 1px) calc(100% - 1px - 2px)",
                             6.0f, 3.0f));
    CHECK(has_no_repeat_tile("top", 4.0f, 0.0f));
    CHECK(has_no_repeat_tile("bottom right", 8.0f, 6.0f));
    CHECK(has_no_repeat_tile("left 10px center", 10.0f, 3.0f));
    CHECK(has_no_repeat_tile("center top 10px", 4.0f, 10.0f));
    // Two-value syntax is ordered x then y: a horizontal keyword followed by
    // a length sets the vertical axis. A vertical-first keyword + length is
    // not a valid two-value position.
    CHECK(has_no_repeat_tile("left 10px", 0.0f, 10.0f));
    CHECK(has_no_repeat_tile("right 10px", 8.0f, 10.0f));
    CHECK(has_no_repeat_tile("center left", 0.0f, 3.0f));
    CHECK(has_no_repeat_tile("top center", 4.0f, 0.0f));
    CHECK(has_no_repeat_tile("center right", 8.0f, 3.0f));
    CHECK(has_no_repeat_tile("bottom center", 4.0f, 6.0f));

    View unsupported;
    unsupported.set_bounds({0.0f, 0.0f, 16.0f, 12.0f});
    unsupported.set_background_size("4px 4px");
    require(apply_css_background_gradient(
        unsupported, "linear-gradient(to right, #ff0000, #0000ff)"));
    const auto shader_count = [&](View& view) {
        vellum::canvas::RecordingCanvas canvas;
        view.paint_all(canvas);
        int count = 0;
        for (const auto& command : canvas.commands())
            if (command.type == vellum::canvas::DrawCommand::Type::set_fill_gradient_linear)
                ++count;
        return count;
    };
    unsupported.set_background_repeat("no-repeat");
    unsupported.set_background_position("right 2px left 3px");
    CHECK(shader_count(unsupported) == 0);
    unsupported.set_background_position("calc(100%-2px) 0");
    CHECK(shader_count(unsupported) == 0);
    unsupported.set_background_position("calc(100% -2px) 0");
    CHECK(shader_count(unsupported) == 0);
    unsupported.set_background_position("top 20px");
    CHECK(shader_count(unsupported) == 0);
    unsupported.set_background_position("bottom 20px");
    CHECK(shader_count(unsupported) == 0);
    unsupported.set_background_position(". 0");
    CHECK(shader_count(unsupported) == 0);
    unsupported.set_background_position("1. 0");
    CHECK(shader_count(unsupported) == 0);
    unsupported.set_background_position(
        "999999999999999999999999999999999999999999999999px 0");
    CHECK(shader_count(unsupported) == 0);

    View overflow_size;
    overflow_size.set_bounds({0.0f, 0.0f, std::numeric_limits<float>::max(), 12.0f});
    overflow_size.set_background_size("1000% 6px");
    overflow_size.set_background_repeat("no-repeat");
    require(apply_css_background_gradient(
        overflow_size, "linear-gradient(to right, #ff0000, #0000ff)"));
    CHECK(shader_count(overflow_size) == 0);

    View overflow_position;
    overflow_position.set_bounds({0.0f, 0.0f, std::numeric_limits<float>::max(), 12.0f});
    overflow_position.set_background_size("1px 6px");
    overflow_position.set_background_repeat("no-repeat");
    overflow_position.set_background_position("1000% 0");
    require(apply_css_background_gradient(
        overflow_position, "linear-gradient(to right, #ff0000, #0000ff)"));
    CHECK(shader_count(overflow_position) == 0);
}

FRAMEWORK_TEST(native_background_size_repeat_and_position_retain_browser_pixels) {
    if (!raw_rgba_render_available())
        SKIP("Skia raw RGBA raster backend unavailable");

    struct Frame {
        std::vector<uint8_t> rgba;
        uint32_t width = 0, height = 0;
        const uint8_t* at(uint32_t x, uint32_t y) const {
            return &rgba[(static_cast<size_t>(y) * width + x) * 4];
        }
        bool red(uint32_t x, uint32_t y) const {
            const auto* p = at(x, y);
            return p[0] > 200 && p[2] < 50;
        }
        bool blue(uint32_t x, uint32_t y) const {
            const auto* p = at(x, y);
            return p[2] > 200 && p[0] < 50;
        }
        bool green(uint32_t x, uint32_t y) const {
            const auto* p = at(x, y);
            return p[1] > 200 && p[0] < 50 && p[2] < 50;
        }
    };
    const auto render = [](std::string_view size, std::string_view repeat,
                           std::string_view position) -> Frame {
        View view;
        view.set_bounds({0.0f, 0.0f, 16.0f, 12.0f});
        view.set_background_color(Color::rgba8(0, 255, 0, 255));
        view.set_background_size(std::string(size));
        view.set_background_repeat(std::string(repeat));
        view.set_background_position(std::string(position));
        require(apply_css_background_gradient(
            view, "linear-gradient(to right, #ff0000 50%, #0000ff 50%)"));
        Frame frame;
        frame.rgba = render_to_rgba(view, 16, 12, 1.0f,
                                    &frame.width, &frame.height);
        require(!frame.rgba.empty());
        require(frame.width == 16);
        require(frame.height == 12);
        return frame;
    };

    const Frame equal = render("16px 12px", "repeat", "2px 0");
    CHECK(equal.blue(0, 6));
    CHECK(equal.red(3, 6));

    const Frame oversized = render("24px 18px", "repeat", "2px 3px");
    CHECK(oversized.blue(0, 6));
    CHECK(oversized.red(3, 6));

    const Frame edge = render("8px 6px", "no-repeat", "right 2px bottom 3px");
    const Frame calc = render("8px 6px", "no-repeat",
                              "calc(100% - 2px) calc(100% - 3px)");
    CHECK(edge.rgba == calc.rgba);
    CHECK(edge.green(0, 0));
    CHECK(edge.red(7, 4));

    // Level-3 edge/offset permutations must match their two-component
    // equivalents, not be rejected and disappear at the paint boundary.
    CHECK(render("8px 6px", "no-repeat", "left 2px center").rgba ==
          render("8px 6px", "no-repeat", "2px center").rgba);
    CHECK(render("8px 6px", "no-repeat", "center top 2px").rgba ==
          render("8px 6px", "no-repeat", "center 2px").rgba);
    // In two-value syntax a horizontal keyword is x and the length is y.
    CHECK(render("8px 6px", "no-repeat", "left 2px").rgba ==
          render("8px 6px", "no-repeat", "0 2px").rgba);
    CHECK(render("8px 6px", "no-repeat", "right 2px").rgba ==
          render("8px 6px", "no-repeat", "100% 2px").rgba);
    // Reversed pairs involving `center` are valid two-keyword forms and
    // resolve by axis, not textual order.
    CHECK(render("8px 6px", "no-repeat", "center left").rgba ==
          render("8px 6px", "no-repeat", "left center").rgba);
    CHECK(render("8px 6px", "no-repeat", "top center").rgba ==
          render("8px 6px", "no-repeat", "center top").rgba);
    CHECK(render("8px 6px", "no-repeat", "center right").rgba ==
          render("8px 6px", "no-repeat", "right center").rgba);
    CHECK(render("8px 6px", "no-repeat", "bottom center").rgba ==
          render("8px 6px", "no-repeat", "center bottom").rgba);
    // Leading decimal fractions are valid CSS numbers; their canonical forms
    // must paint identically through the real Skia raster path.
    CHECK(render("8px 6px", "no-repeat", ".5px 0").rgba ==
          render("8px 6px", "no-repeat", "0.5px 0").rgba);
    CHECK(render("8px 6px", "no-repeat", ".5% 0").rgba ==
          render("8px 6px", "no-repeat", "0.5% 0").rgba);
    // Invalid vertical-first keyword + length fails closed rather than using
    // a guessed axis ordering.
    const Frame invalid_vertical_first = render("8px 6px", "no-repeat", "top 2px");
    CHECK(invalid_vertical_first.green(0, 0));
    CHECK(invalid_vertical_first.green(15, 11));

    const Frame repeat_x = render("4px 6px", "repeat-x", "0 0");
    CHECK(repeat_x.red(1, 2));
    CHECK(repeat_x.red(5, 2));
    CHECK(repeat_x.green(1, 8));
    const Frame repeat_y = render("4px 6px", "repeat-y", "0 0");
    CHECK(repeat_y.red(1, 2));
    CHECK(repeat_y.green(5, 2));
    CHECK(repeat_y.red(1, 8));
}

FRAMEWORK_TEST(a_captured_antialiased_text_node_materializes_as_an_antialiased_label) {
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.style.width = 240.0f;
    ir.root.style.height = 60.0f;
    for (const char* text : {"Antialiased", "Default"}) {
        IRNode node;
        node.type = "text";
        node.text_content = text;
        node.style.font_size = 15.0f;
        node.style.width = 120.0f;
        node.style.height = 20.0f;
        if (std::string_view(text) == "Antialiased")
            node.attributes["font_smoothing"] = "antialiased";
        ir.root.children.push_back(node);
    }
    auto root = build_native_view_tree(ir, ir.asset_manifest);
    require(root);
    const auto find = [&](const std::string& text) -> Label* {
        const auto walk = [&](auto&& self, View& view) -> Label* {
            if (auto* label = dynamic_cast<Label*>(&view); label && label->text() == text)
                return label;
            for (std::size_t i = 0; i < view.child_count(); ++i)
                if (auto* child = view.child_at(i))
                    if (auto* found = self(self, *child))
                        return found;
            return nullptr;
        };
        return walk(walk, *root);
    };
    auto* smooth = find("Antialiased");
    auto* plain = find("Default");
    require(smooth != nullptr);
    require(plain != nullptr);
    CHECK(smooth->font_smoothing() == vellum::canvas::FontSmoothing::antialiased);
    CHECK(plain->font_smoothing() == vellum::canvas::FontSmoothing::auto_smoothing);
    CHECK(design_ir_text_raster_policy(ir) == vellum::canvas::TextRasterPolicy::chromium);
}

// Generated JS and the bridge's registerFont carry each captured @font-face
// rule's declared weight range to the text renderer.
namespace {

DesignIR ir_with_face(std::string family, std::string path, int weight, int weight_max) {
    DesignIR ir;
    ir.root.type = "frame";
    ir.root.stable_anchor_id = "root";
    ir.root.style.width = 100.0f;
    ir.root.style.height = 40.0f;
    IRFontAsset face;
    face.family = std::move(family);
    face.resolved_path = std::move(path);
    face.weight = weight;
    face.weight_max = weight_max;
    ir.font_family_assets.push_back(face);
    return ir;
}

} // namespace


FRAMEWORK_TEST(generated_js_registers_a_captured_face_with_its_declared_weight_range) {
    CodeGenOptions opts;
    opts.mode = CodeGenMode::bridge_native_js;
    const auto range = generate_pulp_js(ir_with_face("Probe", "/fonts/Jost.ttf", 400, 700), opts);
    CHECK(range.find("typeof registerFont === 'function') registerFont('Probe', '/fonts/Jost.ttf', "
                     "400, 700);") != std::string::npos);
    const auto single = generate_pulp_js(ir_with_face("Probe", "/fonts/Jost.ttf", 400, 0), opts);
    CHECK(single.find("typeof registerFont === 'function') registerFont('Probe', "
                      "'/fonts/Jost.ttf', 400, 400);") != std::string::npos);
}


int main() { return framework_test::run_all(); }
