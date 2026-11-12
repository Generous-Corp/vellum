#include <vellum/authoring/js_application.hpp>

#include <array>
#include <cstdio>
#include <cmath>
#include <string>

namespace vellum::authoring {
namespace {

void append_string(std::string& out, const std::string& value) {
    out.push_back('"');
    for (const unsigned char c : value) {
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20U) {
                    constexpr char hex[] = "0123456789abcdef";
                    out += "\\u00";
                    out.push_back(hex[c >> 4U]);
                    out.push_back(hex[c & 0xFU]);
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    out.push_back('"');
}

void append_number(std::string& out, float value) {
    if (!std::isfinite(value)) {
        out += "0";
        return;
    }
    std::array<char, 32> buffer{};
    const int length = std::snprintf(
        buffer.data(), buffer.size(), "%.9g", static_cast<double>(value));
    if (length > 0) out.append(buffer.data(), static_cast<std::size_t>(length));
}

}  // namespace

std::string tree_dump_json(const RenderedApplication& rendered) {
    std::string out = R"({"protocol":"vellum.tree-dump.v1","width":)";
    append_number(out, rendered.scene.width);
    out += ",\"height\":";
    append_number(out, rendered.scene.height);
    out += ",\"nodes\":[";
    bool first = true;
    for (const auto& node : rendered.nodes) {
        if (!first) out.push_back(',');
        first = false;
        out += "{\"id\":";
        append_string(out, node.node_id);
        out += ",\"type\":";
        append_string(out, node.type);
        out += ",\"box\":{\"x\":";
        append_number(out, node.box.x);
        out += ",\"y\":";
        append_number(out, node.box.y);
        out += ",\"width\":";
        append_number(out, node.box.width);
        out += ",\"height\":";
        append_number(out, node.box.height);
        out += "},\"source\":";
        if (node.source.file.empty()) {
            out += "null";
        } else {
            out += "{\"file\":";
            append_string(out, node.source.file);
            out += ",\"line\":" + std::to_string(node.source.line);
            out += ",\"column\":" + std::to_string(node.source.column) + "}";
        }
        out += ",\"anchor\":";
        if (node.source.anchor.empty()) {
            out += "null";
        } else {
            append_string(out, node.source.anchor);
        }
        out.push_back('}');
    }
    out += "]}";
    return out;
}

}  // namespace vellum::authoring
