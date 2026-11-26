// Copy the assets a design references next to an emitted artifact.
//
// Absolute asset paths are copied into <output dir>/assets/ under their
// content-hash names, verified by SHA-256, and rewritten to the portable
// relative path the generated code embeds. Relative paths are left as they are.

#include "design_emit_assets.hpp"

#include <vellum/runtime/crypto.hpp>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <string>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace vellum::authoring {

namespace fs = std::filesystem;
using vellum::view::IRNode;

bool localize_ir_assets(
    vellum::view::DesignIR& ir,
    const std::string& output_file,
    std::string* error) {
    if (output_file.empty()) return true;

    fs::path out_dir = fs::path(output_file).parent_path();
    std::error_code ec;
    if (out_dir.empty()) out_dir = fs::current_path(ec);
    const fs::path assets_dir = out_dir / "assets";

    std::unordered_map<std::string, std::string> rel_by_source;   // abs source → assets/<file>
    std::unordered_map<std::string, std::string> source_by_name;  // <file> → abs source
    bool dir_ready = false;
    bool succeeded = true;

    auto fail = [&](std::string message) {
        succeeded = false;
        if (error && error->empty()) *error = std::move(message);
    };

    auto file_sha256 = [](const fs::path& path) -> std::string {
        std::ifstream stream(path, std::ios::binary);
        if (!stream) return {};
        const std::string bytes(
            (std::istreambuf_iterator<char>(stream)),
            std::istreambuf_iterator<char>());
        return vellum::runtime::sha256_hex(bytes);
    };

    auto localize = [&](std::string& path_ref,
                        std::string_view content_hash = {}) {
        if (path_ref.empty()) return;
        const fs::path src(path_ref);
        // Already-relative references (an envelope that lives in the output
        // dir, or a re-run over localized IR) are portable as-is.
        if (src.is_relative()) return;
        if (auto hit = rel_by_source.find(path_ref); hit != rel_by_source.end()) {
            path_ref = hit->second;
            return;
        }
        std::error_code lec;
        if (!fs::is_regular_file(src, lec) || lec) {
            fail("referenced asset is missing: " + src.string());
            return;
        }

        // Asset filenames are content hashes in the export lanes, so clashes
        // only happen for genuinely different files; suffix those.
        std::string name = content_hash.size() == 64
            ? std::string(content_hash) + src.extension().string()
            : src.filename().string();
        for (int n = 2;; ++n) {
            auto used = source_by_name.find(name);
            if (used == source_by_name.end() || used->second == path_ref) break;
            name = src.stem().string() + "-" + std::to_string(n)
                 + src.extension().string();
        }

        if (!dir_ready) {
            fs::create_directories(assets_dir, lec);
            if (lec) {
                fail("could not create localized asset directory " +
                     assets_dir.string() + ": " + lec.message());
                return;
            }
            dir_ready = true;
        }
        const fs::path dst = assets_dir / name;
        const auto expected_hash = content_hash.size() == 64
            ? std::string(content_hash)
            : file_sha256(src);
        if (expected_hash.empty() || file_sha256(src) != expected_hash) {
            fail("source asset hash mismatch: " + src.string());
            return;
        }
        const bool destination_exists = fs::exists(dst, lec) && !lec;
        const bool same_file = destination_exists &&
                               fs::equivalent(src, dst, lec) && !lec;
        lec.clear();
        if (destination_exists && !same_file) {
            const auto actual = file_sha256(dst);
            if (actual != expected_hash) {
                fail("localized asset collision at " + dst.string());
                return;
            }
        }
        if (!same_file && !destination_exists) {
            const auto nonce = std::chrono::steady_clock::now()
                                   .time_since_epoch()
                                   .count();
            const fs::path staged =
                assets_dir / (name + ".tmp-" + std::to_string(nonce));
            fs::copy_file(src, staged, fs::copy_options::none, lec);
            if (!lec) fs::rename(staged, dst, lec);
            if (lec) {
                std::error_code cleanup_ec;
                fs::remove(staged, cleanup_ec);
                fail("could not localize asset " + src.string() + " to " +
                     dst.string() + ": " + lec.message());
                return;
            }
        }
        if (file_sha256(dst) != expected_hash) {
            fail("localized asset failed hash verification: " + dst.string());
            return;
        }
        // generic_string(): the relative path is baked into generated JS and
        // must use '/' on every platform (same reason as the resolve pass).
        std::string rel = (fs::path("assets") / name).generic_string();
        rel_by_source.emplace(path_ref, rel);
        source_by_name.emplace(std::move(name), path_ref);
        path_ref = rel;
    };

    for (auto& asset : ir.asset_manifest.assets) {
        if (!asset.local_path || asset.local_path->empty()) continue;
        auto localized = *asset.local_path;
        localize(localized, asset.content_hash);
        asset.local_path = std::move(localized);
    }
    std::function<void(IRNode&)> walk = [&](IRNode& n) {
        // Browser capture may split a fader into a cleaned body and a moving
        // indicator. Both are first-class publishable assets even though they
        // deliberately avoid the generic asset_path (which would make the
        // whole fader look like one indivisible image widget).
        for (const char* key : {"fader_body_asset_path",
                                "fader_indicator_asset_path"}) {
            if (auto it = n.attributes.find(key); it != n.attributes.end())
                localize(it->second);
        }
        if (auto it = n.attributes.find("asset_path"); it != n.attributes.end()) {
            localize(it->second);
        } else if (auto ref = n.attributes.find("asset_ref");
                   ref != n.attributes.end() && !ref->second.empty()) {
            // Some authoritative lanes (notably faithful browser capture)
            // intentionally carry only the source-agnostic asset_ref. Once the
            // manifest is localized, stamp the portable path directly rather
            // than asking a source-specific resolver to reinterpret it against
            // the original input directory.
            if (const auto* asset = ir.asset_manifest.resolve(ref->second);
                asset && asset->local_path && !asset->local_path->empty()) {
                n.attributes["asset_path"] = *asset->local_path;
            }
        }
        for (auto& alt : n.alternate_frames) walk(alt);
        for (auto& c : n.children) walk(c);
    };
    walk(ir.root);
    for (auto& fa : ir.font_family_assets) localize(fa.resolved_path);
    return succeeded;
}

}  // namespace vellum::authoring
