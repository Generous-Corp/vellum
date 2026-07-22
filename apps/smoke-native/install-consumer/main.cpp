#include <vellum/runtime/kernel.hpp>

#if defined(VELLUM_CONSUMER_HAS_GRAPHICS)
#include <pulp/canvas/canvas.hpp>
#endif

int main() {
    vellum::runtime::Kernel kernel({.application_id = "dev.vellum.install-consumer"});
    if (!kernel.start()) return 1;

#if defined(VELLUM_CONSUMER_HAS_GRAPHICS)
    const auto accent = pulp::canvas::Color::hex(0x8A5CFF);
    if (accent.r8() != 0x8A || accent.g8() != 0x5C || accent.b8() != 0xFF) return 1;
#endif

    return kernel.stop() ? 0 : 1;
}
