// Offline `vellum design` verbs over the design contract: DESIGN.md lint and
// diff, manifest compile, adherence lint, ledger record and theme token
// export. Each verb reads and writes files and calls the design cores; none
// opens a window.

#include "design_tool_verbs.hpp"

#include <vellum/design/design_adherence.hpp>
#include <vellum/design/design_ledger.hpp>
#include <vellum/design/design_manifest.hpp>
#include <vellum/view/design_import.hpp>
#include <vellum/view/design_tokens.hpp>
#include <vellum/view/w3c_tokens.hpp>

#include <choc/text/choc_JSON.h>
#include <choc/text/choc_UTF8.h>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <system_error>

namespace fs = std::filesystem;

namespace vellum::design_tool {

namespace {

constexpr int kDesignMdParseErrorExitCode = 3;

bool has_designmd_parse_error(const vellum::view::DesignMdParseResult& parsed) {
    return std::any_of(parsed.diagnostics.begin(), parsed.diagnostics.end(),
                       [](const auto& diagnostic) {
                           return diagnostic.severity ==
                                  vellum::view::DesignMdSeverity::error;
                       });
}

void print_designmd_diagnostics(
    const std::vector<vellum::view::DesignMdDiagnostic>& diagnostics) {
    for (const auto& diagnostic : diagnostics) {
        const char* severity =
            diagnostic.severity == vellum::view::DesignMdSeverity::error ? "error" :
            diagnostic.severity == vellum::view::DesignMdSeverity::warning ? "warning" :
            "info";
        std::cerr << "[" << severity << "] " << diagnostic.code
                  << " at "
                  << (diagnostic.path.empty() ? "<root>" : diagnostic.path);
        if (diagnostic.line > 0) {
            std::cerr << " (line " << diagnostic.line << ":"
                      << diagnostic.column << ")";
        }
        std::cerr << ": " << diagnostic.message << "\n";
    }
}

bool reject_designmd_parse_errors(
    const vellum::view::DesignMdParseResult& parsed) {
    if (!has_designmd_parse_error(parsed)) return false;
    print_designmd_diagnostics(parsed.diagnostics);
    return true;
}

} // namespace

std::string read_text_file(const fs::path& p) {
    std::ifstream f(p);
    if (!f.is_open()) return {};
    std::stringstream ss;
    ss << f.rdbuf();
    return ss.str();
}

// Atomic write: a full write to a sibling temp file, verified, then a rename
// over the destination: so a crash or full disk mid-write leaves the previous
// file intact rather than truncated. Matters most for the ledger sidecar, whose
// recorded history must survive an interrupted record.
bool write_text_file(const fs::path& p, const std::string& content) {
    fs::path tmp = p;
    tmp += ".vellum-tmp";
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f.is_open()) return false;
        f << content;
        f.flush();
        if (!f.good()) {
            f.close();
            std::error_code ec;
            fs::remove(tmp, ec);
            return false;
        }
    }
    std::error_code ec;
    fs::rename(tmp, p, ec);
    if (ec) {
        fs::remove(tmp, ec);
        return false;
    }
    return true;
}

// Build the design manifest from the standard source options: an explicit
// DESIGN.md or Theme JSON, else the built-in Ink & Signal system. Prints an
// error and returns its CLI exit code on an unreadable, invalid, or tokenless
// source. Shared by `vellum design compile` and `vellum design lint-adherence`.
int build_source_manifest(const fs::path& design_md, const fs::path& theme_json,
                          bool dark, vellum::design::DesignManifest& out) {
    const std::string appearance = dark ? "dark" : "light";
    if (!design_md.empty()) {
        auto text = read_text_file(design_md);
        if (text.empty()) { std::cerr << "Error: cannot read " << design_md << "\n"; return 1; }
        auto parsed = vellum::view::parse_designmd(text);
        if (reject_designmd_parse_errors(parsed)) {
            return kDesignMdParseErrorExitCode;
        }
        auto theme = vellum::view::ir_tokens_to_theme(parsed.ir.tokens);
        if (theme.colors.empty() && theme.dimensions.empty() && theme.strings.empty()) {
            std::cerr << "Error: no tokens parsed from " << design_md << "\n";
            return 1;
        }
        out = vellum::design::compile_design_manifest(
            theme, vellum::design::catalog(), design_md.filename().string(), appearance);
        return 0;
    }
    if (!theme_json.empty()) {
        auto theme = vellum::view::Theme::load_from_file(theme_json.string());
        if (theme.colors.empty() && theme.dimensions.empty() && theme.strings.empty()) {
            std::cerr << "Error: no tokens read from " << theme_json << "\n";
            return 1;
        }
        out = vellum::design::compile_design_manifest(
            theme, vellum::design::catalog(), theme_json.filename().string(), appearance);
        return 0;
    }
    out = vellum::design::compile_ink_signal_manifest(dark);
    return 0;
}

