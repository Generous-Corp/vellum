// Copy the assets a design references next to an emitted artifact.
//
// Absolute asset paths are copied into <output dir>/assets/ under their
// content-hash names, verified by SHA-256, and rewritten to the portable
// relative path the generated code embeds. Relative inputs resolve beside the design.

#include "design_emit_assets.hpp"
#include "design_ir_helpers.hpp"

#include <vellum/runtime/crypto.hpp>
#include <vellum/runtime/base64.hpp>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iterator>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <utility>

namespace vellum::authoring {

namespace fs = std::filesystem;
using vellum::view::IRNode;

bool localize_ir_assets(
    vellum::view::DesignIR& ir,
    const std::string& output_file,
    std::string* error,
    const fs::path& input_directory,
    bool dry_run) {
    if (output_file.empty()) return true;

    fs::path out_dir = fs::path(output_file).parent_path();
    std::error_code ec;
    if (out_dir.empty()) out_dir = fs::current_path(ec);
    const fs::path assets_dir = out_dir / "assets";

    std::unordered_map<std::string, std::string> rel_by_source;   // abs source → assets/<file>
    std::unordered_map<std::string, std::string> hash_by_source;
    std::unordered_map<std::string, std::string> source_by_name;  // <file> → abs source
    struct Copy { fs::path source; fs::path destination; std::string hash; };
    std::vector<Copy> copies;
    std::unordered_set<std::string> svg_ids;
    std::function<void(const IRNode&)> collect_svg = [&](const IRNode& node) {
        if (node.svg_asset_id) svg_ids.insert(*node.svg_asset_id);
        for (const auto& alt : node.alternate_frames) collect_svg(alt);
        for (const auto& child : node.children) collect_svg(child);
    };
    collect_svg(ir.root);
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
        if (stream.bad()) return {};
        return vellum::runtime::sha256_hex(bytes);
    };

    auto localize = [&](std::string& path_ref,
                        std::string_view content_hash = {},
                        fs::path* validated_source = nullptr) {
        if (path_ref.empty()) return;
        fs::path src(path_ref);
        if (src.is_relative()) src = input_directory / src;
        src = fs::absolute(src).lexically_normal();
        // Relative names may be resources supplied by the consumer
        // at runtime. Keep those deferred references without reading a file.
        std::error_code missing_ec;
        if (fs::path(path_ref).is_relative() &&
            vellum::view::asset_path_lexically_within(path_ref, input_directory) &&
            vellum::view::asset_path_within(src, input_directory) &&
            !fs::exists(src, missing_ec) && !missing_ec)
            return;
        vellum::view::IRAssetRef reference;
        reference.content_hash = std::string(content_hash);
        // Node and font references may reuse a manifest-verified cache asset.
        if (reference.content_hash.empty()) {
            if (auto hash = hash_by_source.find(src.string()); hash != hash_by_source.end())
                reference.content_hash = hash->second;
        }
        const auto contained = vellum::view::contained_asset_file(
            reference, path_ref, input_directory, "asset_path");
        if (!contained) {
            fail("referenced asset is missing or outside the document directory: " + path_ref);
            return;
        }
        src = *contained;
        std::error_code lec;
        const auto actual_hash = file_sha256(src);
        if (actual_hash.empty() || (!reference.content_hash.empty() &&
            !vellum::view::asset_file_matches_declared_sha256(reference, src))) {
            fail("source asset hash mismatch: " + src.string());
            return;
        }
        if (validated_source) *validated_source = src;
        // Every manifest occurrence must validate its own hash, including cache hits.
        if (auto hit = rel_by_source.find(src.string()); hit != rel_by_source.end()) {
            path_ref = hit->second;
            return;
        }
        // Asset filenames are content hashes in the export lanes, so clashes
        // only happen for genuinely different files; suffix those.
        std::string name = !content_hash.empty()
            ? actual_hash + src.extension().string()
            : src.filename().string();
        for (int n = 2;; ++n) {
            auto used = source_by_name.find(name);
            if (used == source_by_name.end() || used->second == src.string()) break;
            name = src.stem().string() + "-" + std::to_string(n)
                 + src.extension().string();
        }

        const fs::path dst = assets_dir / name;
        const auto& expected_hash = actual_hash;
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
        if (!same_file && !destination_exists) copies.push_back({src, dst, expected_hash});
        // generic_string(): the relative path is baked into generated JS and
        // must use '/' on every platform (same reason as the resolve pass).
        std::string rel = (fs::path("assets") / name).generic_string();
        rel_by_source.emplace(src.string(), rel);
        hash_by_source.emplace(src.string(), actual_hash);
        source_by_name.emplace(std::move(name), src.string());
        path_ref = rel;
    };

    for (auto& asset : ir.asset_manifest.assets) {
        if (!asset.local_path || asset.local_path->empty()) {
            if (asset.original_uri.starts_with("file://")) {
                asset.local_path = asset.original_uri.substr(7);
            } else if (svg_ids.contains(asset.asset_id) && !asset.original_uri.starts_with("data:")) {
                const auto source = vellum::view::resolve_asset_file(asset, input_directory);
                if (!source) {
                    fail("cannot resolve SVG asset: " + asset.asset_id);
                    continue;
                }
                asset.local_path = source->string();
            } else {
                continue;
            }
        }
        auto localized = *asset.local_path;
        fs::path validated_source;
        localize(localized, asset.content_hash, &validated_source);
        // Faithful SVG lowering reads bytes during code generation. Preserve
        // them in its supported inline URI before replacing the source path.
        if (succeeded && svg_ids.contains(asset.asset_id) &&
            !asset.original_uri.starts_with("data:")) {
            std::ifstream in(validated_source, std::ios::binary);
            const std::string bytes((std::istreambuf_iterator<char>(in)), {});
            if (!in || in.bad() ||
                vellum::runtime::sha256_hex(bytes) != hash_by_source.at(validated_source.string()))
                fail("cannot read verified SVG asset: " + validated_source.string());
            else asset.original_uri = "data:image/svg+xml;base64," + vellum::runtime::base64_encode(bytes);
        }
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
    if (!succeeded || dry_run || copies.empty()) return succeeded;
    fs::create_directories(assets_dir, ec);
    if (ec) {
        fail("could not create localized asset directory " + assets_dir.string() + ": " + ec.message());
        return false;
    }
    for (const auto& copy : copies) {
        const auto& src = copy.source;
        const auto& dst = copy.destination;
        const auto& expected_hash = copy.hash;
        const auto name = dst.filename().string();
        std::error_code lec;
        const auto nonce = std::chrono::steady_clock::now().time_since_epoch().count();
        const fs::path staged = assets_dir / (name + ".tmp-" + std::to_string(nonce));
        fs::copy_file(src, staged, fs::copy_options::none, lec);
        if (!lec && file_sha256(staged) != expected_hash) {
            fs::remove(staged, lec);
            fail("localized asset failed hash verification: " + dst.string());
            return false;
        }
        if (!lec) fs::rename(staged, dst, lec);
        if (lec) {
            std::error_code cleanup_ec;
            fs::remove(staged, cleanup_ec);
            fail("could not localize asset " + src.string() + " to " + dst.string() + ": " + lec.message());
            return false;
        }
    }
    return true;
}

}  // namespace vellum::authoring
