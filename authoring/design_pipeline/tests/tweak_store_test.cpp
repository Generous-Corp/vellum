// TweakStore: mutation, bypass and lock overlays, JSON round trip, disk
// persistence and drift classification.

#include <vellum/authoring/tweak_store.hpp>

#include "mini_test.hpp"

#include <choc/text/choc_JSON.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

using vellum::authoring::TweakStore;

namespace {

// Each disk test gets its own scratch directory under the system temp root so
// the suite stays parallel-safe. The destructor removes the directory and any
// half-written .tmp files. PULP_TWEAKS_FILE is cleared on construction so
// default_tweaks_path() lookups stay deterministic; tests that need it set and
// unset it inside the test body.
struct TempTweaksDir {
    std::filesystem::path dir;
    std::filesystem::path file;

    TempTweaksDir() {
        // The counter keeps names unique when two directories land on the same
        // clock tick.
        static std::atomic<std::uint64_t> counter{0};
        const auto now = std::chrono::steady_clock::now().time_since_epoch().count();
        std::ostringstream name;
        name << "vellum-tweaks-test-" << now << "-" << counter.fetch_add(1);
        dir = std::filesystem::temp_directory_path() / name.str();
        std::filesystem::create_directories(dir);
        file = dir / "pulp-tweaks.json";
#ifdef _WIN32
        _putenv_s("PULP_TWEAKS_FILE", "");
#else
        ::unsetenv("PULP_TWEAKS_FILE");
#endif
    }

    ~TempTweaksDir() {
        std::error_code ec;
        std::filesystem::remove_all(dir, ec);
    }

    std::string read() const {
        std::ifstream in(file);
        std::ostringstream ss;
        ss << in.rdbuf();
        return ss.str();
    }

    void write(const std::string& s) const {
        std::ofstream out(file);
        out << s;
    }
};

} // namespace

TEST_CASE("TweakStore: apply_tweak records new entries", "[inspect][tweak-store]") {
    TweakStore s;
    REQUIRE(s.count() == 0);

    auto n = s.apply_tweak("anchor:a", "layout.padding",
                           choc::value::createInt32(12), "drag");
    REQUIRE(n == 1);
    REQUIRE(s.count() == 1);

    auto v = s.lookup("anchor:a", "layout.padding");
    REQUIRE(v.has_value());
    REQUIRE(v->getInt32() == 12);
}

TEST_CASE("TweakStore: apply_tweak overwrites the same anchor + path",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.apply_tweak("anchor:a", "paint.bg", choc::value::createString("#abc"), "color-picker");
    s.apply_tweak("anchor:a", "paint.bg", choc::value::createString("#def"), "color-picker");
    REQUIRE(s.count() == 1);
    REQUIRE(s.lookup("anchor:a", "paint.bg")->getString() == "#def");
}

TEST_CASE("TweakStore: multiple paths under one anchor coexist",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.apply_tweak("anchor:a", "layout.padding", choc::value::createInt32(12), {});
    s.apply_tweak("anchor:a", "paint.bg", choc::value::createString("#abc"), {});
    REQUIRE(s.count() == 2);
    auto recs = s.list_tweaks();
    REQUIRE(recs.size() == 2);
}

TEST_CASE("TweakStore: list_tweaks preserves insertion order",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.apply_tweak("anchor:a", "layout.padding", choc::value::createInt32(12), {});
    s.apply_tweak("anchor:a", "layout.gap", choc::value::createInt32(4), {});
    s.apply_tweak("anchor:b", "paint.opacity",
                  choc::value::createFloat32(0.5f), {});

    // Overwriting an existing entry should not move it to the end.
    s.apply_tweak("anchor:a", "layout.padding", choc::value::createInt32(16), {});

    auto recs = s.list_tweaks();
    REQUIRE(recs.size() == 3);
    REQUIRE(recs[0].anchor_id == "anchor:a");
    REQUIRE(recs[0].property_path == "layout.padding");
    REQUIRE(recs[0].value.getInt32() == 16);
    REQUIRE(recs[1].anchor_id == "anchor:a");
    REQUIRE(recs[1].property_path == "layout.gap");
    REQUIRE(recs[2].anchor_id == "anchor:b");
    REQUIRE(recs[2].property_path == "paint.opacity");
}

