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
#include <utility>
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

// Geometry built directly, not parsed, so non-finite values and a missing
// pointer reach the paint commands and must fail closed there.
bool path_fail_closed() {
    using vellum::graphics::PaintCommand;
    using vellum::graphics::SceneNode;
    constexpr float nan = std::numeric_limits<float>::quiet_NaN();
    constexpr float infinity = std::numeric_limits<float>::infinity();
    const auto direct = [](std::vector<PathVerb> verbs, std::vector<float> values) {
        auto geometry = std::make_shared<PathGeometry>();
        geometry->verbs = std::move(verbs);
        geometry->values = std::move(values);
        return geometry;
    };
    const auto nan_line = direct({PathVerb::move, PathVerb::line}, {0.0F, 0.0F, nan, 5.0F});
    const auto inf_line = direct({PathVerb::move, PathVerb::line}, {0.0F, 0.0F, infinity, 5.0F});
    const auto nan_arc = direct({PathVerb::move, PathVerb::arc},
                                {0.0F, 0.0F, nan, 5.0F, 0.0F, 0.0F, 1.0F, 10.0F, 0.0F});
    const auto inf_cubic = direct({PathVerb::move, PathVerb::cubic},
                                  {0.0F, 0.0F, 1.0F, 1.0F, 2.0F, -infinity, 3.0F, 0.0F});
    vellum::graphics::Scene scene;
    for (const auto& [id, geometry] :
         std::vector<std::pair<std::string, std::shared_ptr<const PathGeometry>>>{
             {"ghost", nullptr}, {"nan-line", nan_line}, {"inf-line", inf_line},
             {"nan-arc", nan_arc}, {"inf-cubic", inf_cubic},
             {"empty", std::make_shared<const PathGeometry>()}}) {
        scene.root.children.push_back({
            .id = id, .kind = SceneNode::Kind::path, .bounds = {3.0F, 4.0F, 5.0F, 6.0F},
            .path = geometry});
    }
    const auto commands = vellum::graphics::make_paint_commands(scene);
    bool ok = expect(commands.size() == 5U, "every path except the empty one emits a command");
    for (const auto& command : commands) {
        const auto extent = vellum::graphics::paint_extent(command);
        ok &= expect(command.kind == PaintCommand::Kind::path && !extent.bounded &&
                         near(extent.rect.x, 3.0F) && command.node_id != "empty",
                     command.node_id + " has an unbounded paint extent");
    }
    ok &= expect(!commands.empty() && commands[0].node_id == "ghost" && !commands[0].path,
                 "a path without geometry still emits its command");
    for (const auto& geometry : {nan_line, inf_line, nan_arc, inf_cubic}) {
        std::string reason;
        ok &= expect(!vellum::graphics::path_is_valid(*geometry, &reason) &&
                         reason == "a path coordinate is not finite",
                     "a directly built non-finite path is invalid: " + reason);
    }

    PathGeometry coincident;
    (void)vellum::graphics::parse_svg_path_data("M5 5 A10 10 0 0 1 5 5", coincident);
    const auto point = vellum::graphics::path_control_bounds(coincident);
    ok &= expect(point && near(point->x, 5.0F) && near(point->y, 5.0F) &&
                     near(point->width, 0.0F) && near(point->height, 0.0F) &&
                     vellum::graphics::path_length_bound(coincident) == 0.0,
                 "an arc whose ends coincide is omitted, as in SVG");
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
    scene.root.children.push_back({.id = "empty-path",
                                   .kind = SceneNode::Kind::path,
                                   .path = std::make_shared<const PathGeometry>()});
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
    static_assert(vellum::graphics::draws_content(PaintCommand::Kind::path));
    static_assert(vellum::graphics::to_string(PaintCommand::Kind::end_layer) == "end_layer");

    vellum::graphics::Scene turned = scene;
    turned.root.children[0].transform =
        vellum::graphics::Affine2D{.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
    turned.root.children[0].opacity = 0.5F;
    const auto turned_commands = vellum::graphics::make_paint_commands(turned);
    ok &= expect(turned_commands.size() == 1U &&
                     turned_commands[0].kind == PaintCommand::Kind::path &&
                     near(turned_commands[0].opacity, 0.5F),
                 "a faded path is one command carrying the opacity");
    if (turned_commands.size() == 1U) {
        const auto pivot = vellum::graphics::apply(turned_commands[0].transform, {11.0F, 22.0F});
        const auto turned_point =
            vellum::graphics::apply(turned_commands[0].transform, {21.0F, 22.0F});
        ok &= expect(near(pivot.x, 11.0F) && near(pivot.y, 22.0F) &&
                         near(turned_point.x, 11.0F) && near(turned_point.y, 32.0F),
                     "a path command carries its transform about the node origin");
    }

    const auto extent = vellum::graphics::paint_extent(command);
    ok &= expect(extent.bounded && near(extent.rect.x, 7.0F) && near(extent.rect.y, 18.0F) &&
                     near(extent.rect.width, 18.0F) && near(extent.rect.height, 16.0F),
                 "a path's paint extent is its control box outset by the stroke reach");
    auto broken = std::make_shared<PathGeometry>(star);
    broken->values.pop_back();
    PaintCommand invalid = command;
    invalid.path = broken;
    const auto unbounded = vellum::graphics::paint_extent(invalid);
    ok &= expect(!unbounded.bounded && near(unbounded.rect.x, 11.0F),
                 "an invalid path's paint extent is its unbounded box");
    return ok;
}

bool near_point(vellum::graphics::Point point, float x, float y) {
    return near(point.x, x) && near(point.y, y);
}

bool near_rect(const vellum::graphics::Rect& rect, float x, float y, float width, float height) {
    return near(rect.x, x) && near(rect.y, y) && near(rect.width, width) &&
           near(rect.height, height);
}

vellum::graphics::Affine2D quarter_turn() {
    return {.a = 0.0F, .b = 1.0F, .c = -1.0F, .d = 0.0F};
}

// Transforms pivot on each node's origin and compose down the tree, while
// `bounds` stays untransformed.
bool transform_geometry() {
    using vellum::graphics::Affine2D;
    using vellum::graphics::SceneNode;
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "turned",
        .kind = SceneNode::Kind::group,
        .bounds = {10.0F, 20.0F, 40.0F, 40.0F},
        .children = {{
            .id = "bar",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {5.0F, 5.0F, 10.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFF0000),
        }},
        .transform = quarter_turn(),
    });
    scene.root.children.push_back({
        .id = "scaled",
        .kind = SceneNode::Kind::group,
        .bounds = {10.0F, 20.0F, 40.0F, 40.0F},
        .children = {{
            .id = "nudged",
            .kind = SceneNode::Kind::rectangle,
            .bounds = {5.0F, 5.0F, 2.0F, 2.0F},
            .fill = vellum::graphics::Color::hex(0x00FF00),
            .transform = vellum::graphics::translation(3.0F, 0.0F),
        }},
        .transform = Affine2D{.a = 2.0F, .d = 2.0F},
    });
    scene.root.children.push_back({
        .id = "plain",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {1.0F, 1.0F, 2.0F, 2.0F},
        .transform = Affine2D{},
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 3U || commands[0].node_id != "bar" ||
        commands[1].node_id != "nudged" || commands[2].node_id != "plain") {
        return false;
    }
    const auto& bar = commands[0];
    const auto& nudged = commands[1];
    if (!near_rect(bar.bounds, 15.0F, 25.0F, 10.0F, 4.0F) ||
        !near_point(vellum::graphics::apply(bar.transform, {15.0F, 25.0F}), 5.0F, 25.0F) ||
        !near_point(vellum::graphics::apply(bar.transform, {25.0F, 29.0F}), 1.0F, 35.0F) ||
        !near_rect(vellum::graphics::transformed_bounds(bar.transform, bar.bounds),
                   1.0F, 25.0F, 4.0F, 10.0F)) {
        return false;
    }
    if (!near_point(vellum::graphics::apply(nudged.transform, {15.0F, 25.0F}), 26.0F, 30.0F) ||
        !near_rect(vellum::graphics::transformed_bounds(nudged.transform, nudged.bounds),
                   26.0F, 30.0F, 4.0F, 4.0F) ||
        !vellum::graphics::is_identity(commands[2].transform) ||
        !near_rect(vellum::graphics::transformed_bounds(commands[2].transform,
                                                         commands[2].bounds),
                   1.0F, 1.0F, 2.0F, 2.0F)) {
        return false;
    }
    const auto composed = vellum::graphics::concat(
        vellum::graphics::translation(1.0F, 0.0F), Affine2D{.a = 2.0F, .d = 2.0F});
    if (!near_point(vellum::graphics::apply(composed, {1.0F, 1.0F}), 3.0F, 2.0F)) return false;

    auto broken = scene;
    broken.root.children[1].transform->c = std::numeric_limits<float>::infinity();
    const auto kept = vellum::graphics::make_paint_commands(broken);
    const auto* found = vellum::graphics::find_non_finite_compositing(broken.root);
    return kept.size() == 2U && kept[1].node_id == "plain" && found != nullptr &&
           found->id == "scaled" &&
           vellum::graphics::find_non_finite_compositing(scene.root) == nullptr;
}