// `vellum design md-lint <DESIGN.md>`: runs the seven Google design.md
// lint rules + section-order. Exit 0 if no error findings; exit 1
// if any error-severity finding is present.
int run_lint(const std::vector<std::string>& rest) {
    if (rest.empty()) {
        std::cerr << "Usage: vellum design md-lint <path/to/DESIGN.md>\n";
        return 1;
    }
    auto text = read_text_file(rest.front());
    if (text.empty()) {
        std::cerr << "Error: cannot read " << rest.front() << "\n";
        return 1;
    }
    auto parsed = vellum::view::parse_designmd(text);
    if (reject_designmd_parse_errors(parsed)) {
        return kDesignMdParseErrorExitCode;
    }
    auto findings = vellum::view::lint_designmd(parsed);
    int errors = 0;
    for (const auto& d : findings) {
        const char* sev = (d.severity == vellum::view::DesignMdSeverity::error)   ? "error" :
                          (d.severity == vellum::view::DesignMdSeverity::warning) ? "warning" : "info";
        std::cout << "[" << sev << "] " << d.code
                  << " at " << (d.path.empty() ? "<root>" : d.path);
        if (d.line > 0) std::cout << " (line " << d.line << ":" << d.column << ")";
        std::cout << ": " << d.message << "\n";
        if (d.severity == vellum::view::DesignMdSeverity::error) ++errors;
    }
    std::cout << "Lint summary: " << findings.size() << " finding(s), "
              << errors << " error(s).\n";
    return errors > 0 ? 1 : 0;
}

// `vellum design md-diff <before.md> <after.md>`: token-level diff plus
// regression flag. Exit 0 if no regression; exit 1 if regression.
int run_diff(const std::vector<std::string>& rest) {
    if (rest.size() < 2) {
        std::cerr << "Usage: vellum design md-diff <before.md> <after.md>\n";
        return 1;
    }
    auto before_text = read_text_file(rest[0]);
    auto after_text  = read_text_file(rest[1]);
    if (before_text.empty() || after_text.empty()) {
        std::cerr << "Error: cannot read one of the input files\n";
        return 1;
    }
    auto before = vellum::view::parse_designmd(before_text);
    auto after  = vellum::view::parse_designmd(after_text);
    const bool before_invalid = has_designmd_parse_error(before);
    const bool after_invalid = has_designmd_parse_error(after);
    if (before_invalid || after_invalid) {
        if (before_invalid) print_designmd_diagnostics(before.diagnostics);
        if (after_invalid) print_designmd_diagnostics(after.diagnostics);
        return kDesignMdParseErrorExitCode;
    }
    auto diff = vellum::view::diff_designmd(before, after);
    auto report = [](const char* group, const vellum::view::DesignMdTokenDiff& d) {
        std::cout << group << ": +" << d.added.size()
                  << " -" << d.removed.size()
                  << " ~" << d.modified.size() << "\n";
        for (const auto& k : d.added)    std::cout << "  + " << k << "\n";
        for (const auto& k : d.removed)  std::cout << "  - " << k << "\n";
        for (const auto& k : d.modified) std::cout << "  ~ " << k << "\n";
    };
    report("colors",     diff.colors);
    report("dimensions", diff.dimensions);
    report("strings",    diff.strings);
    std::cout << "regression: " << (diff.regression ? "true" : "false") << "\n";
    return diff.regression ? 1 : 0;
}

