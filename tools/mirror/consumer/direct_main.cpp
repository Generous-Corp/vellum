#include <dlfcn.h>
#include <cstring>
#include <mach-o/dyld.h>

// The units are spelled vellum after the framework transfer and pulp before
// it; the consumer builds against whichever the install carries.
#if __has_include(<vellum/canvas/canvas.hpp>)
#include <vellum/canvas/canvas.hpp>
namespace units = vellum;
#define VELLUM_MIRROR_FROM_HSV_SYMBOL "_ZN6vellum6canvas5Color8from_hsvENS1_3HSVEf"
#else
#include <pulp/canvas/canvas.hpp>
namespace units = pulp;
#define VELLUM_MIRROR_FROM_HSV_SYMBOL "_ZN4pulp6canvas5Color8from_hsvENS1_3HSVEf"
#endif

int main() {
    void* const frontend = dlopen("@rpath/libvellum-mirror-canvas.dylib", RTLD_NOW | RTLD_GLOBAL);
    if (frontend == nullptr)
        return 2;

    using FromHsv = units::canvas::Color (*)(units::canvas::Color::HSV, float);
    constexpr char symbol[] = VELLUM_MIRROR_FROM_HSV_SYMBOL;
    const char* provider_path = nullptr;
    for (uint32_t i = 0; i < _dyld_image_count(); ++i) {
        const char* const image = _dyld_get_image_name(i);
        if (image != nullptr &&
            std::strstr(image, "libvellum-skia-dawn-provider.dylib") != nullptr) {
            provider_path = image;
            break;
        }
    }
    if (provider_path == nullptr)
        return 3;

    void* const provider = dlopen(provider_path, RTLD_NOLOAD | RTLD_GLOBAL);
    if (provider == nullptr)
        return 4;
    auto const from_hsv = reinterpret_cast<FromHsv>(dlsym(provider, symbol));
    if (from_hsv == nullptr)
        return 5;

    Dl_info provider_info{};
    if (dladdr(reinterpret_cast<void*>(from_hsv), &provider_info) == 0 ||
        provider_info.dli_fname == nullptr ||
        std::strstr(provider_info.dli_fname, "libvellum-skia-dawn-provider.dylib") == nullptr)
        return 6;

    const auto color = from_hsv({210.0f, 1.0f, 1.0f}, 1.0f);
    return color.r8() == 0 && color.g8() == 128 && color.b8() == 255 ? 0 : 1;
}