// Group opacity folds into a lone command and otherwise opens a layer.
bool opacity_nesting() {
    using vellum::graphics::PaintCommand;
    using vellum::graphics::SceneNode;
    const auto box = [](std::string id, float opacity = 1.0F) {
        return SceneNode{
            .id = std::move(id),
            .kind = SceneNode::Kind::rectangle,
            .bounds = {0.0F, 0.0F, 4.0F, 4.0F},
            .fill = vellum::graphics::Color::hex(0xFFFFFF),
            .opacity = opacity,
        };
    };
    vellum::graphics::Scene scene;
    scene.root.id = "root";
    scene.root.children.push_back({
        .id = "pair",
        .kind = SceneNode::Kind::group,
        .bounds = {2.0F, 3.0F, 10.0F, 10.0F},
        .children = {box("pair/a"), box("pair/b", 0.5F)},
        .transform = quarter_turn(),
        .opacity = 0.5F,
    });
    scene.root.children.push_back({
        .id = "single",
        .kind = SceneNode::Kind::group,
        .children = {box("single/a", 0.5F)},
        .opacity = 0.25F,
    });
    scene.root.children.push_back(box("clear", 0.0F));
    scene.root.children.back().children.push_back(box("clear/child"));
    scene.root.children.push_back(box("clamped", 4.0F));
    scene.root.children.push_back({
        .id = "empty",
        .kind = SceneNode::Kind::group,
        .opacity = 0.5F,
    });
    scene.root.children.push_back({
        .id = "caption",
        .kind = SceneNode::Kind::text,
        .box_shadows = {{.color = vellum::graphics::Color::hex(0x000000)}},
        .text = "Hi",
        .opacity = 0.5F,
    });

    const auto commands = vellum::graphics::make_paint_commands(scene);
    const std::vector<std::pair<PaintCommand::Kind, std::string>> expected{
        {PaintCommand::Kind::begin_layer, "pair"},
        {PaintCommand::Kind::rectangle, "pair/a"},
        {PaintCommand::Kind::rectangle, "pair/b"},
        {PaintCommand::Kind::end_layer, "pair"},
        {PaintCommand::Kind::rectangle, "single/a"},
        {PaintCommand::Kind::rectangle, "clamped"},
        {PaintCommand::Kind::begin_layer, "caption"},
        {PaintCommand::Kind::rectangle, "caption/background"},
        {PaintCommand::Kind::text, "caption"},
        {PaintCommand::Kind::end_layer, "caption"},
    };
    if (commands.size() != expected.size()) return false;
    for (std::size_t index = 0; index < expected.size(); ++index) {
        if (commands[index].kind != expected[index].first ||
            commands[index].node_id != expected[index].second) {
            return false;
        }
    }
    const std::vector<float> paint{0.5F, 1.0F, 0.5F, 1.0F, 0.125F, 1.0F, 0.5F, 1.0F, 1.0F, 1.0F};
    const std::vector<float> effective{
        0.5F, 0.5F, 0.25F, 1.0F, 0.125F, 1.0F, 0.5F, 0.5F, 0.5F, 1.0F};
    const auto flattened = vellum::graphics::effective_opacities(commands);
    for (std::size_t index = 0; index < commands.size(); ++index) {
        if (!near(commands[index].opacity, paint[index]) ||
            !near(flattened[index], effective[index])) {
            return false;
        }
    }
    if (!near_rect(commands[0].bounds, -2.0F, 3.0F, 4.0F, 4.0F) ||
        commands[0].layer_unbounded || !vellum::graphics::is_identity(commands[0].transform) ||
        !commands[6].layer_unbounded ||
        vellum::graphics::draws_content(PaintCommand::Kind::begin_layer) ||
        vellum::graphics::draws_content(PaintCommand::Kind::end_layer) ||
        !vellum::graphics::draws_content(PaintCommand::Kind::mesh)) {
        return false;
    }
    const std::vector<PaintCommand> nested{
        {.kind = PaintCommand::Kind::begin_layer, .opacity = 0.5F},
        {.kind = PaintCommand::Kind::begin_layer, .opacity = 0.5F},
        {.kind = PaintCommand::Kind::rectangle, .opacity = 0.5F},
        {.kind = PaintCommand::Kind::end_layer},
        {.kind = PaintCommand::Kind::rectangle},
        {.kind = PaintCommand::Kind::end_layer},
        {.kind = PaintCommand::Kind::end_layer},
        {.kind = PaintCommand::Kind::rectangle},
    };
    const auto deep = vellum::graphics::effective_opacities(nested);
    const std::vector<float> deep_expected{0.5F, 0.25F, 0.125F, 0.5F, 0.5F, 1.0F, 1.0F, 1.0F};
    for (std::size_t index = 0; index < deep.size(); ++index) {
        if (!near(deep[index], deep_expected[index])) return false;
    }
    auto broken = scene;
    broken.root.children[0].opacity = std::numeric_limits<float>::quiet_NaN();
    const auto kept = vellum::graphics::make_paint_commands(broken);
    return deep.size() == nested.size() && kept.size() == expected.size() - 4U &&
           kept[0].node_id == "single/a";
}

