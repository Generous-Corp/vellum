#include <vellum/runtime/kernel.hpp>

#if defined(VELLUM_CONSUMER_HAS_GRAPHICS)
#include <vellum/graphics/color.hpp>
#endif

int main() {
    vellum::runtime::Kernel kernel({.application_id = "dev.vellum.install-consumer"});
    if (!kernel.start()) return 1;

#if defined(VELLUM_CONSUMER_HAS_GRAPHICS)
    const auto accent = vellum::graphics::Color::hex(0x8A5CFF);
    if (accent.red8() != 0x8A || accent.green8() != 0x5C || accent.blue8() != 0xFF) return 1;
#endif

    return kernel.stop() ? 0 : 1;
}
