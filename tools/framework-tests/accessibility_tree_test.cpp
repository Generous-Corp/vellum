// Cross-platform a11y test harness. Verifies snapshot_accessibility_tree walks
// the View tree and captures role/label/value metadata without needing a
// platform screen reader.

#include "catch_shim.hpp"
#include <vellum/view/accessibility.hpp>
#include <vellum/view/accessibility_provider.hpp>
#include <vellum/view/accessibility_tree.hpp>
#include <vellum/view/platform/atspi_mapping.hpp>
#include <vellum/view/buttons.hpp>
#include <vellum/view/scroll_bar.hpp>
#include <vellum/view/table.hpp>
#include <vellum/view/text_editor.hpp>
#include <vellum/view/ui_components.hpp>
#include <vellum/view/view.hpp>
#include <vellum/view/widgets.hpp>

#include <algorithm>
#include <memory>
#include <string>
#include <utility>


// Meter lives with the audio widgets, which Pulp owns; its role and value cases
// are tested there, so the widget lists below leave it out.

using namespace vellum::view;

namespace {

class Probe : public View {};

class RangeProbe : public View, public AccessibilityValueInterface {
public:
    explicit RangeProbe(double current) : current_(current) {}

    double get_current_value() const override { return current_; }
    void set_current_value(double value) override { current_ = value; }
    double get_minimum_value() const override { return -12.0; }
    double get_maximum_value() const override { return 12.0; }

private:
    double current_ = 0.0;
};

class DegenerateRangeProbe : public View, public AccessibilityValueInterface {
public:
    explicit DegenerateRangeProbe(double current) : current_(current) {}

    double get_current_value() const override { return current_; }
    void set_current_value(double value) override { current_ = value; }
    double get_minimum_value() const override { return 5.0; }
    double get_maximum_value() const override { return 5.0; }

private:
    double current_ = 0.0;
};

class TextProbe : public AccessibilityTextInterface {
public:
    explicit TextProbe(std::string text) : text_(std::move(text)) {}

    std::string get_text() const override { return text_; }
    void set_text(std::string_view text) override { text_ = std::string(text); }

private:
    std::string text_;
};

class TextViewProbe : public View, public AccessibilityTextInterface {
  public:
    explicit TextViewProbe(std::string text) : text_(std::move(text)) {}

    std::string get_text() const override {
        return text_;
    }
    void set_text(std::string_view text) override {
        text_ = std::string(text);
    }

  private:
    std::string text_;
};

class TableProbe : public AccessibilityTableInterface {
public:
    int get_row_count() const override { return 3; }
    int get_column_count() const override { return 2; }
    std::string get_column_header(int column) const override {
        return column == 0 ? "Name" : "Value";
    }
};

class CellProbe : public AccessibilityCellInterface {
public:
    std::string get_cell_text(int row, int column) const override {
        return std::to_string(row) + ":" + std::to_string(column);
    }
};

} // namespace

TEST_CASE("snapshot captures role + label + value", "[a11y][harness]") {
    Probe root;
    root.set_access_role(View::AccessRole::group);
    root.set_access_label("Root");

    auto knob = std::make_unique<Probe>();
    knob->set_access_role(View::AccessRole::slider);
    knob->set_access_label("Gain");
    knob->set_access_value("0 dB");
    root.add_child(std::move(knob));

    auto label = std::make_unique<Probe>();
    label->set_access_role(View::AccessRole::label);
    label->set_access_label("Title");
    root.add_child(std::move(label));

    // root excluded to match platform bridge output;
    // only descendants reported, child depth resets to 0.
    auto nodes = snapshot_accessibility_tree(root);
    REQUIRE(nodes.size() == 2);
    REQUIRE(nodes[0].role == View::AccessRole::slider);
    REQUIRE(nodes[0].label == "Gain");
    REQUIRE(nodes[0].value == "0 dB");
    REQUIRE(nodes[0].depth == 0);
    REQUIRE(nodes[1].role == View::AccessRole::label);
    REQUIRE(nodes[1].depth == 0);
}

TEST_CASE("snapshot excludes root even when it is announceable",
          "[a11y][harness]") {
    Probe root;
    root.set_access_role(View::AccessRole::group);
    root.set_access_label("Root");

    REQUIRE(snapshot_accessibility_tree(root).empty());
    REQUIRE(count_announceable(root) == 0);
    REQUIRE(find_by_role_and_label(root, View::AccessRole::group, "Root")
            == nullptr);
}

TEST_CASE("count_announceable excludes AccessRole::none", "[a11y][harness]") {
    Probe root;
    // root has role::none by default
    auto a = std::make_unique<Probe>();
    a->set_access_role(View::AccessRole::slider);
    a->set_access_label("Gain");
    root.add_child(std::move(a));
    auto b = std::make_unique<Probe>();
    // b stays none - invisible to screen readers
    root.add_child(std::move(b));
    auto c = std::make_unique<Probe>();
    c->set_access_role(View::AccessRole::meter);
    c->set_access_label("Output");
    root.add_child(std::move(c));
    REQUIRE(count_announceable(root) == 2);

    // ...and a role with NOTHING to say (no name, no value source, no state) is
    // excluded too: a bare Probe carrying `slider` announces "slider" and then
    // falls silent. Real widgets carry a value interface (Knob) or a name.
    auto d = std::make_unique<Probe>();
    d->set_access_role(View::AccessRole::slider);
    root.add_child(std::move(d));
    REQUIRE(count_announceable(root) == 2);
}