bool corner_radii() {
    using vellum::graphics::CornerRadii;
    using vellum::graphics::SceneNode;
    vellum::graphics::Scene scene;
    scene.root.children.push_back({
        .id = "shorthand",
        .kind = SceneNode::Kind::rectangle,
        .corner_radius = 5.0F,
    });
    scene.root.children.push_back({
        .id = "corners",
        .kind = SceneNode::Kind::rectangle,
        .corner_radius = 5.0F,
        .corner_radii = CornerRadii{1.0F, 2.0F, -3.0F, 4.0F},
    });
    scene.root.children.push_back({
        .id = "label",
        .kind = SceneNode::Kind::text,
        .fill_gradients = {{.stops = {{}, {.position = 1.0F}}}},
        .text = "x",
        .corner_radii = CornerRadii{0.0F, 6.0F, 0.0F, 0.0F},
    });
    const auto commands = vellum::graphics::make_paint_commands(scene);
    if (commands.size() != 4U) return false;
    const auto& shorthand = commands[0].corner_radii;
    const auto& corners = commands[1].corner_radii;
    const auto& background = commands[2].corner_radii;
    if (!vellum::graphics::is_uniform(shorthand) || shorthand.top_left != 5.0F ||
        commands[0].corner_radius != 5.0F || vellum::graphics::is_uniform(corners) ||
        corners.top_left != 1.0F || corners.top_right != 2.0F ||
        corners.bottom_right != 0.0F || corners.bottom_left != 4.0F ||
        commands[2].node_id != "label/background" || background.top_right != 6.0F ||
        !vellum::graphics::is_rounded(background) ||
        vellum::graphics::is_rounded(CornerRadii{})) {
        return false;
    }
    scene.root.children[1].corner_radii->top_left = std::numeric_limits<float>::quiet_NaN();
    const auto* found = vellum::graphics::find_non_finite_compositing(scene.root);
    return found != nullptr && found->id == "corners";
}