void print_compile_usage() {
    std::cout <<
        "Usage: vellum design compile [options]\n"
        "\n"
        "Compile the design contract (token allowlist + component contracts) into\n"
        "a machine-readable manifest and an LLM binding prompt. The default source\n"
        "is the built-in Ink & Signal system.\n"
        "\n"
        "Options:\n"
        "  --design-md <file>  Compile a project's DESIGN.md tokens instead\n"
        "  --theme <file.json> Compile a saved Theme JSON instead\n"
        "  --dark              Use the dark appearance (default: light)\n"
        "  -o, --out-dir <dir> Output directory (default: current directory)\n"
        "  --json              Emit only design-manifest.json\n"
        "  --prompt            Emit only design-binding-prompt.md\n"
        "  --stdout            Print to stdout instead of writing files\n"
        "  -h, --help          Show this help\n"
        "\n"
        "Outputs (in --out-dir): design-manifest.json, design-binding-prompt.md\n";
}

// `vellum design compile`: compile the design contract into
// design-manifest.json + design-binding-prompt.md. Deterministic; the manifest
// is the token/component allowlist the importer and adherence check share.
int run_compile(const std::vector<std::string>& rest) {
    fs::path out_dir = fs::current_path();
    fs::path design_md;
    fs::path theme_json;
    bool dark = false;
    bool to_stdout = false;
    bool only_json = false;
    bool only_prompt = false;

    for (size_t i = 0; i < rest.size(); ++i) {
        const std::string& a = rest[i];
        auto value_for = [&](const char* flag) -> std::string {
            if (i + 1 >= rest.size() || rest[i + 1].empty() || rest[i + 1][0] == '-') {
                std::cerr << "vellum design compile: " << flag << " requires a value\n";
                return {};
            }
            return rest[++i];
        };
        if (a == "-h" || a == "--help") { print_compile_usage(); return 0; }
        else if (a == "--dark") { dark = true; }
        else if (a == "--stdout") { to_stdout = true; }
        else if (a == "--json") { only_json = true; }
        else if (a == "--prompt") { only_prompt = true; }
        else if (a == "-o" || a == "--out-dir") {
            auto v = value_for("--out-dir"); if (v.empty()) return 2; out_dir = fs::absolute(v);
        } else if (a == "--design-md") {
            auto v = value_for("--design-md"); if (v.empty()) return 2; design_md = fs::absolute(v);
        } else if (a == "--theme") {
            auto v = value_for("--theme"); if (v.empty()) return 2; theme_json = fs::absolute(v);
        } else {
            std::cerr << "vellum design compile: unknown argument '" << a << "'\n";
            return 2;
        }
    }
    if (!design_md.empty() && !theme_json.empty()) {
        std::cerr << "vellum design compile: --design-md and --theme are mutually exclusive\n";
        return 2;
    }
    if (only_json && only_prompt) {
        std::cerr << "vellum design compile: --json and --prompt are mutually exclusive\n";
        return 2;
    }
    enum class Emit { both, json, prompt };
    const Emit emit = only_json ? Emit::json : only_prompt ? Emit::prompt : Emit::both;

    vellum::design::DesignManifest manifest;
    if (const int status =
            build_source_manifest(design_md, theme_json, dark, manifest);
        status != 0) {
        return status;
    }

    const std::string json = vellum::design::manifest_to_json(manifest);
    const std::string prompt = vellum::design::emit_binding_prompt(manifest);

    if (to_stdout) {
        if (emit != Emit::prompt) std::cout << json << "\n";
        if (emit != Emit::json) std::cout << prompt;
        return 0;
    }

    std::error_code ec;
    fs::create_directories(out_dir, ec);
    if (emit != Emit::prompt) {
        auto p = out_dir / "design-manifest.json";
        if (!write_text_file(p, json)) { std::cerr << "Error: cannot write " << p << "\n"; return 1; }
        std::cout << "Wrote " << p.string() << " (" << manifest.tokens.size()
                  << " tokens, " << manifest.components.size() << " components)\n";
    }
    if (emit != Emit::json) {
        auto p = out_dir / "design-binding-prompt.md";
        if (!write_text_file(p, prompt)) { std::cerr << "Error: cannot write " << p << "\n"; return 1; }
        std::cout << "Wrote " << p.string() << "\n";
    }
    return 0;
}

