#pragma once
// The MIDI panic button for RmlUi editors: the RmlUi-free half of the stock
// panic button Pulp's view layer draws (its PanicButton and PanicTarget). One
// action asks the plug-in to silence its voices and, where the host has MIDI
// output, to end the notes it sent downstream; element classes show whether
// the current host services the request at all.
//
// The editor supplies a PanicHost (the same two calls as Pulp's PanicTarget,
// which its format layer builds from a Processor), tracks each panic button
// element, forwards its action events to on_event(), and calls refresh() with
// a ClassSink once per UI tick, before the context updates.
//
// Actions: "midi.panic" asks for the hard stop (CC 120) and
// "midi.panic:release" for the release-only panic (CC 123). Any other
// "midi.panic:<x>" is refused.
//
// Classes: midi-panic-unavailable while no panic request is serviced (the
// stock sheet hides the element), and midi-panic-midi while a request also
// reaches the host's MIDI output.
//
// Threading: UI thread only, like every RmlUi call.
//
// Provenance: written from Pulp's panic button contract (panic_button.hpp,
// MIT) and this module's host bridge. It contains no code from any
// GPL-licensed project.

#include <vellum/rmlui/element_classes.hpp>
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

// What a panic request does in the current host.
enum class PanicAvailability : std::uint8_t {
    unavailable,     // nothing services the request
    voices_only,     // the plug-in's voices stop; no MIDI is sent downstream
    voices_and_midi, // voices stop and note-offs plus channel panic go to the MIDI output
};

inline constexpr std::string_view kMidiPanicAction = "midi.panic";
inline constexpr std::string_view kMidiPanicReleaseSuffix = "release";
inline constexpr std::string_view kMidiPanicUnavailableClass = "midi-panic-unavailable";
inline constexpr std::string_view kMidiPanicMidiClass = "midi-panic-midi";

// The request a panic action asks for: true for the release-only panic. Nothing
// when the event is not a well-formed panic action.
inline std::optional<bool> parse_midi_panic_action(const HostEvent& event) {
    if (event.kind != HostEventKind::action)
        return std::nullopt;
    const std::string_view id = event.id;
    if (id == kMidiPanicAction)
        return false;
    if (id.size() == kMidiPanicAction.size() + 1 + kMidiPanicReleaseSuffix.size() &&
        id.substr(0, kMidiPanicAction.size()) == kMidiPanicAction &&
        id[kMidiPanicAction.size()] == ':' &&
        id.substr(kMidiPanicAction.size() + 1) == kMidiPanicReleaseSuffix)
        return true;
    return std::nullopt;
}

// The editor's panic seam, as Pulp's PanicTarget presents it. An empty
// availability reads as unavailable.
struct PanicHost {
    std::function<void(bool release_only)> request;
    std::function<PanicAvailability()> availability;
};

class PanicBridge final {
  public:
    explicit PanicBridge(PanicHost host) : host_(std::move(host)) {}

    // Shows availability on the element `element_id` (a panic button).
    void track(std::string element_id) {
        for (const auto& tracked : tracked_)
            if (tracked.element_id == element_id)
                return;
        tracked_.push_back(Tracked{std::move(element_id), {}});
    }

    // Raises the request a panic action asks for. rejected for anything that
    // is not a panic action, or when the host throws; missing_capability, and
    // no request, while the host services none.
    HostStatus on_event(const HostEvent& event) {
        const auto release_only = parse_midi_panic_action(event);
        if (!release_only)
            return HostStatus::rejected;
        if (availability() == PanicAvailability::unavailable || !host_.request)
            return HostStatus::missing_capability;
        try {
            host_.request(*release_only);
        } catch (...) {
            return HostStatus::rejected;
        }
        ++requests_;
        return HostStatus::ok;
    }

    // What the host can do now; unavailable when it cannot say.
    PanicAvailability availability() {
        if (!host_.availability)
            return PanicAvailability::unavailable;
        try {
            return host_.availability();
        } catch (...) {
            return PanicAvailability::unavailable;
        }
    }

    // Sends the sink each class that changed since it last accepted one. A
    // change the sink refuses is offered again on the next refresh. Returns
    // the number of changes the sink accepted.
    std::size_t refresh(ClassSink& sink) {
        const PanicAvailability now = availability();
        const std::array<bool, 2> wanted{now == PanicAvailability::unavailable,
                                         now == PanicAvailability::voices_and_midi};
        const std::array<std::string_view, 2> classes{kMidiPanicUnavailableClass,
                                                      kMidiPanicMidiClass};
        std::size_t applied = 0;
        for (auto& tracked : tracked_) {
            for (std::size_t i = 0; i < wanted.size(); ++i) {
                if (wanted[i] == tracked.applied[i])
                    continue;
                if (sink.set_class(tracked.element_id, classes[i], wanted[i]) == Status::ok) {
                    tracked.applied[i] = wanted[i];
                    ++applied;
                }
            }
        }
        return applied;
    }

    // Requests this bridge has raised.
    std::uint64_t request_count() const noexcept {
        return requests_;
    }

  private:
    struct Tracked {
        std::string element_id;
        std::array<bool, 2> applied; // unavailable, midi
    };

    PanicHost host_;
    std::vector<Tracked> tracked_;
    std::uint64_t requests_ = 0;
};

// The stock stylesheet, identical to the installed
// share/VellumRmlUi/midi-panic.rcss. Serve it from an asset provider and link
// it before the editor's own sheets, which can then override it.
inline std::string_view midi_panic_stylesheet() {
    return R"rcss(/* MIDI panic button states, set by vellum::rmlui::PanicBridge. A host that
   services no panic requests hides the button, so an editor can always
   include it and never shows a control that does nothing. Visibility, not
   display: editors set display on their controls (often by id, which would
   outrank this class), and a hidden button keeps its place in the layout.
   To keep it visible but inert instead, override .midi-panic-unavailable
   after this sheet. */
.midi-panic-unavailable {
    visibility: hidden;
}
)rcss";
}

} // namespace vellum::rmlui
