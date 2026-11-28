// SPDX-License-Identifier: MIT
//
// The building blocks of design-import source detection: parsing the
// detection manifest (`imports/<source>/detected-formats` entries),
// snapshotting a file or directory, and matching each fingerprint clause
// kind against the snapshot. Scoring a snapshot against every format and
// drafting a new-format report build on these.
//
// This module is intentionally self-contained: no view or state link
// dependencies, so the detector can be unit-tested without dragging the
// design-import pipeline (Skia, V8, Yoga) into the test binary.

#pragma once

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace vellum::authoring::import_detect {

// One fingerprint clause from compat.json[imports/<src>/detected-formats/<i>/fingerprint].
struct FingerprintClause {
    enum class Kind {
        directory_files,        // {"kind":"directory-files", "files":[...]}
        html_script_src,        // {"kind":"html-script-src", "regex":"..."}
        html_script_type,       // {"kind":"html-script-type", "value":"..."}
        tailwind_config_token,  // {"kind":"tailwind-config-token", "any-of":[...]}
        filename,               // {"kind":"filename", "regex":"(?i)^DESIGN\\.md$"}
        frontmatter_fence,      // {"kind":"frontmatter-fence", "value":"---"}
        frontmatter_key,        // {"kind":"frontmatter-key", "required":"name"}
                                //   or {"kind":"frontmatter-key", "any-of":["colors","typography"]}
        json_key_equals,        // {"kind":"json-key-equals", "path":"provenance.adapter", "value":"figma-plugin"}
        unknown,
    };

    Kind kind = Kind::unknown;
    std::vector<std::string> files;     // directory-files
    std::string regex;                  // html-script-src, filename
    std::string value;                  // html-script-type, frontmatter-fence, json-key-equals
    std::vector<std::string> any_of;    // tailwind-config-token, frontmatter-key
    std::string required;               // frontmatter-key (single required key)
    std::string path;                   // json-key-equals: dotted path into the parsed JSON
    std::string raw_kind;               // for diagnostics on unknown clauses
};

// One format entry from compat.json[imports/<src>/detected-formats/<i>].
struct FormatEntry {
    std::string format_version;
    std::string parser_version;        // duplicated from the source-level
                                       // entry for convenience at match time
    std::string introduced;
    std::string deprecated;            // empty when null/absent
    std::vector<FingerprintClause> fingerprint;
    std::string notes;
    // Detection-strictness controls (used by DESIGN.md to avoid false
    // positives on generic Jekyll/Hugo Markdown frontmatter):
    //   match == "all-of"       → require matched == total
    //   min_confidence_pct > 0  → require confidence >= threshold
    std::string match;                 // empty or "all-of"
    int min_confidence_pct = 0;
};

// One source from compat.json[imports/<src>].
struct SourceEntry {
    std::string source;                // "stitch", "claude", ...
    std::string parser_version;
    std::vector<FormatEntry> formats;
};

// Top-level imports section parsed from compat.json.
struct ImportsManifest {
    std::string compat_schema_version;
    std::vector<SourceEntry> sources;
};

// ── Manifest parsing ───────────────────────────────────────────────────

// Parse compat.json text → ImportsManifest. Returns std::nullopt on
// malformed JSON or missing `imports` section. Tolerates extra
// top-level keys so broader compat sections can coexist with imports.
std::optional<ImportsManifest> parse_compat_json(const std::string& text);

// Walk parents of `start_dir` (including `start_dir` itself) until a
// `compat.json` is found. Returns empty path when none.
std::filesystem::path find_compat_json(const std::filesystem::path& start_dir);

// ── Fingerprinting ─────────────────────────────────────────────────────

// Snapshot of the input file/directory used by every clause matcher.
// We compute it once per detect-only call; clauses are cheap arithmetic
// over the snapshot.
struct InputSnapshot {
    bool is_directory = false;
    std::filesystem::path root;
    std::string filename;                          // basename of the input file,
                                                   // or DESIGN.md fallback in a
                                                   // Markdown-only directory
    std::vector<std::string> directory_basenames;  // top-level only
    std::string html_text;             // primary HTML content (file or
                                       // dir/code.html / dir/index.html)
    std::vector<std::string> script_srcs;          // <script src="...">
    std::vector<std::string> script_types;         // <script type="...">
    std::vector<std::string> tailwind_tokens;      // tw.config.theme
                                                   // .extend.colors keys
                                                   // (and other config
                                                   // identifiers we grep for)
    // DESIGN.md import: populated when the input file ends in `.md`
    // and has a leading YAML frontmatter fence. The detector consumes
    // these via the frontmatter-fence and frontmatter-key clause kinds.
    bool has_frontmatter_fence = false;
    std::vector<std::string> frontmatter_keys;     // top-level YAML keys
};

// Build a snapshot from a file path or a directory path. Best-effort:
// missing pieces stay empty rather than failing.
InputSnapshot snapshot_input(const std::filesystem::path& input);

// Evaluate one clause against a snapshot.
bool match_clause(const FingerprintClause& clause, const InputSnapshot& snap);

}  // namespace vellum::authoring::import_detect
