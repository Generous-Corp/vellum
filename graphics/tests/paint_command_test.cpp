#include <vellum/graphics/paint_command.hpp>

#include <array>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace {

bool near(float left, float right) {
    return std::abs(left - right) < 0.0001F;
}


bool mesh_commands() {
    using vellum::graphics::MeshTexture;
    using vellum::graphics::MeshVertex;
    using vellum::graphics::TriangleMesh;
    auto mesh = std::make_shared<TriangleMesh>();
    mesh->vertices = {
        {.x = 0.0F, .y = 0.0F},
        {.x = 4.0F, .y = 0.0F, .u = 1.0F},
        {.x = 0.0F, .y = 4.0F, .v = 1.0F},
    };
    mesh->indices = {0, 1, 2};

    vellum::graphics::Scene scene;
    scene.root.bounds = {10.0F, 20.0F, 100.0F, 100.0F};
    scene.root.children.push_back({
        .id = "mesh",
        .kind = vellum::graphics::SceneNode::Kind::mesh,
        .bounds = {1.0F, 2.0F, 0.0F, 0.0F},
        .mesh = mesh,
    });
    // A mesh node without geometry paints nothing.
    scene.root.children.push_back({
        .id = "no-geometry",
        .kind = vellum::graphics::SceneNode::Kind::mesh,
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 1U ||
        commands[0].kind != vellum::graphics::PaintCommand::Kind::mesh ||
        commands[0].node_id != "mesh" || commands[0].mesh != mesh ||
        !near(commands[0].bounds.x, 11.0F) || !near(commands[0].bounds.y, 22.0F)) {
        return false;
    }

    std::string reason;
    if (!vellum::graphics::mesh_is_valid(*mesh, &reason) || !reason.empty()) return false;

    auto expect_invalid = [&](TriangleMesh candidate, const std::string& expected) {
        std::string why;
        return !vellum::graphics::mesh_is_valid(candidate, &why) &&
               why.find(expected) != std::string::npos;
    };
    TriangleMesh partial = *mesh;
    partial.indices.push_back(0);
    TriangleMesh out_of_range = *mesh;
    out_of_range.indices[2] = 3;
    TriangleMesh not_finite = *mesh;
    not_finite.vertices[1].u = std::numeric_limits<float>::quiet_NaN();
    TriangleMesh short_texture = *mesh;
    short_texture.texture = std::make_shared<MeshTexture>(
        MeshTexture{.width = 2, .height = 2, .premultiplied_rgba = std::vector<std::uint8_t>(15)});
    TriangleMesh empty_texture = *mesh;
    empty_texture.texture = std::make_shared<MeshTexture>();
    TriangleMesh bad_clip = *mesh;
    bad_clip.clip_enabled = true;
    bad_clip.clip.width = std::numeric_limits<float>::infinity();
    TriangleMesh good_texture = *mesh;
    good_texture.texture = std::make_shared<MeshTexture>(
        MeshTexture{.width = 2, .height = 1, .premultiplied_rgba = std::vector<std::uint8_t>(8)});
    return expect_invalid(partial, "multiple of three") &&
           expect_invalid(out_of_range, "past the end") &&
           expect_invalid(not_finite, "not finite") &&
           expect_invalid(short_texture, "does not match") &&
           expect_invalid(empty_texture, "no pixels") &&
           expect_invalid(bad_clip, "clip rectangle") &&
           vellum::graphics::mesh_is_valid(good_texture) &&
           vellum::graphics::mesh_is_valid(TriangleMesh{});
}

using vellum::graphics::FillRule;
using vellum::graphics::PathGeometry;
using vellum::graphics::PathVerb;
using vellum::graphics::StrokeCap;
using vellum::graphics::StrokeJoin;

bool expect(bool ok, const std::string& what) {
    if (!ok) std::cerr << "FAILED: " << what << '\n';
    return ok;
}

bool same_values(const std::vector<float>& actual, const std::vector<float>& expected) {
    if (actual.size() != expected.size()) return false;
    for (std::size_t index = 0; index < actual.size(); ++index) {
        if (!near(actual[index], expected[index])) return false;
    }
    return true;
}

bool parses_to(std::string_view data, const std::vector<PathVerb>& verbs,
               const std::vector<float>& values) {
    PathGeometry path;
    std::string reason;
    const bool parsed = vellum::graphics::parse_svg_path_data(data, path, &reason);
    const bool ok = parsed && path.verbs == verbs && same_values(path.values, values) &&
                    vellum::graphics::path_is_valid(path);
    if (!ok) std::cerr << "path data \"" << data << "\" parsed wrongly: " << reason << '\n';
    return ok;
}

bool rejects(std::string_view data, const std::string& expected) {
    PathGeometry path;
    path.verbs = {PathVerb::move};
    path.values = {7.0F, 8.0F};
    std::string reason;
    const bool ok = !vellum::graphics::parse_svg_path_data(data, path, &reason) &&
                    reason.find(expected) != std::string::npos &&
                    path.verbs == std::vector<PathVerb>{PathVerb::move} &&
                    same_values(path.values, {7.0F, 8.0F});
    if (!ok) std::cerr << "path data \"" << data << "\" was not rejected with \"" << expected
                       << "\": " << reason << '\n';
    return ok;
}

bool path_parsing() {
    using V = PathVerb;
    bool ok = true;
    ok &= parses_to("", {}, {});
    ok &= parses_to(" \t\r\n\f", {}, {});
    ok &= parses_to("M10 20 L30 40 Q1 2 3 4 C1 2 3 4 5 6 Z",
                    {V::move, V::line, V::quadratic, V::cubic, V::close},
                    {10, 20, 30, 40, 1, 2, 3, 4, 1, 2, 3, 4, 5, 6});
    ok &= parses_to("m10 20 l5 5 h10 v-5 H0 V0",
                    {V::move, V::line, V::line, V::line, V::line, V::line},
                    {10, 20, 15, 25, 25, 25, 25, 20, 0, 20, 0, 0});
    ok &= parses_to("M0 0 10 0 10 10", {V::move, V::line, V::line}, {0, 0, 10, 0, 10, 10});
    ok &= parses_to("m1 1 2 2 3 3", {V::move, V::line, V::line}, {1, 1, 3, 3, 6, 6});
    ok &= parses_to("M0,0L1,1,2,2", {V::move, V::line, V::line}, {0, 0, 1, 1, 2, 2});
    ok &= parses_to("M0 0 C0 10 10 10 10 0 S20 -10 20 0",
                    {V::move, V::cubic, V::cubic},
                    {0, 0, 0, 10, 10, 10, 10, 0, 10, -10, 20, -10, 20, 0});
    ok &= parses_to("M0 0 s5 5 10 0", {V::move, V::cubic}, {0, 0, 0, 0, 5, 5, 10, 0});
    ok &= parses_to("M0 0 Q5 10 10 0 T20 0 t10 0",
                    {V::move, V::quadratic, V::quadratic, V::quadratic},
                    {0, 0, 5, 10, 10, 0, 15, -10, 20, 0, 25, 10, 30, 0});
    ok &= parses_to("M0 0 L5 5 T10 0", {V::move, V::line, V::quadratic},
                    {0, 0, 5, 5, 5, 5, 10, 0});
    ok &= parses_to("M0 0 A10 -5 30 1 0 20 0 a5 5 0 0 1 10 0",
                    {V::move, V::arc, V::arc},
                    {0, 0, 10, 5, 30, 1, 0, 20, 0, 5, 5, 0, 0, 1, 30, 0});
    ok &= parses_to("M0 0a10 10 0 1120 0", {V::move, V::arc}, {0, 0, 10, 10, 0, 1, 1, 20, 0});
    ok &= parses_to("M1e1-2.5e-1.5.5", {V::move, V::line}, {10, -0.25F, 0.5F, 0.5F});
    ok &= parses_to("M+3-4E+1", {V::move}, {3, -40});
    ok &= parses_to("M10 10 L20 10 Z l5 5 z m1 1 l1 0",
                    {V::move, V::line, V::close, V::move, V::line, V::close, V::move, V::line},
                    {10, 10, 20, 10, 10, 10, 15, 15, 11, 11, 12, 11});
    ok &= parses_to("M0.000000000000000000000001 1000000000000000000000000",
                    {V::move}, {1e-24F, static_cast<float>(1e24)});

    ok &= rejects("L0 0", "must start with a move");
    ok &= rejects("  10 10", "must start with a move");
    ok &= rejects("M0", "expected a number at byte 2");
    ok &= rejects("M0 0 X1 1", "unknown command 'X' at byte 5");
    ok &= rejects("M0 0 L1 1,", "expected a number at byte 10");
    ok &= rejects("M,0 0", "expected a number at byte 1");
    ok &= rejects("M0 0 Z 5", "unknown command '5'");
    ok &= rejects("M0 0 A1 1 0 2 0 1 1", "expected an arc flag of 0 or 1 at byte 12");
    ok &= rejects("M1e 2", "expected an exponent at byte 3");
    ok &= rejects("M- 2", "expected a number at byte 1");
    ok &= rejects("M1e39 0", "a number is out of range at byte 1");
    ok &= rejects("M3e38 0 l3e38 0", "a coordinate is out of range");
    return ok;
}

bool path_budgets() {
    bool ok = true;
    std::string data = "M0 0";
    for (std::size_t index = 1; index < vellum::graphics::kMaxPathCommands; ++index) data += "L1 1";
    PathGeometry path;
    std::string reason;
    ok &= expect(vellum::graphics::parse_svg_path_data(data, path, &reason) &&
                     path.verbs.size() == vellum::graphics::kMaxPathCommands,
                 "path data at the command budget parses: " + reason);
    ok &= rejects(data + "L1 1", "exceeds the 4096-command budget");
    ok &= rejects(data.substr(0, data.size() - 4U) + "Z L1 1", "exceeds the 4096-command budget");

    std::string padded = "M0 0";
    padded.resize(vellum::graphics::kMaxPathDataBytes, ' ');
    ok &= expect(vellum::graphics::parse_svg_path_data(padded, path),
                 "path data of exactly the byte limit parses");
    ok &= rejects(padded + " ", "longer than the 65536-byte limit");

    std::string cubics = "M0 0";
    for (std::size_t index = 0; index < 2730U; ++index) cubics += "C1 2 3 4 5 6";
    ok &= expect(vellum::graphics::parse_svg_path_data(cubics + "L1 1", path) &&
                     path.values.size() == vellum::graphics::kMaxPathValues,
                 "path data at the value budget parses");
    ok &= rejects(cubics + "L1 1L2 2", "exceeds the 16384-value budget");

    PathGeometry lines;
    lines.verbs.assign(vellum::graphics::kMaxPathCommands, PathVerb::line);
    lines.verbs.front() = PathVerb::move;
    lines.values.assign(vellum::graphics::kMaxPathCommands * 2U, 1.0F);
    ok &= expect(vellum::graphics::path_is_valid(lines), "a path at the command budget is valid");
    lines.verbs.push_back(PathVerb::line);
    lines.values.insert(lines.values.end(), {1.0F, 1.0F});
    ok &= expect(!vellum::graphics::path_is_valid(lines, &reason) &&
                     reason == "the path has more than 4096 commands",
                 "a path over the command budget is invalid: " + reason);

    PathGeometry values;
    values.verbs = {PathVerb::move};
    values.values = {0.0F, 0.0F};
    for (std::size_t index = 0; index < 2730U; ++index) {
        values.verbs.push_back(PathVerb::cubic);
        values.values.insert(values.values.end(), 6U, 1.0F);
    }
    values.verbs.push_back(PathVerb::line);
    values.values.insert(values.values.end(), {1.0F, 1.0F});
    ok &= expect(values.values.size() == vellum::graphics::kMaxPathValues &&
                     vellum::graphics::path_is_valid(values),
                 "a path at the value budget is valid");
    values.verbs.push_back(PathVerb::line);
    values.values.insert(values.values.end(), {1.0F, 1.0F});
    ok &= expect(!vellum::graphics::path_is_valid(values, &reason) &&
                     reason == "the path has more than 16384 values",
                 "a path over the value budget is invalid: " + reason);
    return ok;
}

bool path_validity() {
    PathGeometry good;
    if (!vellum::graphics::parse_svg_path_data("M0 0 L10 0 A5 5 0 0 1 10 10 Z", good)) return false;
    good.stroke = {.color = vellum::graphics::Color::hex(0x000000), .width = 2.0F,
                   .dash_array = {0.0F, 3.0F, 1.0F}};
    bool ok = expect(vellum::graphics::path_is_valid(good) &&
                         vellum::graphics::path_is_valid(PathGeometry{}),
                     "a good path and an empty path are valid");
    const auto invalid = [&](auto mutate, const std::string& expected) {
        PathGeometry candidate = good;
        mutate(candidate);
        std::string reason;
        return expect(!vellum::graphics::path_is_valid(candidate, &reason) &&
                          reason.find(expected) != std::string::npos,
                      "expected \"" + expected + "\", got \"" + reason + "\"");
    };
    constexpr float nan = std::numeric_limits<float>::quiet_NaN();
    constexpr float infinity = std::numeric_limits<float>::infinity();
    ok &= invalid([](PathGeometry& p) { p.values.pop_back(); }, "fewer values than its arity");
    ok &= invalid([](PathGeometry& p) { p.values.push_back(1.0F); },
                  "values its commands do not consume");
    ok &= invalid([](PathGeometry& p) { p.verbs.front() = PathVerb::line; },
                  "does not start with a move");
    ok &= invalid([](PathGeometry& p) { p.verbs[1] = static_cast<PathVerb>(42); },
                  "unknown command");
    ok &= invalid([&](PathGeometry& p) { p.values[2] = nan; }, "coordinate is not finite");
    ok &= invalid([&](PathGeometry& p) { p.values[3] = infinity; }, "coordinate is not finite");
    ok &= invalid([](PathGeometry& p) { p.values[4] = -5.0F; }, "arc radius is negative");
    ok &= invalid([](PathGeometry& p) { p.values[7] = 2.0F; }, "arc flag is not 0 or 1");
    ok &= invalid([](PathGeometry& p) { p.fill_rule = static_cast<FillRule>(9); },
                  "fill rule is unknown");
    ok &= invalid([](PathGeometry& p) { p.stroke.join = static_cast<StrokeJoin>(9); },
                  "join is unknown");
    ok &= invalid([](PathGeometry& p) { p.stroke.cap = static_cast<StrokeCap>(9); },
                  "cap is unknown");
    ok &= invalid([&](PathGeometry& p) { p.stroke.color.alpha = nan; }, "colour is not finite");
    ok &= invalid([](PathGeometry& p) { p.stroke.width = -1.0F; }, "stroke width");
    ok &= invalid([&](PathGeometry& p) { p.stroke.width = infinity; }, "stroke width");
    ok &= invalid([](PathGeometry& p) { p.stroke.miter_limit = 0.5F; }, "miter limit");
    ok &= invalid([&](PathGeometry& p) { p.stroke.dash_offset = nan; }, "dash offset");
    ok &= invalid([](PathGeometry& p) { p.stroke.dash_array = {1.0F, -1.0F}; },
                  "stroke dash is not");
    ok &= invalid([](PathGeometry& p) { p.stroke.dash_array = {0.0F, 0.0F}; }, "sums to zero");
    ok &= invalid([](PathGeometry& p) {
        p.stroke.dash_array.assign(vellum::graphics::kMaxPathDashes + 1U, 1.0F);
    }, "more than 64 entries");
    PathGeometry dashes = good;
    dashes.stroke.dash_array.assign(vellum::graphics::kMaxPathDashes, 1.0F);
    ok &= expect(vellum::graphics::path_is_valid(dashes), "a dash array at the limit is valid");

    const auto dashed_line = [](float length, std::vector<float> pattern) {
        PathGeometry line{.verbs = {PathVerb::move, PathVerb::line},
                          .values = {0.0F, 0.0F, length, 0.0F}};
        line.stroke.dash_array = std::move(pattern);
        return line;
    };
    std::string reason;
    ok &= expect(vellum::graphics::path_is_valid(dashed_line(200.0F, {0.001F, 0.001F})),
                 "a dash pattern drawing 100000 dashes is valid");
    ok &= expect(!vellum::graphics::path_is_valid(dashed_line(201.0F, {0.001F, 0.001F}), &reason) &&
                     reason == "the stroke dash pattern would draw more than 100000 dashes",
                 "a dash pattern drawing more than 100000 dashes is invalid: " + reason);
    ok &= expect(vellum::graphics::path_is_valid(dashed_line(200.0F, {0.001F})) &&
                     !vellum::graphics::path_is_valid(dashed_line(201.0F, {0.001F})),
                 "an odd dash array counts one dash per entry over twice its sum");
    ok &= expect(!vellum::graphics::path_is_valid(dashed_line(100.0F, {1e-30F, 1e-30F})),
                 "a vanishing dash pattern is invalid");
    ok &= expect(vellum::graphics::path_is_valid(dashed_line(1.0e6F, {10.0F, 10.0F})),
                 "a long path with a reasonable dash pattern is valid");
    return ok;
}

bool path_bounds_and_outset() {
    bool ok = true;
    const auto bounds_of = [](std::string_view data) {
        PathGeometry path;
        (void)vellum::graphics::parse_svg_path_data(data, path);
        return vellum::graphics::path_control_bounds(path);
    };
    const auto is = [](const std::optional<vellum::graphics::Rect>& rect, float x, float y,
                       float width, float height, float slack = 0.0001F) {
        const bool ok = rect && std::abs(rect->x - x) <= slack && std::abs(rect->y - y) <= slack &&
                        std::abs(rect->width - width) <= slack &&
                        std::abs(rect->height - height) <= slack;
        if (!ok && rect) {
            std::cerr << "bounds are " << rect->x << ' ' << rect->y << ' ' << rect->width << ' '
                      << rect->height << '\n';
        }
        return ok;
    };
    ok &= expect(!bounds_of(""), "an empty path has no bounds");
    ok &= expect(is(bounds_of("M5 5 L15 25"), 5, 5, 10, 20), "line bounds");
    ok &= expect(is(bounds_of("M0 0 C-10 40 50 -20 30 10"), -10, -20, 60, 60),
                 "cubic bounds include the control points");
    ok &= expect(is(bounds_of("M0 0 A10 10 0 0 1 20 0"), 0, -10, 20, 20, 0.05F),
                 "arc bounds include the ellipse");
    ok &= expect(is(bounds_of("M0 0 A1 1 0 0 1 20 0"), 0, -10, 20, 20, 0.05F),
                 "arc radii too small to span are scaled up");
    ok &= expect(is(bounds_of("M0 0 A20 10 90 0 1 0 40"), -10, 0, 20, 40, 0.05F),
                 "rotated arc bounds");
    ok &= expect(is(bounds_of("M0 0 A0 10 0 0 1 20 0"), 0, 0, 20, 0),
                 "a zero-radius arc is a line");
    ok &= expect(is(bounds_of("M10 10 L20 10 Z L10 30"), 10, 10, 10, 20),
                 "a command after close starts at the subpath start");

    const auto length_of = [](std::string_view data) {
        PathGeometry path;
        (void)vellum::graphics::parse_svg_path_data(data, path);
        return vellum::graphics::path_length_bound(path);
    };
    ok &= expect(length_of("") == 0.0, "an empty path has no length");
    ok &= expect(std::abs(length_of("M0 0 L3 4 L3 0 Z") - 12.0) < 1e-9,
                 "polyline length includes the closing segment");
    ok &= expect(std::abs(length_of("M0 0 C0 10 10 10 10 0") - 30.0) < 1e-9,
                 "a cubic is bounded by its control polygon");
    ok &= expect(std::abs(length_of("M0 0 A10 10 0 0 1 20 0") - 20.0 * 3.14159265358979) < 1e-4,
                 "an arc is bounded by its ellipse perimeter");
    ok &= expect(std::abs(length_of("M0 0 A0 10 0 0 1 20 0") - 20.0) < 1e-9,
                 "a zero-radius arc is bounded by its chord");
    ok &= expect(std::abs(length_of("M0 0 L10 0 M100 100 L100 110") - 20.0) < 1e-9,
                 "a move adds no length");

    using vellum::graphics::PaintCommand;
    PathGeometry triangle;
    (void)vellum::graphics::parse_svg_path_data("M2 3 L12 3 L7 11 Z", triangle);
    PaintCommand command{.kind = PaintCommand::Kind::path, .bounds = {5.0F, 6.0F, 30.0F, 40.0F},
                         .path = std::make_shared<const PathGeometry>(triangle)};
    ok &= expect(is(vellum::graphics::path_gradient_box(command), 5, 6, 30, 40),
                 "a gradient resolves against a layout box with area");
    command.bounds = {5.0F, 6.0F, 30.0F, 0.0F};
    ok &= expect(is(vellum::graphics::path_gradient_box(command), 7, 9, 10, 8),
                 "a gradient on an empty layout box resolves against the control box");
    PathGeometry flat;
    (void)vellum::graphics::parse_svg_path_data("M0 0 L10 0", flat);
    command.path = std::make_shared<const PathGeometry>(flat);
    ok &= expect(!vellum::graphics::path_gradient_box(command),
                 "a gradient has no box when neither box has area");

    using vellum::graphics::PathStroke;
    const auto black = vellum::graphics::Color::hex(0x000000);
    const auto outset = [](const PathStroke& stroke) {
        return vellum::graphics::path_stroke_outset(stroke);
    };
    ok &= expect(near(outset({.color = black, .width = 4.0F}), 8.0F), "miter outset");
    ok &= expect(near(outset({.color = black, .width = 4.0F, .join = StrokeJoin::bevel}), 2.0F),
                 "bevel butt outset");
    ok &= expect(near(outset({.color = black, .width = 4.0F, .join = StrokeJoin::round,
                              .cap = StrokeCap::round}), 2.0F * std::sqrt(2.0F)),
                 "round cap outset");
    ok &= expect(near(outset({.color = black, .width = 4.0F, .join = StrokeJoin::bevel,
                              .cap = StrokeCap::square}), 2.0F * std::sqrt(2.0F)),
                 "square cap outset");
    ok &= expect(near(outset({.width = 4.0F}), 0.0F), "a transparent stroke has no outset");
    ok &= expect(near(outset({.color = black}), 0.0F), "a zero-width stroke has no outset");
    return ok;
}

bool path_commands() {
    using vellum::graphics::Color;
    using vellum::graphics::PaintCommand;
    using vellum::graphics::SceneNode;
    PathGeometry star;
    if (!vellum::graphics::parse_svg_path_data("M0 0 L10 0 L5 8 Z", star)) return false;
    star.fill_rule = FillRule::evenodd;
    star.stroke = {.color = Color::hex(0x00FF00), .width = 2.0F, .cap = StrokeCap::round,
                   .dash_array = {3.0F, 1.0F}, .dash_offset = 0.5F};

    vellum::graphics::Scene scene;
    scene.root.bounds = {10.0F, 20.0F, 100.0F, 100.0F};
    scene.root.children.push_back({
        .id = "star",
        .kind = SceneNode::Kind::path,
        .bounds = {1.0F, 2.0F, 30.0F, 40.0F},
        .fill = Color::hex(0xFF0000),
        .fill_gradients = {{.angle_degrees = 45.0F}},
        .box_shadows = {{.spread_radius = 3.0F}},
        .corner_radius = 4.0F,
        .path = std::make_shared<const PathGeometry>(star),
    });
    scene.root.children.push_back({.id = "empty-path", .kind = SceneNode::Kind::path});
    const auto commands = vellum::graphics::make_paint_commands(scene);
    bool ok = expect(commands.size() == 1U, "only the non-empty path emits a command");
    if (!ok) return false;
    const auto& command = commands[0];
    ok &= expect(command.kind == PaintCommand::Kind::path && command.node_id == "star" &&
                     near(command.bounds.x, 11.0F) && near(command.bounds.y, 22.0F) &&
                     near(command.bounds.width, 30.0F) && near(command.bounds.height, 40.0F),
                 "the path command carries absolute bounds");
    ok &= expect(near(command.fill.red, 1.0F) && command.fill_gradients.size() == 1U &&
                     near(command.fill_gradients[0].angle_degrees, 45.0F) &&
                     command.box_shadows.empty() && near(command.corner_radius, 0.0F),
                 "the path command carries the fill paint and no box styling");
    ok &= expect(command.path->verbs == star.verbs &&
                     same_values(command.path->values, star.values) &&
                     command.path->fill_rule == FillRule::evenodd &&
                     command.path->stroke.cap == StrokeCap::round &&
                     same_values(command.path->stroke.dash_array, {3.0F, 1.0F}) &&
                     near(command.path->stroke.dash_offset, 0.5F) && command.path->antialias,
                 "the path command carries the geometry and stroke");

    static_assert(vellum::graphics::is_known_kind(SceneNode::Kind::path));
    static_assert(vellum::graphics::to_string(PaintCommand::Kind::path) == "path");
    vellum::graphics::Scene mixed = scene;
    mixed.root.children.push_back({
        .id = "box", .kind = SceneNode::Kind::rectangle, .bounds = {0.0F, 0.0F, 4.0F, 4.0F}});
    const auto mixed_commands = vellum::graphics::make_paint_commands(mixed);
    constexpr std::array drawn{PaintCommand::Kind::rectangle, PaintCommand::Kind::text};
    const auto report =
        vellum::graphics::report_undrawn_commands(mixed_commands, drawn, "the test renderer");
    using vellum::graphics::LoweringDiagnostic;
    ok &= expect(report.size() == 1U &&
                     report[0].code == vellum::graphics::kNodeContentDroppedCode &&
                     report[0].node_id == "star" && report[0].property == "kind" &&
                     report[0].severity == LoweringDiagnostic::Severity::warning &&
                     report[0].disposition == LoweringDiagnostic::Disposition::unsupported &&
                     report[0].message ==
                         "the test renderer does not draw path commands; path star was dropped",
                 "a renderer without paths reports the dropped path");
    constexpr std::array everything{PaintCommand::Kind::rectangle, PaintCommand::Kind::text,
                                    PaintCommand::Kind::mesh, PaintCommand::Kind::path};
    ok &= expect(vellum::graphics::report_undrawn_commands(mixed_commands, everything, "all")
                     .empty(),
                 "a renderer that draws every kind reports nothing");
    return ok;
}

}  // namespace