TEST_CASE("snapshot walks through non-announceable containers",
          "[a11y][harness]") {
    Probe root;

    auto hidden_container = std::make_unique<Probe>();
    hidden_container->set_access_label("Layout Only");

    auto meter = std::make_unique<Probe>();
    meter->set_access_role(View::AccessRole::meter);
    meter->set_access_label("Output");
    hidden_container->add_child(std::move(meter));

    root.add_child(std::move(hidden_container));

    auto nodes = snapshot_accessibility_tree(root);
    REQUIRE(nodes.size() == 2);
    REQUIRE(nodes[0].role == View::AccessRole::none);
    REQUIRE(nodes[0].label == "Layout Only");
    REQUIRE(nodes[0].depth == 0);
    REQUIRE(nodes[1].role == View::AccessRole::meter);
    REQUIRE(nodes[1].label == "Output");
    REQUIRE(nodes[1].depth == 1);
    REQUIRE(count_announceable(root) == 1);
}

TEST_CASE("find_by_role_and_label returns nullptr when absent",
          "[a11y][harness]") {
    Probe root;
    root.set_access_role(View::AccessRole::group);
    root.set_access_label("Root");
    auto knob = std::make_unique<Probe>();
    knob->set_access_role(View::AccessRole::slider);
    knob->set_access_label("Gain");
    root.add_child(std::move(knob));

    REQUIRE(find_by_role_and_label(root, View::AccessRole::slider, "Gain")
            != nullptr);
    REQUIRE(find_by_role_and_label(root, View::AccessRole::slider, "Absent")
            == nullptr);
    REQUIRE(find_by_role_and_label(root, View::AccessRole::meter, "Gain")
            == nullptr);
}

TEST_CASE("snapshot preserves depth-first order and first matching view pointer",
          "[a11y][harness]") {
    Probe root;

    auto group = std::make_unique<Probe>();
    group->set_access_role(View::AccessRole::group);
    group->set_access_label("Strip");
    auto* group_ptr = group.get();

    auto first_gain = std::make_unique<Probe>();
    first_gain->set_access_role(View::AccessRole::slider);
    first_gain->set_access_label("Gain");
    auto* first_gain_ptr = first_gain.get();
    group->add_child(std::move(first_gain));

    auto second_gain = std::make_unique<Probe>();
    second_gain->set_access_role(View::AccessRole::slider);
    second_gain->set_access_label("Gain");
    auto* second_gain_ptr = second_gain.get();
    root.add_child(std::move(group));
    root.add_child(std::move(second_gain));

    auto nodes = snapshot_accessibility_tree(root);
    REQUIRE(nodes.size() == 3);
    REQUIRE(nodes[0].view == group_ptr);
    REQUIRE(nodes[0].depth == 0);
    REQUIRE(nodes[1].view == first_gain_ptr);
    REQUIRE(nodes[1].depth == 1);
    REQUIRE(nodes[2].view == second_gain_ptr);
    REQUIRE(nodes[2].depth == 0);

    REQUIRE(find_by_role_and_label(root, View::AccessRole::slider, "Gain")
            == first_gain_ptr);
}

TEST_CASE("snapshot captures nested range metadata", "[a11y][harness]") {
    Probe root;

    auto group = std::make_unique<Probe>();
    group->set_access_role(View::AccessRole::group);
    group->set_access_label("Channel Strip");

    auto gain = std::make_unique<RangeProbe>(6.0);
    gain->set_access_role(View::AccessRole::slider);
    gain->set_access_label("Gain");
    group->add_child(std::move(gain));

    root.add_child(std::move(group));

    auto nodes = snapshot_accessibility_tree(root);
    REQUIRE(nodes.size() == 2);

    REQUIRE(nodes[0].role == View::AccessRole::group);
    REQUIRE(nodes[0].label == "Channel Strip");
    REQUIRE(nodes[0].depth == 0);
    REQUIRE_FALSE(nodes[0].has_value);

    REQUIRE(nodes[1].role == View::AccessRole::slider);
    REQUIRE(nodes[1].label == "Gain");
    REQUIRE(nodes[1].depth == 1);
    REQUIRE(nodes[1].has_value);
    REQUIRE(nodes[1].min_value == -12.0);
    REQUIRE(nodes[1].max_value == 12.0);
    REQUIRE(nodes[1].current_value == 6.0);
    REQUIRE(nodes[1].value_string == "75%");
}

