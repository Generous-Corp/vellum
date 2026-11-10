#pragma once
// Class changes on a loaded document's elements, the way this module's
// editor helpers (MIDI Learn, MIDI panic) show state: a ClassSink, and the
// sink over a Core Backend. VellumRmlUi::Native's sink is in
// element_classes_native.hpp. UI thread only.
//
// Provenance: written for this module. It contains no code from any
// GPL-licensed project.

#include <vellum/rmlui/backend.hpp>

#include <string_view>

namespace vellum::rmlui {

// Applies a class change to an element. Returns ok, or the reason it could not.
class ClassSink {
  public:
    virtual ~ClassSink() = default;
    virtual Status set_class(std::string_view element_id, std::string_view class_name, bool on) = 0;
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

} // namespace vellum::rmlui
