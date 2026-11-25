// Offline `vellum design` verbs over a design project: the card gallery
// inventory, the handoff contract, component variants and EDITMODE tweaks.
// Each verb reads and writes files and calls the design cores; none opens a
// window.

#include "design_tool_verbs.hpp"

#include <vellum/design/design_gallery.hpp>
#include <vellum/design/design_handoff.hpp>
#include <vellum/design/design_tweaks.hpp>
#include <vellum/design/design_variants.hpp>

#include <choc/text/choc_JSON.h>

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

void print_gallery_usage() {
    std::cout <<
        "Usage: vellum design gallery [--root <dir>] [--out <dir>] [options]\n"
        "\n"
        "  Render every @dsCard-tagged source file under <root> into one review\n"
        "  artifact: a grouped HTML grid (gallery.html) + a JSON manifest\n"
        "  (gallery.json). Tag a file with a leading magic comment, e.g.:\n"
        "      // @dsCard group=knobs viewport=120x140\n"
        "      // @startingPoint\n"
        "\n"
        "  --root <dir>        Tree to scan for tagged *.js files (default: cwd).\n"
        "  --out <dir>         Where to write the artifact (default: <root>/.pulp-gallery).\n"
        "  --no-render         Inventory only: emit the manifest/HTML with no PNGs.\n"
        "                      Card rendering is not available in this SDK yet.\n"
        "  --json              Print the manifest to stdout and skip rendering.\n";
}

// Read a bounded prefix of a file — enough to hold the leading magic-comment
// block without slurping a large source body just to check for a tag.
std::string read_file_head(const fs::path& p, size_t max_bytes = 1024) {
    std::ifstream f(p, std::ios::binary);
    if (!f.is_open()) return {};
    std::string head(max_bytes, '\0');
    f.read(head.data(), static_cast<std::streamsize>(max_bytes));
    head.resize(static_cast<size_t>(f.gcount()));
    return head;
}

// A filesystem-safe, collision-resistant PNG name for one card: its path with
// non-alphanumerics folded to '_', then the viewport and content hash. Because
// the hash is in the name, unchanged content at an unchanged viewport maps to an
// already-existing file — that is the content-hash render cache.
std::string card_png_name(const vellum::design::GalleryCard& c) {
    std::string slug;
    for (char ch : c.file) slug += std::isalnum(static_cast<unsigned char>(ch)) ? ch : '_';
    return slug + "_" + std::to_string(c.width) + "x" + std::to_string(c.height) + "_" +
           c.content_hash + ".png";
}

// Directories that never hold hand-authored cards and are expensive to walk.
bool is_skippable_dir(const std::string& name) {
    return name == ".git" || name == "build" || name == "node_modules" ||
           name == "external" || name == "planning" || name.rfind("build-", 0) == 0 ||
           name == ".pulp-gallery";
}