// ARIA state attributes (aria-pressed, aria-checked,
// aria-disabled, aria-hidden) must surface through the cross-platform
// AccessibilityNodeSnapshot so AT bridges (NSAccessibility today;
// AT-SPI / UIA when wired) and offline tests can read them.
// The View state slots must be carried into the snapshot, otherwise assistive
// tech sees only role/label/value and misses pressed/checked/disabled/hidden.
TEST_CASE("Accessibility snapshot surfaces ARIA state attributes",
          "[a11y]") {
    using namespace vellum::view;
    Probe root;

    auto btn = std::make_unique<Probe>();
    btn->set_access_role(View::AccessRole::toggle);
    btn->set_access_label("Mute");
    btn->set_access_pressed("true");

    auto chk = std::make_unique<Probe>();
    chk->set_access_role(View::AccessRole::toggle);
    chk->set_access_label("Tri-state filter");
    chk->set_access_checked("mixed");
    chk->set_access_disabled("false");

    auto hidden = std::make_unique<Probe>();
    hidden->set_access_role(View::AccessRole::label);
    hidden->set_access_label("Decorative icon");
    hidden->set_access_hidden("true");

    root.add_child(std::move(btn));
    root.add_child(std::move(chk));
    root.add_child(std::move(hidden));

    auto nodes = snapshot_accessibility_tree(root);
    REQUIRE(nodes.size() == 3);

    REQUIRE(nodes[0].label == "Mute");
    REQUIRE(nodes[0].pressed  == "true");
    REQUIRE(nodes[0].checked.empty());
    REQUIRE(nodes[0].disabled.empty());
    REQUIRE(nodes[0].hidden.empty());

    REQUIRE(nodes[1].label == "Tri-state filter");
    REQUIRE(nodes[1].checked  == "mixed");
    REQUIRE(nodes[1].disabled == "false");
    REQUIRE(nodes[1].pressed.empty());

    REQUIRE(nodes[2].label == "Decorative icon");
    REQUIRE(nodes[2].hidden == "true");
}

TEST_CASE("Accessibility value interface default strings clamp and fall back",
          "[a11y]") {
    Probe root;

    auto high = std::make_unique<RangeProbe>(30.0);
    high->set_access_role(View::AccessRole::slider);
    high->set_access_label("High");
    root.add_child(std::move(high));

    auto low = std::make_unique<RangeProbe>(-30.0);
    low->set_access_role(View::AccessRole::slider);
    low->set_access_label("Low");
    root.add_child(std::move(low));

    auto flat = std::make_unique<DegenerateRangeProbe>(7.25);
    flat->set_access_role(View::AccessRole::meter);
    flat->set_access_label("Flat");
    root.add_child(std::move(flat));

    auto nodes = snapshot_accessibility_tree(root);
    REQUIRE(nodes.size() == 3);
    REQUIRE(nodes[0].has_value);
    REQUIRE(nodes[0].value_string == "100%");
    REQUIRE(nodes[1].value_string == "0%");
    REQUIRE(nodes[2].value_string == "7.25");
}

TEST_CASE("Accessibility helper interfaces expose default selection behavior",
          "[a11y]") {
    TextProbe text("abcdef");
    REQUIRE(text.get_character_count() == 6);
    REQUIRE(text.get_selection() == std::pair<int, int>{0, 0});
    REQUIRE_FALSE(text.is_editable());
    REQUIRE(text.get_text_range(-5, 3) == "abc");
    REQUIRE(text.get_text_range(2, 99) == "cdef");
    REQUIRE(text.get_text_range(4, 2).empty());
    text.set_selection(1, 4);
    REQUIRE(text.get_selection() == std::pair<int, int>{0, 0});
    text.set_text("xy");
    REQUIRE(text.get_character_count() == 2);

    TableProbe table;
    REQUIRE(table.get_selected_row() == -1);
    REQUIRE(table.get_selected_rows().empty());
    REQUIRE_FALSE(table.supports_multi_selection());
    table.select_row(1);
    REQUIRE(table.get_selected_row() == -1);

    CellProbe cell;
    REQUIRE(cell.get_cell_text(2, 1) == "2:1");
    REQUIRE_FALSE(cell.is_cell_editable(0, 0));
    REQUIRE(cell.get_cell_span(0, 0) == std::pair<int, int>{1, 1});
}

// ── Linux AT-SPI per-widget tree loopback ───────────────────────────────────
//
// The Linux provider (accessibility_linux.cpp) exports one D-Bus object per
// accessible View implementing org.a11y.atspi.Accessible + Component. This is
// the CI-verifiable proof: build a known View tree, export it on the SESSION
// bus via the test seam, then ACT AS AN AT-SPI CLIENT - call Accessible.
// GetChildren on the root, recurse, and assert the (role, role-name, child-
// count) tuples match snapshot_accessibility_tree's accessible (non-none)
// nodes re-parented onto their nearest accessible ancestor. Then call
// Component.GetExtents on a widget and assert it matches the View's root-
// relative bounds. Runs only under a real session bus (`dbus-run-session --
// ctest`); SUCCEED-skips otherwise so it is green on every Linux CI host and a
// no-op stub off Linux.


