#pragma once
// The MIDI Learn class sink for VellumRmlUi::Native: applies class changes to
// elements of a NativeContext's document, inside its Scope. UI thread only.
//
// Provenance: written from RmlUi's public element API (MIT). It contains no
// code from any GPL-licensed project.

#include <vellum/rmlui/midi_learn.hpp>
#include <vellum/rmlui/native.hpp>

#include <RmlUi/Core/ElementDocument.h>

namespace vellum::rmlui {

class NativeClassSink final : public ClassSink {
  public:
    explicit NativeClassSink(NativeContext& context) : context_(context) {}
    Status set_class(std::string_view element_id, std::string_view class_name, bool on) override {
        Rml::ElementDocument* document = context_.document();
        if (!document)
            return Status::not_open;
        if (element_id.empty() || class_name.empty() ||
            class_name.find_first_of(" \t\r\n") != std::string_view::npos)
            return Status::rejected;
        NativeContext::Scope scope(context_);
        Rml::Element* element = document->GetElementById(Rml::String(element_id));
        if (!element)
            return Status::rejected;
        element->SetClass(Rml::String(class_name), on);
        return Status::ok;
    }

  private:
    NativeContext& context_;
};

} // namespace vellum::rmlui