int run_gallery(const std::vector<std::string>& rest) {
    fs::path root, out_dir, screenshot_bin;
    std::string backend = "skia";
    bool no_render = false, json_only = false, force = false;

    for (size_t i = 0; i < rest.size(); ++i) {
        const std::string& a = rest[i];
        auto next = [&](const char* flag) -> std::string {
            if (i + 1 >= rest.size()) {
                std::cerr << "vellum design gallery: " << flag << " requires a value\n";
                return {};
            }
            return rest[++i];
        };
        if (a == "--help" || a == "-h") { print_gallery_usage(); return 0; }
        else if (a == "--root") root = next("--root");
        else if (a == "--out") out_dir = next("--out");
        else if (a == "--backend") backend = next("--backend");
        else if (a == "--screenshot") screenshot_bin = next("--screenshot");
        else if (a == "--no-render") no_render = true;
        else if (a == "--json") json_only = true;
        else if (a == "--force") force = true;
        else { std::cerr << "vellum design gallery: unknown option '" << a << "'\n"; return 2; }
    }

    std::error_code ec;
    if (root.empty()) root = fs::current_path();
    if (!fs::is_directory(root, ec)) {
        std::cerr << "Error: --root " << root << " is not a directory\n";
        return 1;
    }
    if (out_dir.empty()) out_dir = root / ".pulp-gallery";

    // Discover tagged cards. Walk the tree, skip noise dirs, parse each *.js head.
    // Status queries use the error_code overloads so a broken symlink or a status
    // race skips the entry instead of aborting the CLI.
    std::vector<vellum::design::GalleryCard> cards;
    auto it = fs::recursive_directory_iterator(
        root, fs::directory_options::skip_permission_denied, ec);
    const fs::recursive_directory_iterator end;
    for (; !ec && it != end; it.increment(ec)) {
        std::error_code sec;
        if (it->is_directory(sec) && is_skippable_dir(it->path().filename().string())) {
            it.disable_recursion_pending();
            continue;
        }
        if (sec || !it->is_regular_file(sec) || sec || it->path().extension() != ".js") continue;
        auto card = vellum::design::parse_gallery_card(read_file_head(it->path()),
                                                     fs::relative(it->path(), root).generic_string());
        if (!card) continue;
        card->content_hash = vellum::design::gallery_content_hash(read_text_file(it->path()));
        cards.push_back(std::move(*card));
    }
    if (ec) {
        // A traversal error mid-walk would otherwise emit a silently-partial
        // gallery as success. Fail loudly instead.
        std::cerr << "Error: scanning " << root << " failed: " << ec.message() << "\n";
        return 1;
    }
    vellum::design::sort_cards(cards);

    // Records the rendered (or cached) PNG path for each card, relative to out_dir.
    std::map<std::string, std::string> png_by_file;
    auto png_rel = [&](const vellum::design::GalleryCard& c) -> std::string {
        auto found = png_by_file.find(c.file);
        return found == png_by_file.end() ? std::string{} : found->second;
    };

    if (json_only) {
        // Fast inventory: manifest to stdout, no render, no files written.
        std::cout << vellum::design::gallery_manifest_json(cards, png_rel) << "\n";
        return 0;
    }

    fs::create_directories(out_dir, ec);
    fs::path cards_dir = out_dir / "cards";

    int rendered = 0, cached = 0, failed = 0;
    if (!no_render && !cards.empty()) {
        // Card rendering needs an offscreen capture tool, which this SDK does
        // not ship yet. Refuse rather than emit a gallery without its PNGs.
        (void)force;
        (void)backend;
        (void)screenshot_bin;
        (void)cards_dir;
        std::cerr << "vellum design gallery: card rendering is not available in this SDK; "
                     "pass --no-render or --json.\n";
        return 2;
    }

    if (!write_text_file(out_dir / "gallery.json",
                         vellum::design::gallery_manifest_json(cards, png_rel)) ||
        !write_text_file(out_dir / "gallery.html",
                         vellum::design::gallery_html(cards, png_rel))) {
        std::cerr << "Error: cannot write gallery artifact under " << out_dir << "\n";
        return 1;
    }

    std::cout << "Gallery: " << cards.size() << " card(s)";
    if (!no_render) std::cout << " (" << rendered << " rendered, " << cached << " cached, "
                              << failed << " failed)";
    std::cout << " -> " << (out_dir / "gallery.html").string() << "\n";
    return failed > 0 ? 1 : 0;
}

void print_handoff_usage() {
    std::cout <<
        "Usage: vellum design handoff <project-dir|README.md> [--json]\n"
        "\n"
        "  Parse a design-tool project's handoff contract into machine-stated\n"
        "  intent: the explicit hi-fi vs lo-fi fidelity declaration, bound design\n"
        "  systems, per-screen exact-value specs, the token table, and interaction\n"
        "  notes. Given a directory, reads its handoff README and folds in the\n"
        "  `_ds/<slug>/` design-system directories.\n"
        "\n"
        "  --json              Print the contract JSON instead of a summary.\n";
}

// The handoff README at a project root, trying the common names in order.
fs::path find_handoff_readme(const fs::path& dir) {
    for (const char* name : {"HANDOFF.md", "handoff.md", "README.md", "readme.md"}) {
        std::error_code ec;
        fs::path p = dir / name;
        if (fs::is_regular_file(p, ec)) return p;
    }
    return {};
}

// The `_ds/<slug>/` design-system directory names under a project root.
std::vector<std::string> design_system_slugs(const fs::path& dir) {
    std::vector<std::string> slugs;
    std::error_code ec;
    fs::path ds = dir / "_ds";
    if (!fs::is_directory(ds, ec)) return slugs;
    for (auto it = fs::directory_iterator(ds, ec); !ec && it != fs::directory_iterator();
         it.increment(ec)) {
        std::error_code sec;
        if (it->is_directory(sec) && !sec)  // per-entry error skips that entry, not the walk
            slugs.push_back(it->path().filename().string());
    }
    std::sort(slugs.begin(), slugs.end());
    return slugs;
}

