#pragma once

// Types shared by every RmlUi entry point in this module: the sealed
// VellumRmlUi::Core Backend and the open VellumRmlUi::Native context. None of
// them names an RmlUi type.

#include <vellum/rmlui/export.hpp>

#include <cstdint>
#include <filesystem>
#include <functional>
#include <string>
#include <string_view>
#include <vector>

// Defined when the module provides log sinks and last_load_diagnostics(), so
// a consumer can build against module versions from before and after them.
#define VELLUM_RMLUI_HAS_LOG_SINK 1

namespace vellum::rmlui {

enum class Status : std::uint8_t {
    ok,
    already_open,
    not_open,
    missing_asset,
    rejected,
};

struct Asset {
    std::string path;
    std::string bytes;
};

class VELLUM_RMLUI_API AssetProvider {
  public:
    virtual ~AssetProvider();
    virtual Status load(std::string_view path, Asset& out) = 0;
};

struct AssetManifestEntry {
    std::string path;
    std::string sha256;
};

// Returns the lowercase hex SHA-256 digest of its input. The module carries
// no cryptography of its own; the host supplies the digest it already trusts.
using Sha256Hasher = std::function<std::string(std::string_view)>;

// Reads only regular files inside a declared asset root. The manifest is a
// content closure: a missing hasher, an empty digest or a mismatched digest
// rejects the read.
class VELLUM_RMLUI_API RootedAssetProvider final : public AssetProvider {
  public:
    RootedAssetProvider(std::filesystem::path root, std::vector<AssetManifestEntry> manifest,
                        Sha256Hasher sha256_hex, std::uint64_t max_bytes = 16 * 1024 * 1024);

    Status load(std::string_view path, Asset& out) override;

  private:
    std::filesystem::path root_;
    std::vector<AssetManifestEntry> manifest_;
    Sha256Hasher sha256_hex_;
    std::uint64_t max_bytes_;
};

// Severity of a message RmlUi logs.
enum class LogLevel : std::uint8_t { always, error, assertion, warning, info, debug };

struct LogMessage {
    LogLevel level = LogLevel::info;
    // Valid only for the duration of the sink call.
    std::string_view text;
};

// Receives the messages RmlUi logs while its owner (a Backend or a
// NativeContext) is calling into RmlUi (opening, loading a document, handling
// input, rendering, closing), on that calling thread. Messages are attributed
// by that call, so two open instances each see only their own.
using LogSink = std::function<void(const LogMessage&)>;

// What RmlUi reported while parsing the most recent RML document passed to
// the owner's load_document(). `dropped_declarations` counts style declarations
// RmlUi rejected as syntax errors and discarded (for example a value carrying
// `!important`, which RmlUi does not support). The document still loads, so a
// non-zero count is the only trace of the lost rule.
struct LoadDiagnostics {
    std::uint32_t errors = 0;
    std::uint32_t warnings = 0;
    std::uint32_t dropped_declarations = 0;
};

} // namespace vellum::rmlui