// A version 1 scene that sets a version 2 field yields no commands at all.
bool legacy_scene_fields() {
    using vellum::graphics::SceneNode;
    vellum::graphics::Scene scene;
    scene.scene_version = 1U;
    scene.root.children.push_back({
        .id = "plain",
        .kind = SceneNode::Kind::rectangle,
        .bounds = {1.0F, 1.0F, 4.0F, 4.0F},
        .corner_radius = 2.0F,
        .transform = vellum::graphics::Affine2D{},
    });
    if (!vellum::graphics::scene_fields_match_version(scene) ||
        vellum::graphics::make_paint_commands(scene).size() != 1U) {
        return false;
    }
    const auto refused = [&](auto change) {
        auto candidate = scene;
        change(candidate.root.children[0]);
        const auto* found = vellum::graphics::find_version_2_fields(candidate.root);
        auto upgraded = candidate;
        upgraded.scene_version = 2U;
        return !vellum::graphics::scene_fields_match_version(candidate) &&
               vellum::graphics::make_paint_commands(candidate).empty() &&
               found != nullptr && found->id == "plain" &&
               vellum::graphics::scene_fields_match_version(upgraded);
    };
    return refused([](SceneNode& node) { node.transform = quarter_turn(); }) &&
           refused([](SceneNode& node) { node.opacity = 0.5F; }) &&
           refused([](SceneNode& node) { node.opacity = 2.0F; }) &&
           refused([](SceneNode& node) {
               node.opacity = std::numeric_limits<float>::quiet_NaN();
           }) &&
           refused([](SceneNode& node) {
               node.corner_radii = vellum::graphics::CornerRadii{2.0F, 2.0F, 2.0F, 2.0F};
           });
}

