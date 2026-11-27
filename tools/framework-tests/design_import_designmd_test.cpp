// Checks the DESIGN.md importer's `css-vars` export and its color validation:
// the export's naming, prefix, ordering and value handling, and the color
// shapes the lint accepts or flags (angle units, `color-mix()` weights).

#include "framework_test.hpp"

#include <vellum/view/design_sources.hpp>

#include <algorithm>
#include <string>

using namespace vellum::view;

namespace {

bool contains(const std::string& text, const std::string& needle) {
    return text.find(needle) != std::string::npos;
}

DesignMdParseResult parse_colors(const std::string& color_lines) {
    return parse_designmd("---\nname: Test\ncolors:\n" + color_lines + "---\n");
}

bool has_color_shape_warning(const DesignMdParseResult& parsed) {
    return std::any_of(parsed.diagnostics.begin(), parsed.diagnostics.end(),
                       [](const DesignMdDiagnostic& d) { return d.code == "color-shape"; });
}

const char* kSystem = R"(---
name: Test
colors:
  primary: "#112233"
  background:
    light: "#ffffff"
rounded:
  sm: 4px
spacing:
  base: 8
typography:
  body:
    fontFamily: Inter
    fontSize: 16px
---
)";

} // namespace

FRAMEWORK_TEST(css_vars_emits_a_root_block_with_group_qualified_names) {
    const std::string css = export_css_vars(parse_designmd(kSystem));
    CHECK(css.rfind(":root {\n", 0) == 0);
    CHECK(contains(css, "  --color-primary: #112233;\n"));
    CHECK(contains(css, "  --color-background-light: #ffffff;\n"));
    CHECK(contains(css, "  --rounded-sm: 4px;\n"));
    CHECK(contains(css, "  --spacing-base: 8px;\n"));
    CHECK(contains(css, "  --typography-body-fontFamily: Inter;\n"));
    CHECK(css.size() >= 2 && css.compare(css.size() - 2, 2, "}\n") == 0);
}

FRAMEWORK_TEST(css_vars_prefix_is_applied_and_normalized) {
    const auto parsed = parse_designmd(kSystem);
    CHECK(contains(export_css_vars(parsed, "ds"), "  --ds-color-primary: #112233;\n"));
    CHECK(contains(export_css_vars(parsed, "--my ds--"), "  --my-ds-color-primary: #112233;\n"));
    CHECK(contains(export_css_vars(parsed, "---"), "  --color-primary: #112233;\n"));
    CHECK(!contains(export_css_vars(parsed, "ds"), "--color-primary"));
}

FRAMEWORK_TEST(css_vars_output_is_sorted_and_repeatable) {
    const auto parsed = parse_designmd(kSystem);
    const std::string first = export_css_vars(parsed);
    CHECK(first == export_css_vars(parse_designmd(kSystem)));
    const auto at = [&](const char* name) { return first.find(name); };
    CHECK(at("--color-background-light") < at("--color-primary"));
    CHECK(at("--color-primary") < at("--rounded-sm"));
    CHECK(at("--rounded-sm") < at("--spacing-base"));
    CHECK(at("--spacing-base") < at("--typography-body-fontFamily"));
}

FRAMEWORK_TEST(css_vars_sanitizes_names_and_values_and_drops_collisions) {
    const auto parsed = parse_designmd(
        "---\nname: Test\ncolors:\n  \"a b\": \"#000000\"\n  a.b: \"#111111\"\n"
        "typography:\n  body:\n    fontFamily: \"Inter;} body { x: y\"\n---\n");
    const std::string css = export_css_vars(parsed);
    CHECK(contains(css, "  --color-a-b: #"));
    CHECK(css.find("--color-a-b:") == css.rfind("--color-a-b:"));
    CHECK(!contains(css, "} body"));
    CHECK(!contains(css, "Inter;"));
}

FRAMEWORK_TEST(css_vars_of_an_empty_system_is_an_empty_block) {
    CHECK(export_css_vars(parse_designmd("---\nname: Empty\n---\n")) == ":root {\n}\n");
}

FRAMEWORK_TEST(color_mix_requires_explicit_percentage_weights) {
    CHECK(!has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, red 30%, blue)\"\n")));
    CHECK(!has_color_shape_warning(
        parse_colors("  a: \"color-mix(in oklch, #ff0000, #0000ff 25%)\"\n")));
    CHECK(!has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, 40% red, blue 60%)\"\n")));
    CHECK(!has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, rgb(1 2 3) 50%, hsl(10 20% 30%))\"\n")));
    CHECK(has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, red 30, blue)\"\n")));
    CHECK(has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, red, blue 0.5)\"\n")));
    CHECK(has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, 30 red, blue)\"\n")));
    CHECK(has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, red 130%, blue)\"\n")));
    CHECK(has_color_shape_warning(
        parse_colors("  a: \"color-mix(in srgb, red 30%)\"\n")));
}

FRAMEWORK_TEST(angle_units_in_color_functions_are_valid_colors) {
    CHECK(!has_color_shape_warning(parse_colors("  a: \"hsl(100grad 50% 50%)\"\n")));
    CHECK(!has_color_shape_warning(parse_colors("  a: \"hwb(0.25turn 10% 10%)\"\n")));
    CHECK(!has_color_shape_warning(parse_colors("  a: \"oklch(60% 0.2 1.5rad)\"\n")));
    CHECK(!has_color_shape_warning(parse_colors("  a: \"lch(50% 40 120deg)\"\n")));
}

int main() { return framework_test::run_all(); }
