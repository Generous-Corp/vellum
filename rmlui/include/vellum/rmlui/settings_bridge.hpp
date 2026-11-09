#pragma once
// The settings window in an RmlUi document: actions from its category
// buttons and controls drive a SettingsModel, and element classes show which
// page is active, which pages and rows are unavailable, and each row's scope.
// Header-only and RmlUi-free, in the MIDI Learn and panic helpers' style.
//
// Actions (HostEvent ids):
// - "settings.page:<page id>" selects a page;
// - "settings.set:<row id>" sets a row from the event's value: a toggle is on
//   for any non-zero value, a number takes the value, a choice takes the item
//   at that index in the list open_choice() last returned for the row;
// - "settings.run:<row id>" runs an action row (Panic).
// The id is everything after the first ':' and may itself contain ':'.
//
// Classes, written only when they change:
// - settings-page-active on the selected page's button;
// - settings-page-unavailable on a page button whose page is not shown;
// - settings-row-disabled on a row the model reports disabled;
// - settings-scope-instance or settings-scope-global on each row.
//
// panic_row() makes the Panic action row from the panic seam, so Panic and
// the panic button share one path.
//
// Threading: UI thread only, like every RmlUi call.
//
// Provenance: written for this module. It contains no code from any
// GPL-licensed project.

#include <vellum/rmlui/element_classes.hpp>
#include <vellum/rmlui/host_bridge.hpp>
#include <vellum/rmlui/midi_panic.hpp>
#include <vellum/rmlui/settings.hpp>

#include <algorithm>
#include <array>
#include <cmath>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace vellum::rmlui {

inline constexpr std::string_view kSettingsPageAction = "settings.page";
inline constexpr std::string_view kSettingsSetAction = "settings.set";
inline constexpr std::string_view kSettingsRunAction = "settings.run";
inline constexpr std::string_view kSettingsPageActiveClass = "settings-page-active";
inline constexpr std::string_view kSettingsPageUnavailableClass = "settings-page-unavailable";
inline constexpr std::string_view kSettingsRowDisabledClass = "settings-row-disabled";
inline constexpr std::string_view kSettingsScopeInstanceClass = "settings-scope-instance";
inline constexpr std::string_view kSettingsScopeGlobalClass = "settings-scope-global";

struct SettingsAction {
    enum class Kind : std::uint8_t { page, set, run };
    Kind kind = Kind::page;
    std::string id;
    double value = 0.0;
};

// A settings action, or nothing when the event is not a well-formed one: an
// action event whose id is a known verb, ':' and a non-empty id.
inline std::optional<SettingsAction> parse_settings_action(const HostEvent& event) {
    if (event.kind != HostEventKind::action)
        return std::nullopt;
    const std::string_view text = event.id;
    const auto colon = text.find(':');
    if (colon == std::string_view::npos || colon + 1 == text.size())
        return std::nullopt;
    const std::string_view verb = text.substr(0, colon);
    SettingsAction action;
    if (verb == kSettingsPageAction)
        action.kind = SettingsAction::Kind::page;
    else if (verb == kSettingsSetAction)
        action.kind = SettingsAction::Kind::set;
    else if (verb == kSettingsRunAction)
        action.kind = SettingsAction::Kind::run;
    else
        return std::nullopt;
    action.id = std::string(text.substr(colon + 1));
    action.value = event.value;
    return action;
}

// The Panic action row, running the hard-stop panic through the panic seam.
// It is disabled, with a reason, while the host services no panic request.
inline SettingRow panic_row(PanicHost host, std::string id = "midi.panic",
                            std::string label = "Panic") {
    SettingRow row;
    row.id = std::move(id);
    row.label = std::move(label);
    row.kind = SettingKind::action;
    auto shared = std::make_shared<PanicHost>(std::move(host));
    row.run = [shared] {
        if (shared->request)
            shared->request(false);
    };
    row.state = [shared]() -> RowState {
        PanicAvailability available = PanicAvailability::unavailable;
        if (shared->availability) {
            try {
                available = shared->availability();
            } catch (...) {
                available = PanicAvailability::unavailable;
            }
        }
        if (available == PanicAvailability::unavailable || !shared->request)
            return {false, "this host services no panic request"};
        return {};
    };
    return row;
}

class SettingsBridge final {
  public:
    explicit SettingsBridge(SettingsModel& model) : model_(model) {}

