// The Skia half of the GPU log bridge. It is its own target
// (VellumGpuRuntime::SkiaLogBridge) because it is the only device-layer code
// that needs Skia: an audio-only GPU plug-in never links it, and a renderer
// that does calls register_skia_log_bridge(skia_log_bridge_installer()).

#include <gpu_runtime/gpu_diagnostics.hpp>
#include <gpu_runtime/skia_log_bridge.hpp>

#include "include/core/SkRefCnt.h"
#include "include/utils/SkLogHandler.h"

#include <algorithm>
#include <array>
#include <cstdarg>
#include <cstdio>

namespace gpu_runtime {
namespace {

GpuDiagnosticSeverity severity_of(SkLogPriority priority) noexcept {
    switch (priority) {
    case SkLogPriority::kError:
        return GpuDiagnosticSeverity::error;
    case SkLogPriority::kWarning:
        return GpuDiagnosticSeverity::warning;
    case SkLogPriority::kInfo:
    case SkLogPriority::kDebug:
        return GpuDiagnosticSeverity::info;
    }
    return GpuDiagnosticSeverity::info;
}

// Skia may log from any thread, so this holds no state: the counters and the
// text budget live behind forward_skia_log_record().
class TraceLogHandler final : public SkLogHandler {
  public:
    void onLog(SkLogPriority priority, const char format[], va_list args) override {
        std::array<char, kGpuDiagnosticMessageLimit + 1> buffer{};
        int written = 0;
        if (format != nullptr)
            written = std::vsnprintf(buffer.data(), buffer.size(), format, args);
        if (written < 0)
            written = 0;
        const std::size_t length =
            std::min(static_cast<std::size_t>(written), kGpuDiagnosticMessageLimit);
        forward_skia_log_record(severity_of(priority), std::string_view(buffer.data(), length));
    }
};

SkiaLogBridgeStatus install(void*) noexcept {
    // Polite install: Skia's handler is process-global and first-install-wins,
    // and a host DAW may already own it. Read before writing, and treat an
    // occupied slot as a normal outcome: never displace, never retry.
    if (SkLogHandler::GetInstance() != nullptr)
        return SkiaLogBridgeStatus::declined_handler_present;
    // A false return means somebody won the race between the read above and
    // this write. Their handler stands.
    return SkLogHandler::SetInstance(sk_make_sp<TraceLogHandler>())
               ? SkiaLogBridgeStatus::installed
               : SkiaLogBridgeStatus::declined_handler_present;
}

}  // namespace

SkiaLogBridgeInstaller skia_log_bridge_installer() noexcept {
    return {.install = &install, .context = nullptr};
}

}  // namespace gpu_runtime
