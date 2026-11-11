#pragma once
// A settings window's model for RmlUi editors: pages behind category buttons,
// rows of controls, and for each row whether it is saved per instance (with
// the DAW project) or globally (shared config), and whether the engine can do
// it yet. Header-only and RmlUi-free; a presenter (the editor's document, or
// the RmlUi helper) reads and drives it.
//
// Two seams keep it independent of where values live and of what the engine
// supports:
// - SettingsStore reads and writes a value in a scope. MemorySettingsStore is
//   the in-memory one for tests and for products until a persistent store is
//   wired in.
// - CapabilityAdapter says whether a row is enabled, or disabled with a
//   reason. A disabled row keeps and shows its saved value, so enabling the
//   feature later restores it.
//
// A choice row can take its items from a provider (MIDI ports, preset files,
// skins): open_choice() refills it, and a saved value missing from the list
// stays selected and is reported as missing rather than replaced.
//
// Window scale is a built-in row whose scope each product chooses
// (SettingsOptions::window_scale_scope, global by default). A size the host
// forces is applied but not saved as the user's choice.
//
// Threading: UI thread only.
//
// Provenance: written for this module from user-visible behaviour (a
// settings window with per-instance and global settings, disabled engine
// features, provider-filled lists). It contains no code from any
// GPL-licensed project.