// The Linux AT-SPI provider runs over a D-Bus session and is covered by
// linux_atspi_tree_test.cpp.



// ── Widget default roles ────────────────────────────────────────────────────
//
// The role a widget claims IS its screen-reader identity: every platform bridge
// (NSAccessibility, UIAccessibility, UIA, AT-SPI, TalkBack) keys off it. Before
// the AccessRole vocabulary grew past seven values, widgets were forced into
// roles they are not: a TextButton announced as a checkbox and a ComboBox
// announced as a slider. These cases assert the widget defaults through the
// same headless tree snapshot the platform bridges walk.

TEST_CASE("widget default accessibility roles are announceable and correct",
          "[view][accessibility][roles]") {
    Probe root;

    auto add = [&root](auto widget) {
        auto* raw = widget.get();
        root.add_child(std::move(widget));
        return raw;
    };

    auto* button = add(std::make_unique<TextButton>("Play"));
    auto* combo  = add(std::make_unique<ComboBox>());
    combo->set_items({"Sine", "Saw"});  // an empty dropdown announces nothing
    auto* editor = add(std::make_unique<TextEditor>());
    auto* check  = add(std::make_unique<Checkbox>());
    auto* sw     = add(std::make_unique<Toggle>());
    auto* link   = add(std::make_unique<HyperlinkButton>(
                       "Docs", "https://example.invalid"));
    auto* bar    = add(std::make_unique<ProgressBar>());
    auto* scroll = add(std::make_unique<ScrollBar>());
    auto* knob   = add(std::make_unique<Knob>());

    // A push button is a button, not a checkbox.
    REQUIRE(button->access_role() == View::AccessRole::button);
    REQUIRE(button->access_role() != View::AccessRole::toggle);
    // A dropdown is a combo box, not a slider.
    REQUIRE(combo->access_role() == View::AccessRole::combo_box);
    REQUIRE(combo->access_role() != View::AccessRole::slider);
    // Text entry was invisible to assistive tech entirely (role none).
    REQUIRE(editor->access_role() == View::AccessRole::text_field);
    // A checkbox is distinct from a switch.
    REQUIRE(check->access_role() == View::AccessRole::checkbox);
    REQUIRE(sw->access_role()    == View::AccessRole::toggle);
    REQUIRE(link->access_role()  == View::AccessRole::link);
    REQUIRE(bar->access_role()   == View::AccessRole::progress_bar);
    REQUIRE(scroll->access_role() == View::AccessRole::scroll_bar);
    // Unchanged: value-bearing widgets keep their historical roles.
    REQUIRE(knob->access_role()  == View::AccessRole::slider);

    // Every one of them reaches the platform accessibility tree.
    REQUIRE(count_announceable(root) == 9);
}

// ── A role must never arrive without a name ─────────────────────────────────
//
// The regression this pins: giving ArrowButton / ShapeButton / ImageButton
// AccessRole::button (correct!) without a name made every screen reader
// announce an unnamed "button" for every transport arrow and icon in every
// Vellum UI - a WCAG 4.1.2 (Name, Role, Value) failure that did NOT exist when
// they were AccessRole::none and excluded from the tree. is_accessibility_
// element() is the shared gate all six bridges call.

TEST_CASE("a name-only role is not exposed until it has a name",
          "[view][accessibility][roles]") {
    Probe root;

    auto arrow = std::make_unique<ArrowButton>(ArrowDirection::down);
    auto* arrow_raw = arrow.get();
    auto shape = std::make_unique<ShapeButton>();
    auto* shape_raw = shape.get();
    auto image = std::make_unique<ImageButton>();
    auto* image_raw = image.get();
    root.add_child(std::move(arrow));
    root.add_child(std::move(shape));
    root.add_child(std::move(image));

    // They claim the right role...
    REQUIRE(arrow_raw->access_role() == View::AccessRole::button);
    REQUIRE(shape_raw->access_role() == View::AccessRole::button);
    REQUIRE(image_raw->access_role() == View::AccessRole::button);

    // ...but a nameless button is worse than no button: kept out of the tree.
    REQUIRE_FALSE(is_accessibility_element(*arrow_raw));
    REQUIRE_FALSE(is_accessibility_element(*shape_raw));
    REQUIRE_FALSE(is_accessibility_element(*image_raw));
    REQUIRE(count_announceable(root) == 0);

    // Name them and they appear, correctly roled, on every platform.
    arrow_raw->set_access_label("Next preset");
    shape_raw->set_access_label("Record");
    image_raw->set_access_label("Bypass");
    REQUIRE(is_accessibility_element(*arrow_raw));
    REQUIRE(count_announceable(root) == 3);
    REQUIRE(find_by_role_and_label(root, View::AccessRole::button,
                                   "Next preset") == arrow_raw);
}