int run_handoff(const std::vector<std::string>& rest) {
    fs::path input;
    bool json_out = false;
    for (const auto& a : rest) {
        if (a == "--help" || a == "-h") { print_handoff_usage(); return 0; }
        else if (a == "--json") json_out = true;
        else if (!a.empty() && a[0] == '-') {
            std::cerr << "vellum design handoff: unknown option '" << a << "'\n";
            return 2;
        } else if (input.empty()) {
            input = a;
        } else {
            std::cerr << "vellum design handoff: unexpected extra argument '" << a << "'\n";
            return 2;
        }
    }
    if (input.empty()) { print_handoff_usage(); return 2; }

    std::error_code ec;
    fs::path readme = input;
    std::vector<std::string> slugs;
    if (fs::is_directory(input, ec)) {
        readme = find_handoff_readme(input);
        if (readme.empty()) {
            std::cerr << "Error: no handoff README (HANDOFF.md / README.md) in " << input << "\n";
            return 1;
        }
        slugs = design_system_slugs(input);
    } else if (!fs::is_regular_file(input, ec)) {
        std::cerr << "Error: " << input << " is not a file or directory\n";
        return 1;
    }

    auto text = read_text_file(readme);
    if (text.empty()) { std::cerr << "Error: cannot read " << readme << "\n"; return 1; }

    auto contract = vellum::design::parse_handoff_readme(text, readme.generic_string());
    vellum::design::merge_design_systems(contract, slugs);

    if (json_out) {
        std::cout << vellum::design::handoff_contract_json(contract) << "\n";
        return 0;
    }

    std::cout << "Handoff: " << readme.filename().string() << "\n";
    std::cout << "  fidelity: " << vellum::design::fidelity_intent_name(contract.fidelity) << "\n";
    std::cout << "  design systems: ";
    if (contract.design_systems.empty()) std::cout << "(none declared)";
    for (size_t i = 0; i < contract.design_systems.size(); ++i)
        std::cout << (i ? ", " : "") << contract.design_systems[i];
    std::cout << "\n  screens: " << contract.screens.size()
              << ", tokens: " << contract.tokens.size()
              << ", interactions: " << contract.interactions.size() << "\n";
    if (contract.fidelity == vellum::design::FidelityIntent::unspecified)
        std::cout << "  note: no fidelity declared — confirm hi-fi vs lo-fi before importing.\n";
    return 0;
}

void print_variants_usage() {
    std::cout <<
        "Usage: vellum design variants --component <name> <variants-file> [--json]\n"
        "\n"
        "  Collapse a component set's `Prop=Value` variant names into one typed\n"
        "  component contract: each property with its value enum and a chosen\n"
        "  default. Feeds the design manifest (vellum design compile) and the\n"
        "  adherence lint. <variants-file> is one variant name per line; blank\n"
        "  lines and lines starting with '#' are ignored.\n"
        "\n"
        "  --component <name>  Component-set name (required).\n"
        "  --json              Print the contract JSON instead of a summary.\n";
}

int run_variants(const std::vector<std::string>& rest) {
    std::string component;
    fs::path variants_file;
    bool json_out = false;
    for (size_t i = 0; i < rest.size(); ++i) {
        const std::string& a = rest[i];
        auto next = [&](const char* flag) -> std::string {
            if (i + 1 >= rest.size()) {
                std::cerr << "vellum design variants: " << flag << " requires a value\n";
                return {};
            }
            return rest[++i];
        };
        if (a == "--help" || a == "-h") { print_variants_usage(); return 0; }
        else if (a == "--component") component = next("--component");
        else if (a == "--json") json_out = true;
        else if (!a.empty() && a[0] == '-') {
            std::cerr << "vellum design variants: unknown option '" << a << "'\n";
            return 2;
        } else if (variants_file.empty()) {
            variants_file = a;
        } else {
            std::cerr << "vellum design variants: unexpected extra argument '" << a << "'\n";
            return 2;
        }
    }
    if (component.empty()) {
        std::cerr << "vellum design variants: --component <name> is required\n";
        return 2;
    }
    if (variants_file.empty()) { print_variants_usage(); return 2; }
    std::error_code ec;
    if (!fs::is_regular_file(variants_file, ec)) {
        std::cerr << "Error: cannot read variants file " << variants_file << "\n";
        return 1;
    }

    std::vector<std::string> names;
    {
        std::ifstream f(variants_file);
        if (!f) {
            std::cerr << "Error: cannot open variants file " << variants_file << "\n";
            return 1;
        }
        std::string line;
        while (std::getline(f, line)) {
            // Trim trailing CR (CRLF files) and skip blanks / comments.
            while (!line.empty() && (line.back() == '\r' || line.back() == ' ' || line.back() == '\t'))
                line.pop_back();
            size_t b = line.find_first_not_of(" \t");
            if (b == std::string::npos || line[b] == '#') continue;
            names.push_back(line.substr(b));
        }
    }
    if (names.empty()) {
        std::cerr << "Error: no variant names in " << variants_file << "\n";
        return 1;
    }

    auto contract = vellum::design::build_component_contract(component, names);
    if (json_out) {
        std::cout << vellum::design::component_contract_json(contract) << "\n";
        return 0;
    }
    std::cout << "Component: " << contract.component << " (" << contract.variant_count
              << " variants)\n";
    for (const auto& p : contract.props) {
        std::cout << "  " << p.name << " = { ";
        for (size_t i = 0; i < p.values.size(); ++i)
            std::cout << (i ? ", " : "") << p.values[i]
                      << (p.values[i] == p.default_value ? "*" : "");
        std::cout << " }\n";
    }
    if (!contract.issues.empty()) {
        std::cout << "  issues:\n";
        for (const auto& is : contract.issues)
            std::cout << "    [" << is.kind << "] " << is.detail << "\n";
    }
    return 0;
}