#include <vellum/rmlui/backend.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <functional>
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace vellum::rmlui {

enum class SettingScope : std::uint8_t { instance, global };
enum class SettingKind : std::uint8_t { toggle, choice, number, text, action };

struct RowState {
    bool enabled = true;
    std::string reason; // why it is disabled; empty when enabled
};

struct ChoiceItem {
    std::string value; // what is stored
    std::string label; // what is shown
};

struct SettingRow {
    std::string id;
    std::string label;
    SettingKind kind = SettingKind::toggle;
    SettingScope scope = SettingScope::global;
    ModelValue default_value{};
    // number rows
    double min = 0.0;
    double max = 1.0;
    std::string unit;
    // choice rows: a fixed list, or a provider read on open_choice()
    std::vector<ChoiceItem> choices;
    std::function<std::vector<ChoiceItem>()> choice_source;
    // action rows (for example Panic)
    std::function<void()> run;
    // The row's own availability, for a row whose command knows it (Panic
    // knows whether the host services it). Combined with the adapter: either
    // can disable the row.
    std::function<RowState()> state;
};

struct SettingsPage {
    std::string id;
    std::string title;
    std::function<bool()> available; // empty: always available
    std::vector<SettingRow> rows;
};

class SettingsStore {
  public:
    virtual ~SettingsStore() = default;
    virtual std::optional<ModelValue> read(SettingScope scope, std::string_view key) = 0;
    virtual Status write(SettingScope scope, std::string_view key, const ModelValue& value) = 0;
};

class MemorySettingsStore final : public SettingsStore {
  public:
    std::optional<ModelValue> read(SettingScope scope, std::string_view key) override {
        const auto it = values_.find({scope, std::string(key)});
        if (it == values_.end())
            return std::nullopt;
        return it->second;
    }
    Status write(SettingScope scope, std::string_view key, const ModelValue& value) override {
        values_[{scope, std::string(key)}] = value;
        return Status::ok;
    }
    std::size_t writes_in(SettingScope scope) const {
        return static_cast<std::size_t>(std::count_if(
            values_.begin(), values_.end(), [&](const auto& entry) { return entry.first.first == scope; }));
    }

  private:
    std::map<std::pair<SettingScope, std::string>, ModelValue> values_;
};

class CapabilityAdapter {
  public:
    virtual ~CapabilityAdapter() = default;
    virtual RowState state(std::string_view row_id) = 0;
};

struct SettingsOptions {
    // Whether the window scale is remembered per instance or globally. Each
    // product sets it to match its original.
    SettingScope window_scale_scope = SettingScope::global;
    double window_scale_default = 1.0;
    double window_scale_min = 0.5;
    double window_scale_max = 3.0;
};

// What a choice row shows when it opens.
struct ChoiceView {
    std::vector<ChoiceItem> items;
    std::optional<std::size_t> selected; // index into items
    bool missing = false;                 // the saved value is not in items
    std::string missing_value;            // that saved value
};

inline constexpr std::string_view kSettingsSelectedPageKey = "settings.page";
inline constexpr std::string_view kWindowScaleKey = "window.scale";

class SettingsModel final {
  public:
    explicit SettingsModel(SettingsStore& store, CapabilityAdapter* capabilities = nullptr,
                           SettingsOptions options = {})
        : store_(store), capabilities_(capabilities), options_(options) {}

    // Pages are shown in the order they are added.
    void add_page(SettingsPage page) {
        pages_.push_back(std::move(page));
    }

    // The pages to show now: those whose `available` is empty or true.
    std::vector<const SettingsPage*> pages() const {
        std::vector<const SettingsPage*> shown;
        for (const auto& page : pages_)
            if (!page.available || page.available())
                shown.push_back(&page);
        return shown;
    }

    // The selected page: the remembered one if it is still shown, else the
    // first shown page; empty when no page is shown.
    std::string selected_page() {
        const auto shown = pages();
        if (shown.empty())
            return {};
        const auto saved = store_.read(SettingScope::instance, kSettingsSelectedPageKey);
        if (saved && saved->kind == ModelValue::Kind::string)
            for (const auto* page : shown)
                if (page->id == saved->string)
                    return page->id;
        return shown.front()->id;
    }

    // Remembers the selected page per instance. rejected for a page not shown.
    Status select_page(std::string_view id) {
        for (const auto* page : pages())
            if (page->id == id)
                return store_.write(SettingScope::instance, kSettingsSelectedPageKey,
                                    ModelValue::string_value(std::string(id)));
        return Status::rejected;
    }

    // Enabled, or disabled with a reason: the row's own answer first, then
    // the adapter's. Unknown rows are disabled.
    RowState state(std::string_view row_id) {
        const SettingRow* row = find(row_id);
        if (!row)
            return {false, "unknown setting"};
        if (row->state) {
            RowState own = row->state();
            if (!own.enabled)
                return own;
        }
        return capabilities_ ? capabilities_->state(row_id) : RowState{};
    }

    // The stored value, or the row's default. A disabled row still has one.
    ModelValue value(std::string_view row_id) {
        const SettingRow* row = find(row_id);
        if (!row)
            return {};
        if (auto stored = store_.read(row->scope, row->id); stored && fits(*row, *stored))
            return *stored;
        return row->default_value;
    }

    // Writes a value in the row's scope. rejected when the row is unknown,
    // disabled, an action, or the value does not fit its kind; a number is
    // clamped to the row's range.
    Status set(std::string_view row_id, ModelValue value) {
        const SettingRow* row = find(row_id);
        if (!row || row->kind == SettingKind::action || !state(row_id).enabled || !fits(*row, value))
            return Status::rejected;
        if (row->kind == SettingKind::number)
            value.number = std::clamp(value.number, row->min, row->max);
        return store_.write(row->scope, row->id, value);
    }

    // Runs an action row. rejected when it is unknown, not an action, has no
    // command, or is disabled.
    Status run(std::string_view row_id) {
        const SettingRow* row = find(row_id);
        if (!row || row->kind != SettingKind::action || !row->run || !state(row_id).enabled)
            return Status::rejected;
        row->run();
        return Status::ok;
    }

    // Fills a choice row for display, asking its provider now (call when the
    // list opens, not every frame).
    ChoiceView open_choice(std::string_view row_id) {
        ChoiceView view;
        const SettingRow* row = find(row_id);
        if (!row || row->kind != SettingKind::choice)
            return view;
        view.items = row->choice_source ? row->choice_source() : row->choices;
        const ModelValue current = value(row_id);
        if (current.kind != ModelValue::Kind::string || current.string.empty())
            return view;
        for (std::size_t i = 0; i < view.items.size(); ++i)
            if (view.items[i].value == current.string)
                view.selected = i;
        if (!view.selected) {
            view.missing = true;
            view.missing_value = current.string;
        }
        return view;
    }

    // The window scale: the user's saved choice in the product's scope, else
    // the default.
    double window_scale() {
        const auto saved = store_.read(options_.window_scale_scope, kWindowScaleKey);
        if (saved && saved->kind == ModelValue::Kind::number && std::isfinite(saved->number))
            return std::clamp(saved->number, options_.window_scale_min, options_.window_scale_max);
        return options_.window_scale_default;
    }

    // A scale the user chose is saved; one the host forced (a minimum size, a
    // DAW's own zoom) is not, so it never becomes the remembered choice.
    // rejected for a value that is not finite.
    Status set_window_scale(double scale, bool chosen_by_user) {
        if (!std::isfinite(scale))
            return Status::rejected;
        if (!chosen_by_user)
            return Status::ok;
        scale = std::clamp(scale, options_.window_scale_min, options_.window_scale_max);
        return store_.write(options_.window_scale_scope, kWindowScaleKey, ModelValue::number_value(scale));
    }

    const SettingRow* find(std::string_view row_id) const {
        for (const auto& page : pages_)
            for (const auto& row : page.rows)
                if (row.id == row_id)
                    return &row;
        return nullptr;
    }

  private:
    static bool fits(const SettingRow& row, const ModelValue& value) {
        switch (row.kind) {
        case SettingKind::toggle:
            return value.kind == ModelValue::Kind::boolean;
        case SettingKind::number:
            return value.kind == ModelValue::Kind::number && std::isfinite(value.number);
        case SettingKind::choice:
        case SettingKind::text:
            return value.kind == ModelValue::Kind::string;
        case SettingKind::action:
            return false;
        }
        return false;
    }

    SettingsStore& store_;
    CapabilityAdapter* capabilities_;
    SettingsOptions options_;
    std::vector<SettingsPage> pages_;
};

} // namespace vellum::rmlui
