#include <vellum/graphics/render_telemetry.hpp>

#include <charconv>
#include <cmath>
#include <cstdio>
#include <locale>
#include <sstream>
#include <string>
#include <system_error>

namespace vellum::graphics {
namespace {

// JSON numbers are locale-independent: std::to_chars never reads the C or
// C++ locale, so a host that set a comma decimal separator still gets JSON.
std::string json_number(double value) {
    if (!std::isfinite(value)) return "null";
    char buffer[64];
    const auto result = std::to_chars(buffer, buffer + sizeof(buffer), value,
                                      std::chars_format::general, 17);
    if (result.ec != std::errc{}) return "null";
    return {buffer, result.ptr};
}

std::string json_string(const std::string& value) {
    std::string out = "\"";
    for (const char character : value) {
        const auto byte = static_cast<unsigned char>(character);
        if (character == '"' || character == '\\') {
            out += '\\';
            out += character;
        } else if (byte < 0x20U) {
            char escaped[8];
            std::snprintf(escaped, sizeof(escaped), "\\u%04x", static_cast<unsigned>(byte));
            out += escaped;
        } else {
            out += character;
        }
    }
    out += '"';
    return out;
}

void write_cache(std::ostringstream& out, const char* name, const CacheStats& stats) {
    out << '"' << name << "\":{"
        << "\"hits\":" << stats.hits << ",\"misses\":" << stats.misses
        << ",\"hit_rate\":" << json_number(hit_rate(stats))
        << ",\"frame_hits\":" << stats.frame_hits
        << ",\"frame_misses\":" << stats.frame_misses
        << ",\"frame_hit_rate\":" << json_number(frame_hit_rate(stats))
        << ",\"evictions\":" << stats.evictions << ",\"trimmed\":" << stats.trimmed
        << ",\"refused\":" << stats.refused << ",\"entries\":" << stats.entries
        << ",\"bytes\":" << stats.bytes << '}';
}

}  // namespace

double hit_rate(const CacheStats& stats) noexcept {
    const auto lookups = stats.hits + stats.misses;
    return lookups == 0 ? 0.0
                        : static_cast<double>(stats.hits) / static_cast<double>(lookups);
}

double frame_hit_rate(const CacheStats& stats) noexcept {
    const auto lookups = stats.frame_hits + stats.frame_misses;
    return lookups == 0 ? 0.0
                        : static_cast<double>(stats.frame_hits) / static_cast<double>(lookups);
}

const char* to_string(CacheTrimReason reason) noexcept {
    switch (reason) {
        case CacheTrimReason::none: return "none";
        case CacheTrimReason::resize_settled: return "resize_settled";
        case CacheTrimReason::device_lost: return "device_lost";
        case CacheTrimReason::budget_changed: return "budget_changed";
    }
    return "unknown";
}

std::string to_json(const RenderTelemetry& telemetry) {
    std::ostringstream out;
    // Integers too: the global locale may group thousands.
    out.imbue(std::locale::classic());
    out << "{\"frame\":" << telemetry.frame
        << ",\"full_repaint\":" << (telemetry.full_repaint ? "true" : "false")
        << ",\"full_repaint_reason\":" << json_string(telemetry.full_repaint_reason)
        << ",\"damage_area_pixels\":" << telemetry.damage_area_pixels
        << ",\"frame_area_pixels\":" << telemetry.frame_area_pixels
        << ",\"damage_fraction\":" << json_number(telemetry.damage_fraction)
        << ",\"paint_commands_total\":" << telemetry.paint_commands_total
        << ",\"paint_commands_recorded\":" << telemetry.paint_commands_recorded
        << ",\"layer_count\":" << telemetry.layer_count << ',';
    write_cache(out, "paragraphs", telemetry.paragraphs);
    out << ',';
    write_cache(out, "gradients", telemetry.gradients);
    out << ',';
    write_cache(out, "textures", telemetry.textures);
    out << ",\"atlas_bytes\":" << telemetry.atlas_bytes
        << ",\"gpu_time_available\":" << (telemetry.gpu_time_available ? "true" : "false")
        << ",\"gpu_milliseconds\":" << json_number(telemetry.gpu_milliseconds)
        << ",\"cpu_milliseconds\":" << json_number(telemetry.cpu_milliseconds)
        << ",\"resize_settled\":" << (telemetry.resize_settled ? "true" : "false")
        << ",\"trims\":" << telemetry.trims
        << ",\"last_trim\":" << json_string(to_string(telemetry.last_trim)) << '}';
    return out.str();
}

}  // namespace vellum::graphics