void print_tweak_usage() {
    std::cout <<
        "Usage: vellum design tweak <file> [--set key=value ...] [options]\n"
        "\n"
        "  Read or update the EDITMODE parameter block an imported design\n"
        "  carries. The block is the artifact's own parameter store:\n"
        "      /*EDITMODE-BEGIN*/{\"accent\":\"#33aaff\",\"radius\":8}/*EDITMODE-END*/\n"
        "  With no --set, the current parameters are listed. Each --set updates\n"
        "  one key in place (or appends it), rewriting only the bytes between the\n"
        "  markers so the change survives a reload and shows in the diff.\n"
        "\n"
        "  <file>              The artifact to read/rewrite (e.g. ui.js).\n"
        "  --set key=value     Set one parameter. `value` is a JSON number / true\n"
        "                      / false / null literal (8, true); anything else is\n"
        "                      stored as a JSON string (#33aaff, Hello). Repeatable.\n"
        "  --out <path>        Write the rewritten artifact here (default: in place).\n"
        "  --json              Print the parameters as a JSON object and exit.\n";
}

// Is `s` exactly a JSON number literal (no leading/trailing junk)? A strict
// grammar check, so a value like `1],x:[2` is NOT a number and gets quoted as a
// string rather than smuggling structure into the block.
bool is_json_number(const std::string& s) {
    size_t i = 0, n = s.size();
    if (n == 0) return false;
    if (s[i] == '-') ++i;
    if (i >= n) return false;
    if (s[i] == '0') {
        ++i;  // a leading zero may not be followed by more digits
    } else if (std::isdigit(static_cast<unsigned char>(s[i]))) {
        while (i < n && std::isdigit(static_cast<unsigned char>(s[i]))) ++i;
    } else {
        return false;
    }
    if (i < n && s[i] == '.') {  // fraction
        ++i;
        if (i >= n || !std::isdigit(static_cast<unsigned char>(s[i]))) return false;
        while (i < n && std::isdigit(static_cast<unsigned char>(s[i]))) ++i;
    }
    if (i < n && (s[i] == 'e' || s[i] == 'E')) {  // exponent
        ++i;
        if (i < n && (s[i] == '+' || s[i] == '-')) ++i;
        if (i >= n || !std::isdigit(static_cast<unsigned char>(s[i]))) return false;
        while (i < n && std::isdigit(static_cast<unsigned char>(s[i]))) ++i;
    }
    return i == n;
}

// Coerce a CLI-supplied value into JSON text. A JSON number / true / false /
// null literal passes through verbatim; everything else is quoted as a JSON
// string, so `--set radius=8` stores a number while `--set accent=#33aaff` and
// `--set label=Hello` store strings. Values are never parsed as arrays/objects,
// which keeps a crafted value from smuggling extra JSON into the block. Returns
// nullopt for non-UTF-8 input: choc's JSON writer over-reads on a truncated
// multibyte sequence (its bounds assert is compiled out in Release).
std::optional<std::string> cli_value_to_json(const std::string& raw) {
    if (choc::text::findInvalidUTF8Data(raw.data(), raw.size()) != nullptr) return std::nullopt;
    if (raw == "true" || raw == "false" || raw == "null") return raw;
    if (is_json_number(raw)) return raw;
    return choc::json::toString(choc::value::createString(raw));
}