bool scene_versioning() {
    using vellum::graphics::SceneNode;
    static_assert(vellum::graphics::kSceneVersion == 1U);
    static_assert(vellum::graphics::scene_version_is_supported(vellum::graphics::kSceneVersion));
    static_assert(!vellum::graphics::scene_version_is_supported(0U));
    static_assert(!vellum::graphics::scene_version_is_supported(2U));

    vellum::graphics::Scene scene;
    if (scene.scene_version != vellum::graphics::kSceneVersion) return false;
    scene.root.children.push_back({
        .id = "future",
        .kind = static_cast<SceneNode::Kind>(42),
        .bounds = {5.0F, 5.0F, 10.0F, 10.0F},
        .children = {{
            .id = "future/child",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
        }},
    });
    scene.root.children.push_back({
        .id = "known",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {1.0F, 1.0F, 4.0F, 4.0F},
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 1U || commands[0].node_id != "known") return false;
    static_assert(!vellum::graphics::is_known_kind(static_cast<SceneNode::Kind>(42)));
    static_assert(vellum::graphics::is_known_kind(SceneNode::Kind::mesh));
    const auto* unknown = vellum::graphics::find_unknown_kind(scene.root);
    if (unknown == nullptr || unknown->id != "future" ||
        vellum::graphics::find_unknown_kind(scene.root.children[1]) != nullptr) {
        return false;
    }

    scene.scene_version = vellum::graphics::kSceneVersion + 1U;
    return vellum::graphics::make_paint_commands(scene).empty();
}

int main() {
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.bounds = {10.0F, 20.0F, 300.0F, 200.0F};
    scene.root.children.push_back({
        .id = "card",
        .kind = vellum::graphics::SceneNode::Kind::rectangle,
        .bounds = {5.0F, 7.0F, 120.0F, 80.0F},
        .fill = vellum::graphics::Color::hex(0x123456),
        .corner_radius = 9.0F,
        .children = {{
            .id = "label",
            .kind = vellum::graphics::SceneNode::Kind::text,
            .bounds = {3.0F, 4.0F, 90.0F, 20.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
            .fill_gradients = {{
                .angle_degrees = 90.0F,
                .stops = {
                    {.position = 0.0F, .color = vellum::graphics::Color::hex(0x000000)},
                    {.position = 1.0F, .color = vellum::graphics::Color::hex(0xFFFFFF)},
                },
            }},
            .box_shadows = {{
                .spread_radius = 2.0F,
                .color = vellum::graphics::Color::hex(0x000000),
            }},
            .text = "Shared core",
            .font_size = 16.0F,
        }},
    });

    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 3 || commands[0].node_id != "card" ||
        commands[1].node_id != "label/background" ||
        commands[1].kind != vellum::graphics::PaintCommand::Kind::rectangle ||
        commands[1].fill_gradients.size() != 1U ||
        commands[1].box_shadows.size() != 1U ||
        commands[2].node_id != "label") {
        return 1;
    }
    if (!near(commands[0].bounds.x, 15.0F) ||
        !near(commands[0].bounds.y, 27.0F) ||
        !near(commands[2].bounds.x, 18.0F) ||
        !near(commands[2].bounds.y, 31.0F) ||
        commands[2].text != "Shared core") {
        return 1;
    }

    const auto gradient = vellum::graphics::resolve_linear_gradient(
        {10.0F, 20.0F, 200.0F, 100.0F},
        {.angle_degrees = 90.0F,
         .repeating = true,
         .stops = {
             {.position = 1.2F, .color = vellum::graphics::Color::hex(0xFFFFFF)},
             {.position = -0.2F, .color = vellum::graphics::Color::hex(0x000000)},
         }});
    if (!near(gradient.start_x, 10.0F) || !near(gradient.start_y, 70.0F) ||
        !near(gradient.end_x, 210.0F) || !near(gradient.end_y, 70.0F) ||
        !gradient.repeating || gradient.stops.size() != 2U ||
        !near(gradient.stops[0].position, 0.0F) ||
        !near(gradient.stops[1].position, 1.0F)) {
        return 1;
    }

    const auto diagonal = vellum::graphics::resolve_linear_gradient(
        {0.0F, 0.0F, 200.0F, 100.0F}, {.angle_degrees = 135.0F});
    if (!near(diagonal.start_x, 25.0F) ||
        !near(diagonal.start_y, -25.0F) ||
        !near(diagonal.end_x, 175.0F) ||
        !near(diagonal.end_y, 125.0F)) {
        return 1;
    }
    bool paths = path_parsing();
    paths &= path_budgets();
    paths &= path_validity();
    paths &= path_bounds_and_outset();
    paths &= path_commands();
    return mesh_commands() && scene_versioning() && paths ? 0 : 1;
}
