// SPDX-License-Identifier: MIT
//
// Scoring an input snapshot against every format in a detection manifest,
// and drafting a new-format report for an export the manifest does not fully
// recognize.

#pragma once

#include <vellum/authoring/import_detect.hpp>

#include <string>
#include <vector>

namespace vellum::authoring::import_detect {

// Result of scanning an input file or directory and matching against
// every (source, format) pair in the manifest.
struct DetectionResult {
    bool ok = false;                   // false on schema/manifest errors
    std::string error;                 // populated when ok=false

    std::string source;                // empty when no match
    std::string format_version;
    std::string parser_version;
    int matched_clauses = 0;
    int total_clauses = 0;
    int confidence_pct = 0;            // 100 * matched / total
    std::vector<std::string> matched_kinds;     // distinct kind strings
    std::vector<std::string> unmatched_kinds;
};

// Detect the highest-confidence (source, format) for the given input.
// Iterates every entry in the manifest, scoring matched/total clauses.
// Returns the entry with the most matches; ties resolved in manifest
// order. When no entry has any matches, ok=true with empty source.
DetectionResult detect(const ImportsManifest& manifest,
                       const InputSnapshot& snap);

// ── Reporting ──────────────────────────────────────────────────────────

// Output of `--report-new-format`: a structured fingerprint diff
// suitable for hand-editing into a new `detected-formats` entry.
struct NewFormatReport {
    std::string candidate_source;
    std::string candidate_format_version;
    std::string based_on_source;
    std::string based_on_format_version;
    std::vector<std::string> additions;         // Tailwind tokens the format does not list
    std::vector<std::string> removals;          // listed Tailwind tokens the input lacks
    std::vector<std::string> script_additions;  // script srcs no html-script-src clause matches
    std::vector<std::string> script_removals;   // html-script-src regexes no script src matches
    std::vector<std::string> type_additions;    // script types no html-script-type clause names
    std::vector<std::string> type_removals;     // html-script-type values the input lacks
};

// Build a NewFormatReport by diffing `snap` against the closest match
// in `manifest`: Tailwind tokens, script srcs and script types in both
// directions. Suggests a candidate format-version derived from the
// closest match (e.g. "2025.04" -> "2025.04+next"). If there is no
// closest match, source/version stay empty so the caller does not infer
// a real compat entry from guessed data.
NewFormatReport build_new_format_report(const ImportsManifest& manifest,
                                        const InputSnapshot& snap,
                                        const DetectionResult& closest);

// Render the report as JSON suitable for piping into `compat.json`.
std::string render_new_format_json(const NewFormatReport& report);

}  // namespace vellum::authoring::import_detect
