// Vellum::Gpu is kept as a stable forwarding target. The provider group owns
// every GPU implementation object and the pinned Skia/Dawn definitions.
namespace vellum::graphics {
void gpu_frontend_anchor() noexcept {}
}  // namespace vellum::graphics