TEST_CASE("no exposed node announces a role with nothing to say",
          "[view][accessibility][roles]") {
    // The invariant, asserted over the widget gallery: everything the bridges
    // put in the tree either has a NAME, or carries CONTENT (a value / text
    // source, an app-set value string, or an ARIA state). A node with neither
    // announces its role and then falls silent.
    Probe root;
    auto add = [&root](auto widget) {
        auto* raw = widget.get();
        root.add_child(std::move(widget));
        return raw;
    };
    add(std::make_unique<TextButton>("Play"));
    add(std::make_unique<HyperlinkButton>("Docs", "https://example.invalid"));
    add(std::make_unique<ArrowButton>(ArrowDirection::up));   // unnamed
    add(std::make_unique<ShapeButton>());                     // unnamed
    add(std::make_unique<ImageButton>());                     // unnamed
    add(std::make_unique<ComboBox>());  // no items: nothing to announce
    add(std::make_unique<TextEditor>());
    add(std::make_unique<Checkbox>());
    add(std::make_unique<Toggle>());
    add(std::make_unique<ToggleButton>());
    add(std::make_unique<ProgressBar>());
    add(std::make_unique<ScrollBar>());
    add(std::make_unique<Knob>());
    add(std::make_unique<Fader>());
    add(std::make_unique<Label>("Output"));
    add(std::make_unique<ListBox>());
    add(std::make_unique<TableListBox>());
    add(std::make_unique<TabPanel>());

    std::size_t exposed = 0;
    for (const auto& node : snapshot_accessibility_tree(root)) {
        if (!node.exposed) continue;
        ++exposed;
        INFO("role ordinal " << static_cast<int>(node.role));
        const bool has_name = !node.label.empty();
        // has_accessibility_value covers all three sources the bridges read:
        // a value interface, a text interface (TextEditor), and access_value.
        const bool has_content = has_accessibility_value(*node.view) ||
                                 !node.checked.empty() || !node.pressed.empty();
        REQUIRE((has_name || has_content));
    }
    REQUIRE(exposed > 0);
}

TEST_CASE("painted-row containers do not claim a container role",
          "[view][accessibility][roles]") {
    // Deliberate: ListBox / TableListBox / TabPanel paint their rows and tabs —
    // they hold no child Views for them. A `list` / `table` / `tab_list` role
    // would export an empty AXList / AXTable / AXTabGroup ("list, 0 items"), and
    // a tab group whose children are content panels is worse than none at all.
    // The role lands with the per-row / per-tab elements. VirtualList, which DOES
    // hold child Views, exposes list/list_item today (test_virtual_list).
    ListBox list;
    TableListBox table;
    TabPanel tabs;
    REQUIRE(list.access_role()  == View::AccessRole::none);
    REQUIRE(table.access_role() == View::AccessRole::none);
    REQUIRE(tabs.access_role()  == View::AccessRole::none);
    REQUIRE_FALSE(is_accessibility_element(list));
    REQUIRE_FALSE(is_accessibility_element(table));
    REQUIRE_FALSE(is_accessibility_element(tabs));
}

// ── Roles carry a value the AT can actually read ────────────────────────────

TEST_CASE("TextEditor exposes its content through the text interface",
          "[view][accessibility][roles]") {
    // `text_field` + no text source = macOS -accessibilityValue returns nil and
    // Windows IValueProvider::get_Value returns a NULL BSTR: VoiceOver and
    // Narrator announce "text field" and read NOTHING. Every bridge resolves the
    // value through accessibility_value_string().
    TextEditor editor;
    editor.set_text("hello");

    auto* tif = dynamic_cast<AccessibilityTextInterface*>(&editor);
    REQUIRE(tif != nullptr);
    REQUIRE(tif->get_text() == "hello");
    REQUIRE(tif->is_editable());
    REQUIRE(tif->get_character_count() == 5);
    REQUIRE(accessibility_value_string(editor) == "hello");
    REQUIRE(has_accessibility_value(editor));

    // The Windows provider routes IValueProvider::SetValue back through this.
    tif->set_text(std::string_view{"world"});
    REQUIRE(editor.text() == "world");

    // read_only editors report themselves as such, so UIA IsReadOnly and
    // SetValue agree.
    editor.read_only = true;
    REQUIRE_FALSE(tif->is_editable());
}

TEST_CASE("ProgressBar exposes a real range and not a degenerate 0..0",
          "[view][accessibility][roles]") {
    // `progress_bar` advertises RangeValue. With no value interface behind it,
    // UIA get_Value / get_Minimum / get_Maximum all returned 0.0 - Narrator
    // announced 0 at any progress, and min == max == 0 divides by zero in any
    // client computing (v - min) / (max - min).
    ProgressBar bar;
    bar.set_progress(0.42f);

    auto* vif = dynamic_cast<AccessibilityValueInterface*>(&bar);
    REQUIRE(vif != nullptr);
    REQUIRE(vif->get_minimum_value() == 0.0);
    REQUIRE(vif->get_maximum_value() == 1.0);
    REQUIRE(vif->get_maximum_value() > vif->get_minimum_value());  // not degenerate
    REQUIRE(vif->get_current_value() == Catch::Approx(0.42));
    REQUIRE(vif->get_value_string() == "42%");

    bar.set_progress(-1.0f);  // indeterminate
    REQUIRE(bar.is_indeterminate());
    REQUIRE(vif->get_current_value() == 0.0);
    REQUIRE(vif->get_value_string() == "indeterminate");

    // The label doubles as the accessible name.
    bar.set_label("Analyzing");
    REQUIRE(bar.access_label() == "Analyzing");
}