// Shadows grow the paint extent by offset, spread, and 2.5 blur radii (five sigma), and a
// mesh's extent is its clipped vertex box.
bool paint_extents() {
    using vellum::graphics::PaintCommand;
    const PaintCommand shaded{
        .kind = PaintCommand::Kind::rectangle,
        .bounds = {10.0F, 10.0F, 20.0F, 10.0F},
        .box_shadows = {{.offset_x = 4.0F, .blur_radius = 2.0F, .spread_radius = 1.0F},
                        {.offset_y = -6.0F}},
    };
    const auto extent = vellum::graphics::paint_extent(shaded);
    auto mesh = std::make_shared<vellum::graphics::TriangleMesh>();
    mesh->vertices = {{.x = 1.0F, .y = 2.0F}, {.x = 9.0F, .y = 2.0F}, {.x = 1.0F, .y = 8.0F}};
    mesh->indices = {0, 1, 2};
    mesh->clip_enabled = true;
    mesh->clip = {0.0F, 0.0F, 5.0F, 50.0F};
    const auto mesh_extent = vellum::graphics::paint_extent(
        {.kind = PaintCommand::Kind::mesh, .bounds = {100.0F, 100.0F, 0.0F, 0.0F},
         .mesh = mesh});
    const auto text_extent = vellum::graphics::paint_extent(
        {.kind = PaintCommand::Kind::text, .bounds = {1.0F, 2.0F, 3.0F, 4.0F}});
    return extent.bounded && near_rect(extent.rect, 8.0F, 4.0F, 32.0F, 22.0F) &&
           mesh_extent.bounded && near_rect(mesh_extent.rect, 101.0F, 102.0F, 4.0F, 6.0F) &&
           !text_extent.bounded && near_rect(text_extent.rect, 1.0F, 2.0F, 3.0F, 4.0F);
}

}  // namespace

// A version 1 or 2 scene with a path node yields no commands at all.
bool legacy_scene_paths() {
    using vellum::graphics::SceneNode;
    PathGeometry line{.verbs = {PathVerb::move, PathVerb::line}, .values = {0.0F, 0.0F, 4.0F, 4.0F}};
    vellum::graphics::Scene scene;
    scene.root.children.push_back({
        .id = "box", .kind = SceneNode::Kind::rectangle, .bounds = {0.0F, 0.0F, 4.0F, 4.0F}});
    bool ok = true;
    for (const std::uint32_t version : {1U, 2U}) {
        scene.scene_version = version;
        ok &= expect(vellum::graphics::scene_fields_match_version(scene) &&
                         vellum::graphics::make_paint_commands(scene).size() == 1U,
                     "an older scene without a path still draws");
    }
    scene.root.children[0].children.push_back({
        .id = "line",
        .kind = SceneNode::Kind::path,
        .path = std::make_shared<const PathGeometry>(line),
    });
    for (const std::uint32_t version : {1U, 2U}) {
        scene.scene_version = version;
        const auto* found = vellum::graphics::find_version_3_kinds(scene.root);
        ok &= expect(!vellum::graphics::scene_fields_match_version(scene) &&
                         vellum::graphics::make_paint_commands(scene).empty() &&
                         found != nullptr && found->id == "line",
                     "an older scene with a path is refused");
    }
    scene.scene_version = 3U;
    ok &= expect(vellum::graphics::scene_fields_match_version(scene) &&
                     vellum::graphics::make_paint_commands(scene).size() == 2U,
                 "a version 3 scene draws its path");
    return ok;
}

bool scene_versioning() {
    using vellum::graphics::SceneNode;
    static_assert(vellum::graphics::kSceneVersion == 5U);
    static_assert(vellum::graphics::kMinSupportedSceneVersion == 1U);
    static_assert(vellum::graphics::scene_version_is_supported(vellum::graphics::kSceneVersion));
    static_assert(vellum::graphics::scene_version_is_supported(1U));
    static_assert(vellum::graphics::scene_version_is_supported(2U));
    static_assert(!vellum::graphics::scene_version_is_supported(0U));
    static_assert(vellum::graphics::scene_version_is_supported(3U));
    static_assert(vellum::graphics::scene_version_is_supported(4U));
    static_assert(!vellum::graphics::scene_version_is_supported(6U));

    vellum::graphics::Scene scene;
    if (scene.scene_version != vellum::graphics::kSceneVersion || scene.scene_version != 5U) {
        return false;
    }
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
    if (!vellum::graphics::make_paint_commands(scene).empty()) return false;
    return legacy_scene_fields() && paint_extents() && legacy_scene_paths();
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
    paths &= path_fail_closed();
    return mesh_commands() && scene_versioning() && transform_geometry() &&
                   opacity_nesting() && corner_radii() && paths
               ? 0
               : 1;
}
