// vellum::design implementation — Ink & Signal theme helpers + component catalog.
// See vellum/design/design_system.hpp for the contract.

#include <vellum/design/design_system.hpp>

#include <vellum/view/theme_presets.hpp>

#include <algorithm>

namespace vellum::design {

using vellum::view::Theme;

Theme ink_signal_theme(bool dark) {
    const auto* preset = vellum::view::find_preset("ink-signal");
    // The preset is built-in (theme_presets.cpp); fall back to a derived default
    // only in the impossible case it was stripped, so callers never crash.
    if (preset == nullptr) {
        const auto& all = vellum::view::all_presets();
        if (all.empty()) return Theme{};
        return vellum::view::theme_from_preset(all.front(), dark);
    }
    return vellum::view::theme_from_preset(*preset, dark);
}

void apply_ink_signal(vellum::view::View& root, bool dark) {
    root.set_theme(ink_signal_theme(dark));
}

std::string_view category_name(Category c) {
    switch (c) {
        case Category::controls:   return "Controls";
        case Category::inputs:     return "Inputs";
        case Category::indicators: return "Indicators";
        case Category::navigation: return "Navigation";
        case Category::containers: return "Containers";
        case Category::overlays:   return "Overlays";
        case Category::audio:      return "Audio";
        case Category::feedback:   return "Feedback";
    }
    return "Unknown";
}

namespace {

std::vector<ComponentInfo> build_catalog() {
    using C = Category;
    return {
        // ── Controls ───────────────────────────────────────────────────────
        {"Knob", C::controls, "vellum::view::Knob", "vellum/view/widgets.hpp", "Knob",
         "Rotary control for a continuous parameter",
         {"knob.arc", "knob.arc.bg", "knob.thumb", "focus.ring"}},
        {"Fader", C::controls, "vellum::view::Fader", "vellum/view/widgets.hpp", "Fader",
         "Vertical level control",
         {"slider.track", "slider.fill", "slider.thumb"}},
        {"Slider", C::controls, "vellum::view::RangeSlider", "vellum/view/widgets.hpp", "Slider",
         "Linear / range value control",
         {"slider.track", "slider.fill", "slider.thumb", "focus.ring"}},
        {"Stepper", C::controls, "vellum::view::Stepper", "vellum/view/gap_widgets.hpp", "Stepper",
         "[-] value [+] numeric nudge",
         {"bg.surface", "control.border", "text.primary", "accent.primary"}},
        {"Pan", C::controls, "vellum::view::PanControl", "vellum/view/gap_widgets.hpp", "Pan",
         "Bipolar L/R pan with center detent",
         {"slider.track", "slider.fill", "slider.thumb"}},

        // ── Inputs ─────────────────────────────────────────────────────────
        {"Button", C::inputs, "vellum::view::TextButton", "vellum/view/buttons.hpp", "Button",
         "Primary / secondary / ghost text button",
         {"accent.primary", "text.primary", "bg.surface", "control.border"}},
        {"Toggle", C::inputs, "vellum::view::Toggle", "vellum/view/widgets.hpp", "Toggle",
         "On/off switch",
         {"control.track", "accent.primary", "control.thumb"}},
        {"Checkbox", C::inputs, "vellum::view::Checkbox", "vellum/view/widgets.hpp", "Checkbox",
         "Tri-state checkbox",
         {"accent.primary", "control.border", "text.primary"}},
        {"Input", C::inputs, "vellum::view::TextEditor", "vellum/view/text_editor.hpp", "Input",
         "Single / multi-line text field",
         {"bg.surface", "control.border", "text.primary", "focus.ring", "accent.error"}},
        {"ComboBox", C::inputs, "vellum::view::ComboBox", "vellum/view/ui_components.hpp", "ComboBox",
         "Dropdown selector",
         {"bg.surface", "control.border", "text.primary", "accent.primary"}},

        // ── Indicators ───────────────────────────────────────────────────────
        {"Meter", C::indicators, "vellum::view::Meter", "vellum/view/widgets.hpp", "Meter",
         "Level meter with green/yellow/red zones",
         {"meter.green", "meter.yellow", "meter.red", "control.track"}},
        {"ProgressBar", C::indicators, "vellum::view::ProgressBar", "vellum/view/ui_components.hpp", "ProgressBar",
         "Determinate / indeterminate progress",
         {"progress.track", "progress.fill"}},
        {"Badge", C::indicators, "vellum::view::Badge", "vellum/view/gap_widgets.hpp", "Badge",
         "Compact status / count pill",
         {"bg.surface", "text.secondary", "accent.primary", "accent.error"}},

        // ── Navigation ───────────────────────────────────────────────────────
        {"Tab", C::navigation, "vellum::view::TabPanel", "vellum/view/ui_components.hpp", "Tab",
         "Tab bar with active underline",
         {"tab.active", "tab.inactive", "accent.primary"}},
        {"Toolbar", C::navigation, "vellum::view::Toolbar", "vellum/view/toolbar.hpp", "Toolbar",
         "Action button strip",
         {"bg.secondary", "text.primary", "divider"}},
        {"Breadcrumb", C::navigation, "vellum::view::Breadcrumb", "vellum/view/breadcrumb.hpp", "Breadcrumb",
         "Path navigation trail",
         {"text.secondary", "text.link", "divider"}},
        {"Sidebar", C::navigation, "vellum::view::SidePanel", "vellum/view/side_panel.hpp", "Sidebar",
         "Collapsible side panel",
         {"bg.secondary", "divider", "text.primary"}},
        {"Tree", C::navigation, "vellum::view::TreeView", "vellum/view/tree_view.hpp", "Tree",
         "Hierarchical disclosure list",
         {"text.primary", "text.secondary", "accent.primary"}},

        // ── Containers ───────────────────────────────────────────────────────
        {"Panel", C::containers, "vellum::view::Panel", "vellum/view/widgets.hpp", "Panel",
         "Titled grouping container",
         {"bg.elevated", "modal.border", "text.primary"}},
        {"ChannelStrip", C::containers, "vellum::view::ChannelStrip", "vellum/view/gap_widgets.hpp", "ChannelStrip",
         "Mixer strip: meter + fader + pan",
         {"bg.elevated", "meter.green", "slider.fill", "text.secondary"}},
        {"ScrollBar", C::containers, "vellum::view::ScrollBar", "vellum/view/scroll_bar.hpp", "ScrollBar",
         "Scroll indicator / drag handle",
         {"control.track", "control.thumb"}},
        {"Table", C::containers, "vellum::view::TableListBox", "vellum/view/table.hpp", "Table",
         "Row / column data grid",
         {"bg.surface", "divider", "text.primary"}},

        // ── Overlays ───────────────────────────────────────────────────────
        {"Popover", C::overlays, "vellum::view::Popover", "vellum/view/gap_widgets.hpp", "Popover",
         "Floating panel with a tail",
         {"modal.bg", "modal.border", "text.primary"}},
        {"Dialog", C::overlays, "vellum::view::InCanvasDialog", "vellum/view/gap_widgets.hpp", "Dialog",
         "In-canvas modal alert",
         {"overlay.bg", "modal.bg", "accent.primary", "accent.error"}},
        {"Tooltip", C::overlays, "vellum::view::Tooltip", "vellum/view/ui_components.hpp", "Tooltip",
         "Hover hint bubble",
         {"tooltip.bg", "tooltip.text"}},
        {"Toast", C::overlays, "vellum::view::Toast", "vellum/view/gap_widgets.hpp", "Toast",
         "Transient notification card",
         {"bg.elevated", "accent.primary", "text.primary"}},
        {"Callout", C::overlays, "vellum::view::CallOutBox", "vellum/view/ui_components.hpp", "Callout",
         "Pointered info box",
         {"modal.bg", "modal.border", "accent.primary"}},
        {"PopupMenu", C::overlays, "vellum::view::ContextMenu", "vellum/view/context_menu.hpp", "PopupMenu",
         "Right-click / dropdown menu",
         {"modal.bg", "text.primary", "accent.primary"}},

        // ── Audio ─────────────────────────────────────────────────────────
        {"WaveformEditor", C::audio, "vellum::view::WaveformView", "vellum/view/widgets.hpp", "WaveformEditor",
         "Sample waveform display / edit",
         {"waveform.line", "waveform.fill", "waveform.grid"}},
        {"Spectrum", C::audio, "vellum::view::SpectrumView", "vellum/view/widgets.hpp", "Spectrum",
         "Frequency spectrum analyser",
         {"waveform.line", "waveform.grid", "accent.primary"}},
        {"XYPad", C::audio, "vellum::view::XYPad", "vellum/view/widgets.hpp", "XYPad",
         "2-D parameter control surface",
         {"control.track", "accent.primary", "control.thumb"}},
        {"MIDIKeyboard", C::audio, "vellum::view::MidiKeyboard", "vellum/view/midi_keyboard.hpp", "MIDIKeyboard",
         "Plain MIDI piano input strip (note on/off only). For a playable "
         "computer-typing + piano keyboard with QWERTY play, octave and velocity, "
         "use MusicalTyping instead — NOT this.",
         {"bg.surface", "text.primary", "accent.primary"}},
        {"MusicalTyping", C::audio, "vellum::view::MusicalTypingKeyboard",
         "vellum/view/musical_typing_keyboard.hpp", "Musical Typing Keyboard",
         "THE playable musical-typing keyboard: computer-key letters "
         "(a w s e d f t g y h u j k o l p) play notes, z/x shift octave, plus a "
         "click-to-play piano row; pressed keys light with the accent gradient. "
         "Wire on_note_on/on_note_off. Use this for a \"musical typing keyboard\", "
         "NOT MIDIKeyboard (the plain strip).",
         {"surface.panel", "accent.primary", "accent.text", "key.white", "key.black", "text.secondary"}},
        {"Channel Strip", C::containers, "vellum::view::ChannelStripView",
         "vellum/view/channel_strip_view.hpp", "Channel Strip",
         "Pro channel strip: inserts, sends, routing, pan, dB-tick fader, VU "
         "(faithful Figma render; the lean interactive widget is \"ChannelStrip\")",
         {"surface.panel", "accent.primary", "text.primary"}},
        // Faithful Figma-vector specimens (DesignFrameView), generated by
        // tools/import-design/make_catalog_component.py. Each is the design's
        // composite/states specimen, complementing the lean interactive widgets.
        {"Range Slider", C::controls, "vellum::view::RangeSliderView",
         "vellum/view/range_slider_view.hpp", "Range Slider",
         "Dual-handle range slider — states, vertical, full/collapsed/disabled",
         {"surface.panel", "accent.primary", "text.primary"}},
        {"Inline Value Editor", C::controls, "vellum::view::InlineValueEditorView",
         "vellum/view/inline_value_editor_view.hpp", "Inline Value Editor",
         "Click-to-type readout — under-knob / beside-fader / editing / out-of-range",
         {"surface.panel", "accent.primary", "text.primary"}},
        {"Property Panel", C::containers, "vellum::view::PropertyPanelView",
         "vellum/view/property_panel_view.hpp", "Property Panel",
         "Labeled property rows — slider/choice/toggle/text/button + row states",
         {"surface.panel", "accent.primary", "text.primary"}},
        {"Group Box", C::containers, "vellum::view::GroupBoxView",
         "vellum/view/group_box_view.hpp", "Group Box",
         "Titled container — default / empty / collapsible",
         {"surface.panel", "accent.primary", "text.primary"}},
        {"Number Box", C::inputs, "vellum::view::NumberBoxStatesView",
         "vellum/view/number_box_states_view.hpp", "Number Box",
         "NumberBox states — scrub, chevrons, inline edit, stepping",
         {"surface.panel", "accent.primary", "text.primary"}},
        {"Knob Modulation", C::controls, "vellum::view::KnobModulationView",
         "vellum/view/knob_modulation_view.hpp", "Knob Modulation",
         "Saturn modulation rings — per-source depth, bipolar",
         {"surface.panel", "accent.primary", "text.primary"}},
        {"Waveform Recorder", C::audio, "vellum::view::WaveformRecorderView",
         "vellum/view/waveform_recorder_view.hpp", "Waveform Recorder",
         "Waveform editor + recorder — trim, fades, slice, zoom, record",
         {"surface.panel", "accent.primary", "text.primary"}},

        // ── Feedback ───────────────────────────────────────────────────────
        {"InlineBanner", C::feedback, "vellum::view::InlineBanner", "vellum/view/gap_widgets.hpp", "InlineBanner",
         "Full-width status message",
         {"bg.elevated", "accent.primary", "accent.error", "accent.success"}},
        {"EmptyState", C::feedback, "vellum::view::EmptyState", "vellum/view/gap_widgets.hpp", "EmptyState",
         "Placeholder with a call to action",
         {"bg.surface", "text.secondary", "accent.primary"}},
    };
}

}  // namespace

const std::vector<ComponentInfo>& catalog() {
    static const std::vector<ComponentInfo> kCatalog = build_catalog();
    return kCatalog;
}

const ComponentInfo* find(std::string_view name) {
    const auto& all = catalog();
    auto it = std::find_if(all.begin(), all.end(),
                           [&](const ComponentInfo& c) { return c.name == name; });
    return it == all.end() ? nullptr : &*it;
}

std::vector<const ComponentInfo*> in_category(Category c) {
    std::vector<const ComponentInfo*> out;
    for (const auto& info : catalog()) {
        if (info.category == c) out.push_back(&info);
    }
    return out;
}

}  // namespace vellum::design
