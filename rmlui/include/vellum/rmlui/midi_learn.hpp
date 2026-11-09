#pragma once
// MIDI Learn for RmlUi editors: the RmlUi-free half of the feature that Pulp's
// view layer draws as a right-click menu and an overlay (its MidiLearnTarget).
// An RmlUi editor raises MIDI Learn actions from its document and shows each
// parameter's state with element classes; this header turns the one into
// calls on the editor's MIDI Learn engine and the other into class changes.
//
// The editor supplies a MidiLearnHost (the same five calls as Pulp's
// MidiLearnTarget, keyed by the string parameter ids RmlUi documents carry),
// tracks each control element against a parameter, forwards its action
// events to on_event(), routes Escape to handle_escape(), and calls refresh()
// with a ClassSink once per UI tick, before the context updates, so a class
// change and its restyle land in the same frame.
//
// Action ids: "midi_learn.begin:<param>", "midi_learn.cancel" and
// "midi_learn.clear:<param>". The parameter is everything after the first ':'
// and may itself contain ':'.
//
// Threading: UI thread only, like every RmlUi call. The bridge starts no thread
// and calls the host and the sink synchronously.
//
// Provenance: written from Pulp's MIDI Learn view contract (midi_learn_menu.hpp,
// MIT) and this module's host bridge. It contains no code from any
// GPL-licensed project.

#include <vellum/rmlui/backend.hpp>
#include <vellum/rmlui/host_bridge.hpp>

#include <array>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

namespace vellum::rmlui {

// What MIDI Learn shows for one parameter.
struct MidiLearnParamView {
    enum class Phase : std::uint8_t {
        idle,      // no mapping, not listening
        listening, // a learn is waiting for a controller for this parameter
        mapped,    // a controller is mapped to this parameter
    };
    Phase phase = Phase::idle;
    std::string mapping_label; // e.g. "CC 74 ch 1"; empty unless mapped
    bool just_bound = false;   // true for a short time after a learn binds
};

// Markup class for a learnable control; the stock sheet reserves its border.
inline constexpr std::string_view kMidiLearnControlClass = "midi-learn-control";
inline constexpr std::string_view kMidiLearnListeningClass = "midi-learn-listening";
inline constexpr std::string_view kMidiLearnBoundClass = "midi-learn-bound";
inline constexpr std::string_view kMidiLearnMappedClass = "midi-learn-mapped";

inline constexpr std::string_view kMidiLearnBeginAction = "midi_learn.begin";
inline constexpr std::string_view kMidiLearnCancelAction = "midi_learn.cancel";
inline constexpr std::string_view kMidiLearnClearAction = "midi_learn.clear";

struct ClassToggle {
    std::string element_id;
    std::string class_name;
    bool on = false;
};

// The three class states for one element, given its parameter's view.
inline std::array<ClassToggle, 3> midi_learn_class_toggles(std::string_view element_id,
                                                           const MidiLearnParamView& view) {
    using Phase = MidiLearnParamView::Phase;
    const std::string id(element_id);
    return {ClassToggle{id, std::string(kMidiLearnListeningClass), view.phase == Phase::listening},
            ClassToggle{id, std::string(kMidiLearnBoundClass), view.just_bound},
            ClassToggle{id, std::string(kMidiLearnMappedClass), view.phase == Phase::mapped}};
}

struct MidiLearnAction {
    enum class Kind : std::uint8_t { begin, cancel, clear };
    Kind kind = Kind::begin;
    std::string param; // empty for cancel
};

// An action event, or nothing when the event is not a well-formed MIDI Learn
// action: begin and clear need a non-empty parameter; cancel takes none, so
// "midi_learn.cancel:x" is refused rather than read as a cancel.
inline std::optional<MidiLearnAction> parse_midi_learn_action(const HostEvent& event) {
    if (event.kind != HostEventKind::action)
        return std::nullopt;
    const std::string_view id = event.id;
    const auto colon = id.find(':');
    const std::string_view verb = id.substr(0, colon);
    const bool has_param = colon != std::string_view::npos;
    const std::string_view param = has_param ? id.substr(colon + 1) : std::string_view{};
    if (verb == kMidiLearnCancelAction)
        return has_param ? std::nullopt
                         : std::optional<MidiLearnAction>(MidiLearnAction{MidiLearnAction::Kind::cancel, {}});
    MidiLearnAction action;
    if (verb == kMidiLearnBeginAction)
        action.kind = MidiLearnAction::Kind::begin;
    else if (verb == kMidiLearnClearAction)
        action.kind = MidiLearnAction::Kind::clear;
    else
        return std::nullopt;
    if (param.empty())
        return std::nullopt;
    action.param = std::string(param);
    return action;
}

// The editor's MIDI Learn engine, as Pulp's MidiLearnTarget presents it. Any
// member may be empty; an empty or false `available` means MIDI Learn is not
// present, and every tracked element then shows as idle.
struct MidiLearnHost {
    std::function<bool()> available;
    std::function<MidiLearnParamView(std::string_view param)> view;
    std::function<void(std::string_view param)> begin;
    std::function<void()> cancel;
    std::function<void(std::string_view param)> clear;
};

// Applies a class change to an element. Returns ok, or the reason it could not.
class ClassSink {
  public:
    virtual ~ClassSink() = default;
    virtual Status set_class(std::string_view element_id, std::string_view class_name, bool on) = 0;
};

class MidiLearnBridge final {
  public:
    explicit MidiLearnBridge(MidiLearnHost host) : host_(std::move(host)) {}