int run_tweak(const std::vector<std::string>& rest) {
    fs::path file, out;
    bool json_out = false;
    bool arg_error = false;
    std::vector<std::pair<std::string, std::string>> sets;  // key -> json value
    for (size_t i = 0; i < rest.size(); ++i) {
        const std::string& a = rest[i];
        auto next = [&](const char* flag) -> std::string {
            if (i + 1 >= rest.size()) {
                std::cerr << "vellum design tweak: " << flag << " requires a value\n";
                arg_error = true;
                return {};
            }
            return rest[++i];
        };
        if (a == "--help" || a == "-h") { print_tweak_usage(); return 0; }
        else if (a == "--json") json_out = true;
        else if (a == "--out") { out = next("--out"); if (arg_error) return 2; }
        else if (a == "--set") {
            std::string kv = next("--set");
            if (arg_error) return 2;
            auto eq = kv.find('=');
            if (eq == std::string::npos || eq == 0) {
                std::cerr << "vellum design tweak: --set expects key=value, got '" << kv << "'\n";
                return 2;
            }
            std::string key = kv.substr(0, eq);
            if (choc::text::findInvalidUTF8Data(key.data(), key.size()) != nullptr) {
                std::cerr << "vellum design tweak: --set key is not valid UTF-8\n";
                return 2;
            }
            auto val = cli_value_to_json(kv.substr(eq + 1));
            if (!val) {
                std::cerr << "vellum design tweak: --set value for '" << key
                          << "' is not valid UTF-8\n";
                return 2;
            }
            sets.emplace_back(std::move(key), *val);
        } else if (!a.empty() && a[0] == '-') {
            std::cerr << "vellum design tweak: unknown option '" << a << "'\n";
            return 2;
        } else if (file.empty()) {
            file = a;
        } else {
            std::cerr << "vellum design tweak: unexpected extra argument '" << a << "'\n";
            return 2;
        }
    }
    if (file.empty()) { print_tweak_usage(); return 2; }
    std::error_code ec;
    if (!fs::is_regular_file(file, ec)) {
        std::cerr << "Error: cannot read " << file << "\n";
        return 1;
    }

    std::string text;
    {
        std::ifstream f(file, std::ios::binary);
        if (!f) {
            std::cerr << "Error: cannot open " << file << "\n";
            return 1;
        }
        std::ostringstream ss;
        ss << f.rdbuf();
        text = ss.str();
    }

    auto params = vellum::design::read_edit_block(text);
    if (!params) {
        std::cerr << "Error: no well-formed EDITMODE block in " << file << "\n";
        return 1;
    }

    if (sets.empty()) {
        if (json_out) {
            auto payload = vellum::design::edit_block_payload(*params);
            if (!payload) {
                std::cerr << "Error: the block holds a value that is not valid JSON\n";
                return 1;
            }
            std::cout << *payload << "\n";
        } else {
            std::cout << "Parameters in " << file.filename().string() << ":\n";
            for (const auto& p : *params)
                std::cout << "  " << p.key << " = " << p.json_value << "\n";
        }
        return 0;
    }

    // Apply the sets in order, then rewrite once.
    for (const auto& [key, val] : sets) {
        bool found = false;
        for (auto& p : *params) {
            if (p.key == key) { p.json_value = val; found = true; break; }
        }
        if (!found) params->push_back({key, val});
    }
    auto rewritten = vellum::design::rewrite_edit_block(text, *params);
    if (!rewritten) {
        std::cerr << "Error: could not rewrite the EDITMODE block in " << file << "\n";
        return 1;
    }
    if (!rewritten->outside_bytes_intact) {
        // A rewrite must never disturb bytes outside the block; refuse to write.
        std::cerr << "Error: refusing to write — bytes outside the block changed\n";
        return 1;
    }
    fs::path dest = out.empty() ? file : out;
    // Write atomically: a full write to a sibling temp file, verified, then a
    // rename over the destination — so a crash or a full disk mid-write leaves
    // the original artifact intact rather than truncated.
    fs::path tmp = dest;
    tmp += ".vellum-tweak.tmp";
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) {
            std::cerr << "Error: cannot write " << dest << "\n";
            return 1;
        }
        f << rewritten->text;
        f.flush();
        if (!f.good()) {
            std::cerr << "Error: failed writing " << dest << "\n";
            f.close();
            std::error_code rm;
            fs::remove(tmp, rm);
            return 1;
        }
    }
    std::error_code rn;
    fs::rename(tmp, dest, rn);
    if (rn) {
        std::cerr << "Error: cannot replace " << dest << ": " << rn.message() << "\n";
        std::error_code rm;
        fs::remove(tmp, rm);
        return 1;
    }
    std::cout << "Updated " << sets.size() << " parameter(s) -> " << dest.string() << "\n";
    return 0;
}

}  // namespace vellum::design_tool