void print_adherence_usage() {
    std::cout <<
        "Usage: vellum design lint-adherence <ui.js> [options]\n"
        "\n"
        "Lint UI JS against the design contract: flags raw hex colors, unknown\n"
        "var(--token) references, and px literals that match a design token.\n"
        "\n"
        "Options:\n"
        "  --manifest <file>   Lint against a compiled design-manifest.json\n"
        "  --design-md <file>  Build the contract from a DESIGN.md instead\n"
        "  --theme <file.json> Build the contract from a saved Theme JSON instead\n"
        "  --dark              Use the dark appearance (default: light)\n"
        "  --strict            Exit non-zero on any finding, not just errors\n"
        "  -h, --help          Show this help\n"
        "\n"
        "With no source flag, lints against the built-in Ink & Signal system.\n"
        "Exit 0 when clean; 1 when an error-severity finding is present (or any\n"
        "finding under --strict).\n";
}

// `vellum design lint-adherence <ui.js>`: the mechanical backstop for the design
// contract. Flags raw colors, unknown tokens, and token-valued px literals.
int run_lint_adherence(const std::vector<std::string>& rest) {
    fs::path js_file;
    fs::path manifest_file;
    fs::path design_md;
    fs::path theme_json;
    bool dark = false;
    bool strict = false;

    for (size_t i = 0; i < rest.size(); ++i) {
        const std::string& a = rest[i];
        auto value_for = [&](const char* flag) -> std::string {
            if (i + 1 >= rest.size() || rest[i + 1].empty() || rest[i + 1][0] == '-') {
                std::cerr << "vellum design lint-adherence: " << flag << " requires a value\n";
                return {};
            }
            return rest[++i];
        };
        if (a == "-h" || a == "--help") { print_adherence_usage(); return 0; }
        else if (a == "--dark") { dark = true; }
        else if (a == "--strict") { strict = true; }
        else if (a == "--manifest") {
            auto v = value_for("--manifest"); if (v.empty()) return 2; manifest_file = fs::absolute(v);
        } else if (a == "--design-md") {
            auto v = value_for("--design-md"); if (v.empty()) return 2; design_md = fs::absolute(v);
        } else if (a == "--theme") {
            auto v = value_for("--theme"); if (v.empty()) return 2; theme_json = fs::absolute(v);
        } else if (!a.empty() && a[0] == '-') {
            std::cerr << "vellum design lint-adherence: unknown argument '" << a << "'\n";
            return 2;
        } else if (js_file.empty()) {
            js_file = fs::absolute(a);
        } else {
            std::cerr << "vellum design lint-adherence: unexpected extra argument '" << a << "'\n";
            return 2;
        }
    }
    if (js_file.empty()) {
        std::cerr << "Usage: vellum design lint-adherence <ui.js> [options]\n";
        return 2;
    }
    if (!manifest_file.empty() && (!design_md.empty() || !theme_json.empty())) {
        std::cerr << "vellum design lint-adherence: --manifest is mutually exclusive with "
                     "--design-md/--theme\n";
        return 2;
    }
    if (!design_md.empty() && !theme_json.empty()) {
        std::cerr << "vellum design lint-adherence: --design-md and --theme are mutually exclusive\n";
        return 2;
    }
    if (!fs::exists(js_file)) {
        std::cerr << "Error: cannot read " << js_file << "\n";
        return 1;
    }
    const std::string js = read_text_file(js_file);

    vellum::design::DesignManifest manifest;
    if (!manifest_file.empty()) {
        auto text = read_text_file(manifest_file);
        if (text.empty()) { std::cerr << "Error: cannot read " << manifest_file << "\n"; return 1; }
        manifest = vellum::design::manifest_from_json(text);
        if (manifest.tokens.empty()) {
            std::cerr << "Error: no tokens in " << manifest_file << "\n";
            return 1;
        }
    } else if (const int status =
                   build_source_manifest(design_md, theme_json, dark, manifest);
               status != 0) {
        return status;
    }

    const auto findings = vellum::design::lint_adherence(js, manifest);
    int errors = 0;
    for (const auto& f : findings) {
        const char* sev = (f.severity == vellum::design::AdherenceSeverity::error)   ? "error"
                        : (f.severity == vellum::design::AdherenceSeverity::warning) ? "warning"
                                                                                   : "info";
        std::cout << "[" << sev << "] " << vellum::design::adherence_kind_name(f.kind) << " at "
                  << js_file.filename().string() << ":" << f.line << ":" << f.column << ": "
                  << f.message << "\n";
        if (f.severity == vellum::design::AdherenceSeverity::error) ++errors;
    }
    std::cout << "Adherence: " << findings.size() << " finding(s), " << errors << " error(s).\n";
    if (errors > 0) return 1;
    if (strict && !findings.empty()) return 1;
    return 0;
}