TEST_CASE("TweakStore: remove_tweak removes a single entry",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.apply_tweak("anchor:a", "layout.padding", choc::value::createInt32(12), {});
    s.apply_tweak("anchor:a", "paint.bg", choc::value::createString("#abc"), {});
    REQUIRE(s.remove_tweak("anchor:a", "layout.padding"));
    REQUIRE_FALSE(s.remove_tweak("anchor:a", "layout.padding"));
    REQUIRE(s.count() == 1);
    REQUIRE(s.lookup("anchor:a", "paint.bg").has_value());
}

TEST_CASE("TweakStore: remove_anchor wipes all paths under an anchor",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.apply_tweak("anchor:a", "layout.padding", choc::value::createInt32(12), {});
    s.apply_tweak("anchor:a", "paint.bg", choc::value::createString("#abc"), {});
    s.apply_tweak("anchor:b", "layout.gap", choc::value::createInt32(4), {});
    REQUIRE(s.remove_anchor("anchor:a") == 2);
    REQUIRE(s.count() == 1);
    REQUIRE(s.lookup("anchor:b", "layout.gap").has_value());
}

TEST_CASE("TweakStore: clear preserves locked anchors and clears unlocked state",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.apply_tweak("anchor:a", "layout.padding", choc::value::createInt32(12), {});
    s.apply_tweak("anchor:b", "layout.gap", choc::value::createInt32(4), {});
    s.set_bypass("anchor:a", true);
    s.set_bypass("anchor:b", true);
    s.set_locked("anchor:a", true);
    s.clear();
    REQUIRE(s.count() == 1);
    REQUIRE(s.lookup("anchor:a", "layout.padding").has_value());
    REQUIRE(s.bypass_for("anchor:a").has_value());
    REQUIRE(s.is_locked("anchor:a"));
    REQUIRE_FALSE(s.lookup("anchor:b", "layout.gap").has_value());
    REQUIRE_FALSE(s.bypass_for("anchor:b").has_value());
    REQUIRE_FALSE(s.is_locked("anchor:b"));
}

// ── Bypass overlay ──────────────────────────────────────────────────────

TEST_CASE("TweakStore: bypass=true bypasses every path under the anchor",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.set_bypass("anchor:a", true);
    REQUIRE(s.is_bypassed("anchor:a", "layout.padding"));
    REQUIRE(s.is_bypassed("anchor:a", "paint.bg"));
    REQUIRE_FALSE(s.is_bypassed("anchor:b", "layout.padding"));
}

TEST_CASE("TweakStore: bypass=string[] bypasses only listed paths",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.set_bypass("anchor:a", std::vector<std::string>{"layout.padding"});
    REQUIRE(s.is_bypassed("anchor:a", "layout.padding"));
    REQUIRE_FALSE(s.is_bypassed("anchor:a", "paint.bg"));
}

TEST_CASE("TweakStore: bypass=false clears the overlay",
          "[inspect][tweak-store]") {
    TweakStore s;
    s.set_bypass("anchor:a", true);
    REQUIRE(s.is_bypassed("anchor:a", "any.path"));
    s.set_bypass("anchor:a", false);
    REQUIRE_FALSE(s.is_bypassed("anchor:a", "any.path"));
    REQUIRE_FALSE(s.bypass_for("anchor:a").has_value());
}

TEST_CASE("TweakStore: set_locked marks an anchor protected",
          "[inspect][tweak-store][lock]") {
    TweakStore s;
    REQUIRE_FALSE(s.is_locked("anchor:a"));
    s.set_locked("anchor:a", true);
    REQUIRE(s.is_locked("anchor:a"));
    REQUIRE_FALSE(s.is_locked("anchor:b"));
}

TEST_CASE("TweakStore: set_locked(false) and clear_lock remove the lock",
          "[inspect][tweak-store][lock]") {
    TweakStore s;
    s.set_locked("anchor:a", true);
    s.set_locked("anchor:a", false);
    REQUIRE_FALSE(s.is_locked("anchor:a"));

    s.set_locked("anchor:b", true);
    s.clear_lock("anchor:b");
    REQUIRE_FALSE(s.is_locked("anchor:b"));
}

TEST_CASE("TweakStore: locked_anchors enumerates every locked anchor",
          "[inspect][tweak-store][lock]") {
    TweakStore s;
    REQUIRE(s.locked_anchors().empty());
    s.set_locked("anchor:a", true);
    s.set_locked("anchor:c", true);
    s.set_locked("anchor:b", true);
    auto locked = s.locked_anchors();
    std::sort(locked.begin(), locked.end());
    REQUIRE(locked == std::vector<std::string>{"anchor:a", "anchor:b", "anchor:c"});

    // Lock is independent of whether the anchor has tweak records ,
    // a lock-only anchor still enumerates.
    REQUIRE(s.count() == 0);
}

