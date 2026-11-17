#pragma once

// Stable design-import entry point.
//
// import_design() turns a design payload into a DesignIR plus an import
// report; emit_design() lowers a DesignIR to JS, C++ or SwiftUI source;
// import_result_to_json() renders an import result as versioned JSON. Each
// function is a thin layer over the framework's parsers, generators and
// report, and adds no parsing or code generation of its own. Importer
// commands, in this repository or in another one, call this header instead of
// the parsers and generators directly.
//
// Stability promise. design_pipeline_api_version() is a semantic version of
// this header's contract, returned as a single integer: the major version.
// While it returns 1, enumerators are only ever appended, struct members are
// only ever appended, diagnostic and loss codes keep their meaning, and the
// JSON schema_version stays 1 with existing keys unchanged. Anything that
// removes or renames a name, changes a code's meaning, reorders or removes a
// JSON key, or changes a function signature increments the version.
//
// The contract is source compatibility: this library and its callers are
// built together against one framework revision, and the structs below embed
// framework types, so no binary layout is promised.
//
// Both entry points are noexcept: failures come back as a Status::failed
// result with a diagnostic, never as an exception. When memory is too low to
// record the diagnostic, the result is still Status::failed, with none. ir_path values in
// diagnostics and losses are best-effort locators in the DesignIR tree such as
// "root/children[1]/children[0]"; use node_id, when present, to identify a
// node.

#include <vellum/view/design_import.hpp>
#include <vellum/view/design_codegen.hpp>
#include <vellum/view/design_ir.hpp>

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace vellum::authoring {

int design_pipeline_api_version() noexcept;

enum class DesignSource {
    figma_json,
    figma_plugin,
    stitch_html,
    v0_tsx,
    pencil_json,
    claude_html,
    designmd,
};

enum class Status { ok, partial, failed };

enum class Severity { info, warning, error };

struct Diagnostic {
    std::string code;
    Severity severity = Severity::info;
    std::string ir_path;
    std::string node_id;
    std::string message;
};

struct LossEntry {
    std::string code;
    std::string ir_path;
    std::string node_id;
    std::string detail;
};

struct LossSummary {
    int conflicted = 0;
    int low_confidence = 0;
    int unresolved = 0;
    int unsupported = 0;
    int total = 0;
};

struct ImportInput {
    DesignSource source = DesignSource::figma_json;
    std::string payload;
    // When set, local assets the design references are collected relative to
    // this directory into the IR's asset manifest. Nothing is fetched.
    std::filesystem::path asset_dir;
    float low_confidence_threshold = 0.6f;
};

struct ImportResult {
    Status status = Status::failed;
    DesignSource source = DesignSource::figma_json;
    std::optional<vellum::view::DesignIR> ir;
    vellum::view::ImportReport report;
    std::vector<Diagnostic> diagnostics;
    std::vector<LossEntry> losses;
    LossSummary loss_summary;
};

ImportResult import_design(const ImportInput& input) noexcept;

enum class EmitTarget { js, cpp, swift };

struct EmitOptions {
    vellum::view::IRAssetManifest asset_manifest;
    vellum::view::CodeGenOptions js;
    vellum::view::CppExportOptions cpp;
    vellum::view::SwiftExportOptions swift;
};

struct EmittedFile {
    std::string role;
    std::string content;
};

struct EmitResult {
    Status status = Status::failed;
    EmitTarget target = EmitTarget::js;
    std::vector<EmittedFile> files;
    std::vector<Diagnostic> diagnostics;
    std::vector<LossEntry> losses;
};

EmitResult emit_design(const vellum::view::DesignIR& ir, EmitTarget target,
                       const EmitOptions& options) noexcept;

const char* design_source_name(DesignSource source) noexcept;
const char* status_name(Status status) noexcept;
const char* severity_name(Severity severity) noexcept;
const char* emit_target_name(EmitTarget target) noexcept;

std::string import_result_to_json(const ImportResult& result);

}  // namespace vellum::authoring