TEST_CASE("value-bearing widgets all carry a value source",
          "[view][accessibility][roles]") {
    // Every widget whose ROLE advertises a range (slider / scroll_bar / meter /
    // progress_bar) must implement AccessibilityValueInterface, or the bridges
    // report 0 in a 0..0 range and VoiceOver reads an empty value.
    Knob knob;      knob.set_value(0.25f);
    Fader fader;    fader.set_value(0.5f);
    RangeSlider rs; rs.set_min(20.0f); rs.set_max(20000.0f); rs.set_value(1000.0f);
    ScrollBar sb;   sb.set_range(0.0f, 200.0f); sb.set_value(50.0f);

    REQUIRE(accessibility_value_string(knob) == "25%");
    REQUIRE(accessibility_value_string(fader) == "50%");
    REQUIRE(has_accessibility_value(rs));
    REQUIRE(dynamic_cast<AccessibilityValueInterface*>(&rs)->get_maximum_value()
            == Catch::Approx(20000.0));
    REQUIRE(dynamic_cast<AccessibilityValueInterface*>(&sb)->get_current_value()
            == Catch::Approx(50.0));

    // A knob's own display formatter wins over the generic percentage - it is
    // the string the sighted user reads.
    knob.set_format([](float v) { return std::to_string(static_cast<int>(v * 24)) + " dB"; });
    REQUIRE(accessibility_value_string(knob) == "6 dB");
}

TEST_CASE("toggles publish their state and not just their role",
          "[view][accessibility][roles]") {
    // A `checkbox` / `toggle` with an unset ARIA state announces the control and
    // then says nothing about whether it is on: NSAccessibility's
    // accessibilityValue, UIA's ToggleState and AT-SPI's CHECKED state all read
    // access_checked.
    Checkbox check;
    REQUIRE(check.access_checked() == "false");
    check.set_checked(true);
    REQUIRE(check.access_checked() == "true");

    Toggle sw;
    REQUIRE(sw.access_checked() == "false");
    sw.set_on(true, /*animate=*/false);
    REQUIRE(sw.access_checked() == "true");

    ToggleButton tb;
    REQUIRE(tb.access_checked() == "false");
    tb.set_on(true);
    REQUIRE(tb.access_checked() == "true");
    tb.set_label("Bypass");
    REQUIRE(tb.access_label() == "Bypass");
}

TEST_CASE("HyperlinkButton carries its text as the accessibility label",
          "[view][accessibility][roles]") {
    HyperlinkButton link{"Read the docs", "https://example.invalid"};
    REQUIRE(link.access_label() == "Read the docs");
    link.set_text("Changelog");
    REQUIRE(link.access_label() == "Changelog");
}

TEST_CASE("Android role ordinals are wire format",
          "[view][accessibility][android]") {
    // core/view/platform/android/accessibility_android.cpp marshals the role to
    // Kotlin as static_cast<int>(role); VellumAccessibilityDelegate.kt matches on
    // those integers. Reordering or removing an AccessRole silently
    // re-labels every widget on TalkBack, so the ordinals are frozen here and
    // mirrored by the ROLE_* constants in VellumAccessibility.kt.
    using R = View::AccessRole;
    static_assert(static_cast<int>(R::none)   == 0);
    static_assert(static_cast<int>(R::slider) == 1);
    static_assert(static_cast<int>(R::toggle) == 2);
    static_assert(static_cast<int>(R::label)  == 3);
    static_assert(static_cast<int>(R::group)  == 4);
    static_assert(static_cast<int>(R::meter)  == 5);
    static_assert(static_cast<int>(R::image)  == 6);
    static_assert(static_cast<int>(R::button)       == 7);
    static_assert(static_cast<int>(R::link)         == 8);
    static_assert(static_cast<int>(R::checkbox)     == 9);
    static_assert(static_cast<int>(R::radio)        == 10);
    static_assert(static_cast<int>(R::text_field)   == 11);
    static_assert(static_cast<int>(R::combo_box)    == 12);
    static_assert(static_cast<int>(R::list)         == 13);
    static_assert(static_cast<int>(R::list_item)    == 14);
    static_assert(static_cast<int>(R::table)        == 15);
    static_assert(static_cast<int>(R::row)          == 16);
    static_assert(static_cast<int>(R::cell)         == 17);
    static_assert(static_cast<int>(R::tab)          == 18);
    static_assert(static_cast<int>(R::tab_list)     == 19);
    static_assert(static_cast<int>(R::menu)         == 20);
    static_assert(static_cast<int>(R::menu_item)    == 21);
    static_assert(static_cast<int>(R::progress_bar) == 22);
    static_assert(static_cast<int>(R::dialog)       == 23);
    static_assert(static_cast<int>(R::heading)      == 24);
    static_assert(static_cast<int>(R::scroll_bar)   == 25);
    SUCCEED("AccessRole ordinals are stable for the Android JNI bridge");
}