void print_record_usage() {
    std::cout <<
        "Usage: vellum design record [--ledger <file>] <operation>\n"
        "\n"
        "Maintain the project design ledger (default: .pulp-design-meta.json): the\n"
        "CLI-owned record of emitted design assets, their versions, provenance, and\n"
        "review status. Only this command writes the ledger; skills read it on resume.\n"
        "\n"
        "Operations:\n"
        "  --name <n> --asset <path>   Record (or update) a named asset revision.\n"
        "      --source <s>            Provenance (fig, figma-plugin, claude, …)\n"
        "      --viewport <WxH>        Design viewport (e.g. 340x280)\n"
        "      --system <name>         Bound design system (repeatable)\n"
        "      --inherit-from <ver>    Parent version this revision derives from\n"
        "      --version <ver>         Target an explicit version (else auto v1, v2, …)\n"
        "      --status <s>            needs-review (default) | approved | changes-requested\n"
        "  --remove <name[@version]>   Drop an asset (all versions, or one revision).\n"
        "  --reconcile                 Drop entries whose file no longer exists on disk.\n"
        "  --list [--json]             Print the ledger (human table or JSON) and exit.\n";
}

// `vellum design record`: maintain the project design ledger. All file IO lives
// here; the pure ledger operations live in core/view/src/design_ledger.cpp.
int run_record(const std::vector<std::string>& rest) {
    fs::path ledger_path = ".pulp-design-meta.json";
    std::string name, asset, source, viewport, inherit_from, version, status_str;
    std::vector<std::string> systems;
    std::string remove_selector;
    bool do_reconcile = false, do_list = false, as_json = false;
    bool have_remove = false;

    auto need_value = [&](const std::string& flag, size_t& i) -> const std::string* {
        if (i + 1 >= rest.size()) {
            std::cerr << "vellum design record: " << flag << " requires a value\n";
            return nullptr;
        }
        return &rest[++i];
    };

    for (size_t i = 0; i < rest.size(); ++i) {
        const std::string& a = rest[i];
        if (a == "-h" || a == "--help") { print_record_usage(); return 0; }
        else if (a == "--reconcile") { do_reconcile = true; }
        else if (a == "--list") { do_list = true; }
        else if (a == "--json") { as_json = true; }
        else {
            const std::string* v = need_value(a, i);
            if (!v) return 2;
            if (a == "--ledger") ledger_path = *v;
            else if (a == "--name") name = *v;
            else if (a == "--asset") asset = *v;
            else if (a == "--source") source = *v;
            else if (a == "--viewport") viewport = *v;
            else if (a == "--system") systems.push_back(*v);
            else if (a == "--inherit-from") inherit_from = *v;
            else if (a == "--version") version = *v;
            else if (a == "--status") status_str = *v;
            else if (a == "--remove") { remove_selector = *v; have_remove = true; }
            else {
                std::cerr << "vellum design record: unknown argument '" << a << "'\n";
                return 2;
            }
        }
    }

    // Exactly one operation.
    const int ops = (!name.empty() || !asset.empty()) + have_remove + (do_reconcile ? 1 : 0)
                    + (do_list ? 1 : 0);
    if (ops == 0) {
        std::cerr << "vellum design record: no operation (see --help)\n";
        return 2;
    }
    if (ops > 1) {
        std::cerr << "vellum design record: choose one of record / --remove / --reconcile / --list\n";
        return 2;
    }

    // Load the current ledger. Absent or blank = empty (a first record
    // bootstraps cleanly). But a present-but-unparseable ledger is corrupt:
    // refuse to touch it rather than silently treat it as empty and overwrite
    // the recorded history on the next write.
    vellum::design::DesignLedger ledger;
    if (fs::exists(ledger_path)) {
        const std::string raw = read_text_file(ledger_path);
        const bool blank = raw.find_first_not_of(" \t\r\n") == std::string::npos;
        if (!blank) {
            bool valid = false;
            try {
                auto probe = choc::json::parse(raw);
                valid = probe.isObject();
            } catch (...) {
                // Catch-all, not catch(std::exception&): this CLI co-links Yoga
                // (-fno-rtti), which shadows std::exception's typeinfo so choc's
                // ParseError slips a typed catch. A bare catch keeps the corrupt-
                // ledger guard total instead of letting a parse throw escape.
                valid = false;
            }
            if (!valid) {
                std::cerr << "Error: ledger " << ledger_path.string()
                          << " is not valid JSON; refusing to touch it. "
                             "Fix or delete it first.\n";
                return 1;
            }
        }
        ledger = vellum::design::parse_ledger(raw);
    }

    if (do_list) {
        if (as_json) {
            std::cout << vellum::design::ledger_to_json(ledger) << "\n";
        } else if (ledger.assets.empty()) {
            std::cout << "(empty ledger: " << ledger_path.string() << ")\n";
        } else {
            for (const auto& x : ledger.assets) {
                std::cout << x.name << "@" << x.version << "  [" << vellum::design::review_status_name(x.status)
                          << "]  " << x.path;
                if (!x.source.empty()) std::cout << "  src=" << x.source;
                if (!x.inherit_from.empty()) std::cout << "  <-" << x.inherit_from;
                std::cout << "\n";
            }
        }
        return 0;
    }

    if (do_reconcile) {
        // Asset paths are recorded relative to the project (the ledger's
        // directory), not the process cwd: resolve them against the ledger dir
        // so `--reconcile` from any cwd does not wrongly delete valid entries.
        const fs::path ledger_dir = ledger_path.has_parent_path() ? ledger_path.parent_path()
                                                                  : fs::path(".");
        auto removed = vellum::design::reconcile(ledger, [&](const std::string& p) {
            fs::path fp(p);
            if (fp.is_relative()) fp = ledger_dir / fp;
            std::error_code ec;
            return fs::exists(fp, ec);
        });
        if (!write_text_file(ledger_path, vellum::design::ledger_to_json(ledger))) {
            std::cerr << "Error: cannot write " << ledger_path.string() << "\n";
            return 1;
        }
        std::cout << "Reconciled: dropped " << removed.size() << " missing asset(s).\n";
        for (const auto& s : removed) std::cout << "  - " << s << "\n";
        return 0;
    }

    if (have_remove) {
        auto removed = vellum::design::remove_asset(ledger, remove_selector);
        if (removed.empty()) {
            std::cerr << "vellum design record: no asset matched '" << remove_selector << "'\n";
            return 1;
        }
        if (!write_text_file(ledger_path, vellum::design::ledger_to_json(ledger))) {
            std::cerr << "Error: cannot write " << ledger_path.string() << "\n";
            return 1;
        }
        std::cout << "Removed " << removed.size() << " asset(s).\n";
        for (const auto& s : removed) std::cout << "  - " << s << "\n";
        return 0;
    }

    // Record operation.
    if (name.empty()) {
        std::cerr << "vellum design record: --name is required to record\n";
        return 2;
    }
    if (!status_str.empty() && !vellum::design::review_status_from_name(status_str)) {
        std::cerr << "vellum design record: invalid --status '" << status_str
                  << "' (needs-review | approved | changes-requested)\n";
        return 2;
    }

    // When --version targets an EXISTING revision, this is an update: start from
    // the stored entry and overwrite only the fields the caller actually passed,
    // so a status-only change (e.g. approve v1) never wipes provenance. A fresh
    // revision (no --version, or a version not yet present) requires --asset.
    const vellum::design::LedgerAsset* existing = nullptr;
    if (!version.empty()) {
        for (const auto& a : ledger.assets)
            if (a.name == name && a.version == version) { existing = &a; break; }
    }
    if (!existing && asset.empty()) {
        std::cerr << "vellum design record: --asset is required for a new asset revision\n";
        return 2;
    }

    vellum::design::LedgerAsset incoming = existing ? *existing : vellum::design::LedgerAsset{};
    incoming.name = name;
    incoming.version = version;
    if (!asset.empty()) incoming.path = asset;
    if (!inherit_from.empty()) incoming.inherit_from = inherit_from;
    if (!source.empty()) incoming.source = source;
    if (!viewport.empty()) incoming.viewport = viewport;
    if (!systems.empty()) incoming.design_systems = systems;
    if (!status_str.empty()) incoming.status = *vellum::design::review_status_from_name(status_str);

    const auto& stored = vellum::design::upsert_asset(ledger, incoming);
    const auto stored_status = stored.status;
    const std::string stored_slug = stored.name + "@" + stored.version;
    if (!write_text_file(ledger_path, vellum::design::ledger_to_json(ledger))) {
        std::cerr << "Error: cannot write " << ledger_path.string() << "\n";
        return 1;
    }
    std::cout << "Recorded " << stored_slug << " [" << vellum::design::review_status_name(stored_status)
              << "] -> " << ledger_path.string() << "\n";
    return 0;
}
void print_export_tokens_usage() {
    std::cout <<
        "Usage: vellum design export-tokens [--file <theme.json>] [--format <name>]\n"
        "                                   [--tokens <path>] [--dry-run]\n"
        "\n"
        "Export a Theme (from --file, or the built-in dark theme) as design tokens.\n"
        "\n"
        "  --format <name>   w3c (default) or css-variables\n"
        "  --tokens <path>   Output file (default: tokens.json, or theme.css for\n"
        "                    css-variables)\n"
        "  --dry-run         Print the tokens instead of writing a file\n";
}

