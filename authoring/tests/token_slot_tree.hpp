#pragma once

#include <string>
#include <string_view>

namespace vellum::authoring::testing {

// The colour each token of the slot tree resolves to.
struct SlotTheme final {
    std::string fill = "#14b8a6";
    std::string run = "#f472b6";
    std::string shadow = "#f59e0bcc";
    std::string stop = "#6366f1";
    std::string stroke = "#22c55e";
};

// Which slots carry a `tokenRefs` entry.
struct SlotBindings final {
    bool fill = true;
    bool run = true;
    bool shadow = true;
    bool stop = true;
    bool stroke = true;
};

// A retained tree, as the JavaScript runtime emits it, with one node per
// token slot: a rectangle fill, an attributed text run, a box shadow, a
// gradient stop and a path stroke.
inline std::string slot_tree(const SlotTheme& theme, const SlotBindings& bound = {},
                             std::string_view envelope_extra = {}) {
    const auto receipt = [](std::string_view path, const std::string& value) {
        return std::string(R"({"path":")") + std::string(path) +
               R"(","layer":"theme","value":")" + value + R"("})";
    };
    const auto refs = [](bool on, std::string_view entries) {
        return on ? std::string(R"("tokenRefs":{)") + std::string(entries) + "},"
                  : std::string{};
    };
    return std::string(R"({"protocol":"vellum.authoring-host.v2","kind":"render-result",)") +
           std::string(envelope_extra) +
           R"("tree":{"type":"view","id":"slots","style":{"width":240,"height":160,)"
           R"("backgroundColor":"#0b1020"},"tokens":[)" +
           receipt("c.fill", theme.fill) + "," + receipt("c.run", theme.run) + "," +
           receipt("c.shadow", theme.shadow) + "," + receipt("c.stop", theme.stop) + "," +
           receipt("c.stroke", theme.stroke) +
           R"(],"children":[)"
           R"({"type":"view","id":"fill",)" + refs(bound.fill, R"("backgroundColor":0)") +
           R"("style":{"x":10,"y":10,"width":40,"height":40,"backgroundColor":")" + theme.fill +
           R"("}},)"
           R"({"type":"text","id":"label","style":{"x":60,"y":16,"width":120,"height":20,)"
           R"("fontSize":16,"color":"#e5e7eb"},"children":[{"type":"text-run","id":"label/run",)"
           R"("text":"RUN",)" + refs(bound.run, R"("color":1)") +
           R"("style":{"color":")" + theme.run + R"("}}]},)"
           R"({"type":"view","id":"shadow",)" + refs(bound.shadow, R"("boxShadow":2)") +
           R"("style":{"x":14,"y":70,"width":36,"height":36,"backgroundColor":"#334155",)"
           R"("boxShadow":{"offsetX":8,"offsetY":8,"spreadRadius":4,"color":")" + theme.shadow +
           R"("}}},)"
           R"({"type":"view","id":"gradient",)" +
           refs(bound.stop, R"("backgroundLinearGradient.stops.0":3)") +
           R"("style":{"x":80,"y":70,"width":60,"height":40,"backgroundLinearGradient":)"
           R"({"angle":90,"stops":[{"position":0,"color":")" + theme.stop +
           R"("},{"position":1,"color":"#ffffff"}]}}},)"
           R"({"type":"path","id":"zig","d":"M0 0 L40 0 L40 40 Z","fill":"none","stroke":")" +
           theme.stroke + R"(","strokeWidth":5,)" + refs(bound.stroke, R"("stroke":4)") +
           R"("style":{"x":170,"y":70,"width":40,"height":40}}]}})";
}

}  // namespace vellum::authoring::testing