    // Shows `param`'s state on the element `element_id`. Tracking an element
    // again moves it to the new parameter.
    void track(std::string element_id, std::string param) {
        for (auto& tracked : tracked_) {
            if (tracked.element_id == element_id) {
                tracked.param = std::move(param);
                return;
            }
        }
        tracked_.push_back(Tracked{std::move(element_id), std::move(param), {}});
    }

    // Routes a MIDI Learn action to the host. rejected for anything that is
    // not a well-formed MIDI Learn action, or when the host throws;
    // missing_capability when MIDI Learn is unavailable or the host lacks the
    // call.
    HostStatus on_event(const HostEvent& event) {
        const auto action = parse_midi_learn_action(event);
        if (!action)
            return HostStatus::rejected;
        if (!available())
            return HostStatus::missing_capability;
        try {
            switch (action->kind) {
            case MidiLearnAction::Kind::begin:
                if (!host_.begin)
                    return HostStatus::missing_capability;
                host_.begin(action->param);
                break;
            case MidiLearnAction::Kind::cancel:
                if (!host_.cancel)
                    return HostStatus::missing_capability;
                host_.cancel();
                break;
            case MidiLearnAction::Kind::clear:
                if (!host_.clear)
                    return HostStatus::missing_capability;
                host_.clear(action->param);
                break;
            }
        } catch (...) {
            return HostStatus::rejected;
        }
        return HostStatus::ok;
    }

    // Cancels a learn that is listening on a tracked parameter. True when it
    // did, so the editor can consume the key.
    bool handle_escape() {
        if (!available() || !host_.cancel)
            return false;
        for (const auto& tracked : tracked_) {
            if (view(tracked.param).phase == MidiLearnParamView::Phase::listening) {
                try {
                    host_.cancel();
                } catch (...) {
                    return false;
                }
                return true;
            }
        }
        return false;
    }

    // Sends the sink each class that changed since it last accepted one, so an
    // unchanged state costs no class write (each write restyles the element).
    // A change the sink refuses is offered again on the next refresh. Returns
    // the number of changes the sink accepted.
    std::size_t refresh(ClassSink& sink) {
        std::size_t applied = 0;
        for (auto& tracked : tracked_) {
            const auto toggles = midi_learn_class_toggles(tracked.element_id, view(tracked.param));
            for (std::size_t i = 0; i < toggles.size(); ++i) {
                if (toggles[i].on == tracked.applied[i])
                    continue;
                if (sink.set_class(toggles[i].element_id, toggles[i].class_name, toggles[i].on) ==
                    Status::ok) {
                    tracked.applied[i] = toggles[i].on;
                    ++applied;
                }
            }
        }
        return applied;
    }

  private:
    struct Tracked {
        std::string element_id;
        std::string param;
        std::array<bool, 3> applied; // classes currently set, in toggle order
    };

    bool available() {
        try {
            return host_.available && host_.available();
        } catch (...) {
            return false;
        }
    }

    // Idle when MIDI Learn is unavailable or the host cannot say.
    MidiLearnParamView view(const std::string& param) {
        if (!available() || !host_.view)
            return {};
        try {
            return host_.view(param);
        } catch (...) {
            return {};
        }
    }

    MidiLearnHost host_;
    std::vector<Tracked> tracked_;
};

// A ClassSink over a Core Backend (VellumRmlUi::Core exposes no elements).
class CoreClassSink final : public ClassSink {
  public:
    explicit CoreClassSink(Backend& backend) : backend_(backend) {}
    Status set_class(std::string_view element_id, std::string_view class_name, bool on) override {
        return backend_.set_element_class(element_id, class_name, on);
    }

  private:
    Backend& backend_;
};

// The stock stylesheet, identical to the installed
// share/VellumRmlUi/midi-learn.rcss. Serve it from an asset provider (for
// example as "vellum/midi-learn.rcss") and link it before the editor's own
// sheets, which can then override it. Learnable controls carry the class
// midi-learn-control, which reserves the border the states colour.
inline std::string_view midi_learn_stylesheet() {
    return R"rcss(/* MIDI Learn states for parameter controls, set by vellum::rmlui::MidiLearnBridge.
   Give each learnable control the class midi-learn-control: it reserves a
   transparent 2px border, and the states only colour it, so a state change
   never moves anything. Override these by declaring rules after this sheet. */
.midi-learn-control {
    border: 2px transparent;
}
.midi-learn-mapped {
    border-color: #4a90e2;
}
.midi-learn-listening {
    border-color: #f5a623;
}
.midi-learn-bound {
    border-color: #7ed321;
}
)rcss";
}

} // namespace vellum::rmlui