TEST_CASE("TweakStore: lock state is independent of bypass state",
          "[inspect][tweak-store][lock]") {
    TweakStore s;
    s.set_locked("anchor:a", true);
    REQUIRE_FALSE(s.is_bypassed("anchor:a", "any.path"));  // lock != bypass
    s.set_bypass("anchor:a", true);
    REQUIRE(s.is_locked("anchor:a"));                      // bypass != lock
    s.clear_bypass("anchor:a");
    REQUIRE(s.is_locked("anchor:a"));                      // still locked
}

TEST_CASE("TweakStore: lock state round-trips through disk",
          "[inspect][tweak-store][lock][disk]") {
    TempTweaksDir tmp;
    TweakStore s;
    s.apply_tweak("anchor:a", "layout.padding", choc::value::createInt32(8), "drag");
    s.apply_tweak("anchor:b", "paint.bg", choc::value::createString("#fff"), "picker");
    s.set_locked("anchor:a", true);
    // A lock-only anchor (no tweaks) must also persist.
    s.set_locked("anchor:lonely", true);

    auto saved = s.save_to_disk(tmp.file.string());
    REQUIRE(saved.ok);
    // The serialized file should carry a `locked` array.
    REQUIRE(tmp.read().find("\"locked\"") != std::string::npos);

    TweakStore t;
    auto loaded = t.load_from_disk(tmp.file.string());
    REQUIRE(loaded.ok);
    REQUIRE(t.is_locked("anchor:a"));
    REQUIRE(t.is_locked("anchor:lonely"));
    REQUIRE_FALSE(t.is_locked("anchor:b"));
    auto locked = t.locked_anchors();
    REQUIRE(locked.size() == 2);
}

TEST_CASE("TweakStore::diff classifies every tweak against an anchor set",
          "[inspect][tweak-store][drift]") {
    TweakStore store;
    store.apply_tweak("anchor-live-1", "paint.backgroundColor",
                      choc::value::createString("#ff0000"));
    store.apply_tweak("anchor-live-1", "layout.padding",
                      choc::value::createInt32(12));
    store.apply_tweak("anchor-gone-2", "layout.margin",
                      choc::value::createInt32(8), "inspector-drag-handle");

    // Only anchor-live-1 survives the re-import.
    auto report = store.diff(std::vector<std::string>{"anchor-live-1"});

    REQUIRE(report.total() == 3);
    REQUIRE(report.clean.size() == 2);
    REQUIRE(report.drifted.empty());          // anchor-only matching
    REQUIRE(report.orphaned.size() == 1);
    REQUIRE(report.has_drift());

    const auto& orphan = report.orphaned.front();
    REQUIRE(orphan.anchor_id == "anchor-gone-2");
    REQUIRE(orphan.property_path == "layout.margin");
    REQUIRE(orphan.source == "inspector-drag-handle");
    REQUIRE(orphan.reason == TweakStore::DriftReason::anchor_not_found);
}

TEST_CASE("TweakStore::diff detects property-level drift via DesignSnapshot",
          "[inspect][tweak-store][drift]") {
    TweakStore store;
    store.apply_tweak("card", "paint.backgroundColor",
                      choc::value::createString("#222"));
    store.apply_tweak("card", "layout.padding",
                      choc::value::createInt32(16));

    // The design still has the `card` anchor, but the re-import removed
    // the padding field, only backgroundColor remains exposed.
    TweakStore::DesignSnapshot snap;
    snap.anchors.insert("card");
    snap.properties["card"] = {"paint.backgroundColor"};

    auto report = store.diff(snap);
    REQUIRE(report.clean.size() == 1);
    REQUIRE(report.clean.front().property_path == "paint.backgroundColor");
    REQUIRE(report.orphaned.empty());
    REQUIRE(report.drifted.size() == 1);

    const auto& d = report.drifted.front();
    REQUIRE(d.anchor_id == "card");
    REQUIRE(d.property_path == "layout.padding");
    REQUIRE(d.reason == TweakStore::DriftReason::property_not_found);
    REQUIRE(report.has_drift());
}

int main() { return mini_test::run_all(); }