    // The button element that selects `page_id`.
    void track_page_button(std::string element_id, std::string page_id) {
        tracked_.push_back(Tracked{std::move(element_id), std::move(page_id), true, {}});
    }

    // The element that holds the control for `row_id`.
    void track_row(std::string element_id, std::string row_id) {
        tracked_.push_back(Tracked{std::move(element_id), std::move(row_id), false, {}});
    }

    // Applies a settings action. rejected for anything that is not one, or
    // that the model refuses for a reason other than a disabled row;
    // missing_capability for a disabled row; ok otherwise.
    HostStatus on_event(const HostEvent& event) {
        const auto action = parse_settings_action(event);
        if (!action)
            return HostStatus::rejected;
        switch (action->kind) {
        case SettingsAction::Kind::page:
            return model_.select_page(action->id) == Status::ok ? HostStatus::ok : HostStatus::rejected;
        case SettingsAction::Kind::run:
            if (model_.find(action->id) && !model_.state(action->id).enabled)
                return HostStatus::missing_capability;
            try {
                return model_.run(action->id) == Status::ok ? HostStatus::ok : HostStatus::rejected;
            } catch (...) {
                return HostStatus::rejected;
            }
        case SettingsAction::Kind::set:
            return set(action->id, action->value);
        }
        return HostStatus::rejected;
    }

    // The list a choice row shows when it opens; remembered so a following
    // "settings.set" can name an item by index.
    ChoiceView open_choice(std::string_view row_id) {
        ChoiceView view = model_.open_choice(row_id);
        opened_[std::string(row_id)] = view.items;
        return view;
    }

    // Sends the sink each class that changed since it last accepted one.
    // Returns the number of changes the sink accepted.
    std::size_t refresh(ClassSink& sink) {
        const std::string active = model_.selected_page();
        std::vector<std::string> shown;
        for (const auto* page : model_.pages())
            shown.push_back(page->id);
        std::size_t applied = 0;
        for (auto& tracked : tracked_) {
            std::array<std::pair<std::string_view, bool>, 2> wanted{};
            if (tracked.is_page) {
                const bool visible =
                    std::find(shown.begin(), shown.end(), tracked.target) != shown.end();
                wanted = {std::pair{kSettingsPageActiveClass, tracked.target == active},
                          std::pair{kSettingsPageUnavailableClass, !visible}};
            } else {
                const SettingRow* row = model_.find(tracked.target);
                const bool instance = row && row->scope == SettingScope::instance;
                wanted = {std::pair{kSettingsRowDisabledClass, !model_.state(tracked.target).enabled},
                          std::pair{instance ? kSettingsScopeInstanceClass : kSettingsScopeGlobalClass,
                                    row != nullptr}};
            }
            for (const auto& [name, on] : wanted) {
                const std::string key(name);
                const auto it = tracked.applied.find(key);
                const bool current = it != tracked.applied.end() && it->second;
                if (current == on)
                    continue;
                if (sink.set_class(tracked.element_id, name, on) == Status::ok) {
                    tracked.applied[key] = on;
                    ++applied;
                }
            }
        }
        return applied;
    }

  private:
    struct Tracked {
        std::string element_id;
        std::string target; // page id or row id
        bool is_page = false;
        std::map<std::string, bool> applied; // class -> set
    };

    HostStatus set(const std::string& row_id, double value) {
        const SettingRow* row = model_.find(row_id);
        if (!row)
            return HostStatus::rejected;
        if (!model_.state(row_id).enabled)
            return HostStatus::missing_capability;
        if (!std::isfinite(value))
            return HostStatus::rejected;
        ModelValue next;
        switch (row->kind) {
        case SettingKind::toggle:
            next = ModelValue::boolean_value(value != 0.0);
            break;
        case SettingKind::number:
            next = ModelValue::number_value(value);
            break;
        case SettingKind::choice: {
            const auto it = opened_.find(row_id);
            if (it == opened_.end() || value < 0.0 || value != std::floor(value) ||
                value >= static_cast<double>(it->second.size()))
                return HostStatus::rejected;
            next = ModelValue::string_value(it->second[static_cast<std::size_t>(value)].value);
            break;
        }
        case SettingKind::text:
        case SettingKind::action:
            return HostStatus::rejected;
        }
        return model_.set(row_id, next) == Status::ok ? HostStatus::ok : HostStatus::rejected;
    }

    SettingsModel& model_;
    std::vector<Tracked> tracked_;
    std::map<std::string, std::vector<ChoiceItem>> opened_;
};

} // namespace vellum::rmlui