// ── An explicit accessible name wins over content ────────────────────────────

TEST_CASE("an author-set accessible name is not clobbered by content",
          "[view][accessibility][name]") {
    // ARIA 1.2 accname step 2B: aria-label is consulted BEFORE the element's
    // contents - an explicit name WINS. The JS bridge's setAccessibilityLabel
    // (aria-label replay) lands on set_access_label(); a widget's visible text
    // lands on set_derived_access_label(). If the widgets wrote the content into
    // the same slot:
    //     el.setAttribute('aria-label', 'Gain in decibels');
    //     el.textContent = 'dB';               // -> Label::set_text
    // VoiceOver would announce "dB" and the author's name would be gone.
    Label label{"dB"};
    REQUIRE(label.access_label() == "dB");            // content-derived

    label.set_access_label("Gain in decibels");       // aria-label
    REQUIRE(label.access_label() == "Gain in decibels");
    REQUIRE(label.has_explicit_access_label());

    // The clobber: content written AFTER the aria-label.
    label.set_text("dBFS");
    REQUIRE(label.access_label() == "Gain in decibels");
    REQUIRE(label.derived_access_label() == "dBFS");   // still tracked, just loses

    // react-reconciler's commitTextUpdate rewrites textContent on EVERY
    // re-render, so a safe initial order gets clobbered on the next render.
    for (int render = 0; render < 3; ++render) label.set_text("dB");
    REQUIRE(label.access_label() == "Gain in decibels");

    // Removing the aria-label hands the name back to the content.
    label.set_access_label("");
    REQUIRE_FALSE(label.has_explicit_access_label());
    REQUIRE(label.access_label() == "dB");
}

TEST_CASE("every content-named widget respects an author-set name",
          "[view][accessibility][name]") {
    // The full set of widgets that derive a name from their visible text.
    // Each: aria-label first, then content - the content must not win.
    Knob knob;
    knob.set_access_label("Filter cutoff");
    knob.set_label("Hz");
    REQUIRE(knob.access_label() == "Filter cutoff");

    Fader fader;
    fader.set_access_label("Channel 1 volume");
    fader.set_label("Vol");
    REQUIRE(fader.access_label() == "Channel 1 volume");

    Toggle sw;
    sw.set_access_label("Bypass the effect");
    sw.set_label("Byp");
    REQUIRE(sw.access_label() == "Bypass the effect");

    ToggleButton tb;
    tb.set_access_label("Solo this track");
    tb.set_label("S");
    REQUIRE(tb.access_label() == "Solo this track");

    ProgressBar bar;
    bar.set_access_label("Analysis progress");
    bar.set_label("42%");
    REQUIRE(bar.access_label() == "Analysis progress");

    TextButton button{"OK"};
    button.set_access_label("Confirm and close");
    button.set_label("OK!");
    REQUIRE(button.access_label() == "Confirm and close");

    HyperlinkButton link{"here", "https://example.invalid"};
    link.set_access_label("Read the Vellum documentation");
    link.set_text("here");
    REQUIRE(link.access_label() == "Read the Vellum documentation");

    // And the reverse order (content first, then aria-label) still ends on the
    // author's name - this is the order the JS bridge actually replays in.
    Label l;
    l.set_text("dB");
    l.set_access_label("Gain in decibels");
    REQUIRE(l.access_label() == "Gain in decibels");
}

// ── The snapshot announces what the bridges announce ─────────────────────────

TEST_CASE("the cross-platform snapshot resolves the value like every bridge",
          "[view][accessibility][value]") {
    // accessibility_tree.cpp used to read the raw access_value SLOT, so the
    // snapshot - the one "bridge" that runs on every platform, and the surface
    // the offline tests read - reported "" for a Knob that macOS / Windows /
    // Linux all announce as "25%", and "" for a TextEditor holding "hello".
    Probe root;
    auto add = [&root](auto widget) {
        auto* raw = widget.get();
        root.add_child(std::move(widget));
        return raw;
    };
    auto* knob = add(std::make_unique<Knob>());
    knob->set_value(0.25f);
    knob->set_access_label("Gain");

    auto* editor = add(std::make_unique<TextEditor>());
    editor->set_text("hello");
    editor->set_access_label("Preset name");

    auto* combo = add(std::make_unique<ComboBox>());
    combo->set_items({"Sine", "Saw"});
    combo->set_selected(1);
    combo->set_access_label("Waveform");

    auto* check = add(std::make_unique<Checkbox>());
    check->set_access_label("Bypass");
    check->set_checked(true);

    const auto nodes = snapshot_accessibility_tree(root);
    auto value_of = [&nodes](const View* v) -> std::string {
        for (const auto& n : nodes)
            if (n.view == v) return n.value;
        return "<missing>";
    };
    REQUIRE(value_of(knob)   == "25%");     // value interface
    REQUIRE(value_of(editor) == "hello");   // text interface
    REQUIRE(value_of(combo)  == "Saw");     // access_value slot
    REQUIRE(value_of(check)  == "checked"); // check state
}

