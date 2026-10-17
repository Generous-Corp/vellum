// Gives each mirror unit library a translation unit of its own; the unit
// objects come from its mirrored archives.
namespace {
[[maybe_unused]] const int unit_anchor = 0;
}

// Keep a concrete symbol in forwarding unit dylibs. The implementation
// symbols remain in their owning provider group, while package inertness can
// still inspect this installed image as a real library.
extern "C" void vellum_mirror_forwarding_anchor() noexcept {}