int run_export_tokens(const std::vector<std::string>& rest) {
    fs::path input;
    fs::path tokens_file;
    std::string format = "w3c";
    bool dry_run = false;
    for (size_t i = 0; i < rest.size(); ++i) {
        const std::string& a = rest[i];
        auto next = [&](const char* flag) -> std::string {
            if (i + 1 >= rest.size()) {
                std::cerr << "vellum design export-tokens: " << flag << " requires a value\n";
                return {};
            }
            return rest[++i];
        };
        if (a == "-h" || a == "--help") { print_export_tokens_usage(); return 0; }
        else if (a == "--file") { auto v = next("--file"); if (v.empty()) return 2; input = v; }
        else if (a == "--format") { format = next("--format"); if (format.empty()) return 2; }
        else if (a == "--tokens") { auto v = next("--tokens"); if (v.empty()) return 2; tokens_file = v; }
        else if (a == "--dry-run") { dry_run = true; }
        else {
            std::cerr << "vellum design export-tokens: unknown argument '" << a << "'\n";
            return 2;
        }
    }
    // Tailwind formats need DESIGN.md section context that a flat theme does
    // not carry, so they are refused rather than emitted as W3C.
    if (format == "tailwind" || format == "json-tailwind" || format == "css-tailwind") {
        std::cerr << "vellum design export-tokens: --format " << format
                  << " requires DESIGN.md section context; export-tokens supports w3c and "
                     "css-variables only\n";
        return 2;
    }
    if (format != "w3c" && format != "css-variables") {
        std::cerr << "vellum design export-tokens: unknown --format '" << format
                  << "' (expected w3c or css-variables)\n";
        return 2;
    }
    if (tokens_file.empty()) tokens_file = format == "css-variables" ? "theme.css" : "tokens.json";

    vellum::view::Theme theme;
    if (input.empty()) {
        theme = vellum::view::Theme::dark();
    } else {
        auto content = read_text_file(input);
        if (content.empty()) {
            std::cerr << "Error: cannot read " << input << "\n";
            return 1;
        }
        theme = vellum::view::Theme::from_json(content);
    }
    const std::string body = format == "css-variables"
                                 ? vellum::view::export_css_variables(theme)
                                 : vellum::view::export_w3c_tokens(theme);
    if (dry_run) {
        std::cout << body;
        return 0;
    }
    if (!write_text_file(tokens_file, body)) {
        std::cerr << "Error: cannot write " << tokens_file << "\n";
        return 1;
    }
    std::cout << "Exported "
              << (theme.colors.size() + theme.dimensions.size() + theme.strings.size())
              << " tokens -> " << tokens_file.string() << " (format=" << format << ")\n";
    return 0;
}

}  // namespace vellum::design_tool