// ── Check state reaches the platforms that have no state channel ─────────────

TEST_CASE("check state resolves as a value for the bridges with no state slot",
          "[view][accessibility][value]") {
    // macOS turns access_checked into a native @YES/@NO accessibilityValue, and
    // AT-SPI carries CHECKED/PRESSED in the state bitfield. iOS (UIKit collapses
    // checkbox onto TraitButton) and Android read the VALUE string and nothing
    // else - so without the state in the resolver they announced "button" and
    // fell silent. Windows announces no state at all: the UIA Toggle pattern
    // needs an IToggleProvider that VellumFragmentProvider does not implement.
    Checkbox check;
    REQUIRE(accessibility_value_string(check) == "unchecked");
    REQUIRE(has_accessibility_value(check));
    check.set_checked(true);
    REQUIRE(accessibility_value_string(check) == "checked");
    REQUIRE(accessibility_toggle_state(check) == AccessToggleState::on);

    Toggle sw;
    sw.set_on(true, /*animate=*/false);
    REQUIRE(accessibility_value_string(sw) == "checked");

    // Tri-state (aria-checked="mixed" from the JS bridge).
    Probe tri;
    tri.set_access_role(View::AccessRole::checkbox);
    tri.set_access_checked("mixed");
    REQUIRE(accessibility_toggle_state(tri) == AccessToggleState::mixed);
    REQUIRE(accessibility_value_string(tri) == "mixed");

    // aria-pressed (a toggle BUTTON) announces pressed / not pressed.
    Probe pressed;
    pressed.set_access_role(View::AccessRole::button);
    pressed.set_access_pressed("true");
    REQUIRE(accessibility_value_string(pressed) == "pressed");
    pressed.set_access_pressed("false");
    REQUIRE(accessibility_value_string(pressed) == "not pressed");

    // A real value source still wins: a state string never displaces it.
    Knob knob;
    knob.set_value(0.5f);
    knob.set_access_checked("true");   // nonsense, but must not shadow "50%"
    REQUIRE(accessibility_value_string(knob) == "50%");
}

// ── The interface set each bridge exports ────────────────────────────────────

TEST_CASE("a string-valued role exports a text interface, not nothing",
          "[view][accessibility][atspi]") {
    // The Linux TU (accessibility_linux.cpp) never compiles on the macOS gate,
    // so its export decision is made by THIS pure predicate. Before it was
    // wired, Linux exported org.a11y.atspi.Value for value-interface Views and
    // NOTHING for the string-valued ones: a TextEditor holding "hello" was
    // exported as ROLE_ENTRY with no Text and no Value interface, so Orca
    // announced "entry" plus the name and read nothing. Same for a ComboBox
    // whose selected item lives in access_value.
    TextEditor editor;
    editor.set_text("hello");
    auto ifs = accessibility_interfaces(editor);
    REQUIRE_FALSE(ifs.value);
    REQUIRE(ifs.text);
    REQUIRE(accessibility_text_content(editor) == "hello");
    REQUIRE(accessibility_text_editable(editor));

    // An EMPTY editable field still exports Text: it has a caret, a character
    // count of 0 and an EDITABLE state.
    TextEditor empty;
    REQUIRE(accessibility_interfaces(empty).text);
    REQUIRE(accessibility_text_content(empty).empty());

    TextEditor ro;
    ro.set_text("frozen");
    ro.read_only = true;
    REQUIRE(accessibility_interfaces(ro).text);
    REQUIRE_FALSE(accessibility_text_editable(ro));

    ComboBox combo;
    combo.set_items({"Sine", "Saw"});
    combo.set_selected(1);
    ifs = accessibility_interfaces(combo);
    REQUIRE_FALSE(ifs.value);
    REQUIRE(ifs.text);                                   // AT-SPI reads it as Text
    REQUIRE(accessibility_text_content(combo) == "Saw");
    REQUIRE_FALSE(accessibility_text_editable(combo));

    // A numeric range is Value, NOT Text - a Knob is not a text field.
    Knob knob;
    ifs = accessibility_interfaces(knob);
    REQUIRE(ifs.value);
    REQUIRE_FALSE(ifs.text);

    // A plain label has neither: its NAME is the announcement.
    Label label{"Output"};
    ifs = accessibility_interfaces(label);
    REQUIRE_FALSE(ifs.value);
    REQUIRE_FALSE(ifs.text);
}

int main() { return framework_test::run_all(); }
