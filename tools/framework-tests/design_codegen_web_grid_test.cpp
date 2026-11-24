// A multi-column display:grid node lowers onto wrapped flex rows in the
// web-compat code generator. The cards' live bounds after loading the
// generated script show the rows.

#include "framework_test.hpp"

#include <vellum/state/store.hpp>
#include <vellum/view/design_codegen.hpp>
#include <vellum/view/script_engine.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widget_bridge.hpp>

#include <cmath>
#include <set>
#include <string>
#include <string_view>

namespace {

namespace view = vellum::view;

constexpr float kCardWidth = 156.0f;
constexpr float kCardHeight = 146.0f;
constexpr float kGap = 18.0f;
constexpr float kPad = 27.0f;
constexpr float kContentWidth = kCardWidth * 4.0f + kGap * 3.0f;

bool near(float a, float b) { return std::fabs(a - b) <= 0.5f; }

view::View* find_anchor(view::View& root, std::string_view anchor) {
    if (root.anchor_id() == anchor)
        return &root;
    for (size_t i = 0; i < root.child_count(); ++i)
        if (auto* child = root.child_at(i))
            if (auto* found = find_anchor(*child, anchor))
                return found;
    return nullptr;
}

view::DesignIR grid_design() {
    view::DesignIR ir;
    ir.root.type = "frame";
    ir.root.name = "Panel";
    ir.root.stable_anchor_id = "panel";
    ir.root.style.width = kContentWidth + kPad * 2.0f;
    ir.root.style.height = 420.0f;
    ir.root.layout.display = "flex";
    ir.root.layout.direction = view::LayoutDirection::column;
    ir.root.layout.padding_top = kPad;
    ir.root.layout.padding_right = kPad;
    ir.root.layout.padding_bottom = kPad;
    ir.root.layout.padding_left = kPad;

    view::IRNode grid;
    grid.type = "frame";
    grid.name = "Controls";
    grid.stable_anchor_id = "controls";
    grid.style.height = kCardHeight * 2.0f + kGap;
    grid.layout.display = "grid";
    grid.layout.direction = view::LayoutDirection::row;
    grid.layout.gap = kGap;
    grid.layout.grid_template_columns = "156px 156px 156px 156px";
    grid.layout.grid_auto_flow = "row";
    for (int i = 0; i < 6; ++i) {
        view::IRNode card;
        card.type = "frame";
        card.name = "Card";
        card.stable_anchor_id = "card." + std::to_string(i);
        card.style.width = kCardWidth;
        card.style.height = kCardHeight;
        card.layout.flex_shrink = 0.0f;
        card.layout.display = "flex";
        card.layout.align = view::LayoutAlign::center;
        card.layout.gap = 8.0f;
        card.layout.padding_top = 14.0f;
        card.layout.padding_bottom = 12.0f;
        card.layout.padding_left = 10.0f;
        card.layout.padding_right = 10.0f;
        view::IRNode dial;
        dial.type = "frame";
        dial.name = "Dial";
        dial.style.width = 62.0f;
        dial.style.height = 62.0f;
        card.children.push_back(dial);
        grid.children.push_back(card);
    }
    ir.root.children.push_back(grid);
    return ir;
}

} // namespace

FRAMEWORK_TEST(a_multi_column_grid_lowers_onto_wrapped_rows) {
    view::CodeGenOptions opts;
    opts.mode = view::CodeGenMode::web_compat;
    opts.include_comments = false;
    opts.include_tokens = false;
    const auto js = view::generate_pulp_js(grid_design(), opts);

    view::ScriptEngine engine;
    view::View host;
    host.set_bounds({0, 0, 1600, 1200});
    vellum::state::StateStore store;
    view::WidgetBridge bridge(engine, host, store);
    bridge.load_script(js);
    host.layout_children();

    auto* controls = find_anchor(host, "controls");
    CHECK(controls != nullptr);
    if (!controls)
        return;
    CHECK(controls->child_count() == 6);
    CHECK(near(controls->bounds().width, kContentWidth));

    std::set<int> rows;
    for (size_t i = 0; i < controls->child_count(); ++i) {
        const view::View* card = controls->child_at(i);
        CHECK(card != nullptr);
        if (!card)
            continue;
        const auto b = card->bounds();
        CHECK(near(b.width, kCardWidth));
        CHECK(b.x >= -0.5f);
        CHECK(b.x + b.width <= controls->bounds().width + 0.5f);
        CHECK(b.y + b.height <= controls->bounds().height + 0.5f);
        CHECK(near(b.x, static_cast<float>(i % 4) * (kCardWidth + kGap)));
        CHECK(near(b.y, static_cast<float>(i / 4) * (kCardHeight + kGap)));
        rows.insert(static_cast<int>(b.y + 0.5f));
    }
    CHECK(rows.size() == 2);
}

FRAMEWORK_TEST(a_single_column_grid_keeps_its_lowering) {
    auto ir = grid_design();
    ir.root.children[0].layout.grid_template_columns = "156px";
    ir.root.children[0].layout.direction = view::LayoutDirection::column;
    view::CodeGenOptions opts;
    opts.mode = view::CodeGenMode::web_compat;
    opts.include_comments = false;
    opts.include_tokens = false;
    const auto js = view::generate_pulp_js(ir, opts);
    CHECK(js.find("Controls0.style.flexWrap") == std::string::npos);
}

FRAMEWORK_TEST(a_column_flow_grid_keeps_its_lowering) {
    auto ir = grid_design();
    ir.root.children[0].layout.grid_auto_flow = "column";
    ir.root.children[0].layout.direction = view::LayoutDirection::column;
    view::CodeGenOptions opts;
    opts.mode = view::CodeGenMode::web_compat;
    opts.include_comments = false;
    opts.include_tokens = false;
    const auto js = view::generate_pulp_js(ir, opts);
    CHECK(js.find("Controls0.style.flexWrap") == std::string::npos);
}

int main() { return framework_test::run_all(); }
