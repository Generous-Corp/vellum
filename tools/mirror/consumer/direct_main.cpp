#include <dlfcn.h>
#include <cstring>

#include <pulp/canvas/canvas.hpp>

int main() {
    void* const frontend = dlopen("@rpath/libvellum-mirror-canvas.dylib", RTLD_NOW | RTLD_GLOBAL);
    if (frontend == nullptr)
        return 2;

    using FromHsv = pulp::canvas::Color (*)(pulp::canvas::Color::HSV, float);
    constexpr char symbol[] = "_ZN4pulp6canvas5Color8from_hsvENS1_3HSVEf";
    auto from_hsv = reinterpret_cast<FromHsv>(dlsym(RTLD_DEFAULT, symbol));
    if (from_hsv == nullptr)
        from_hsv = reinterpret_cast<FromHsv>(dlsym(RTLD_NEXT, symbol));
    if (from_hsv == nullptr)
        return 3;

    Dl_info provider_info{};
    if (dladdr(reinterpret_cast<void*>(from_hsv), &provider_info) == 0 ||
        provider_info.dli_fname == nullptr ||
        std::strstr(provider_info.dli_fname, "libvellum-skia-dawn-provider.dylib") == nullptr)
        return 4;

    const auto color = from_hsv({210.0f, 1.0f, 1.0f}, 1.0f);
    return color.r8() == 0 && color.g8() == 128 && color.b8() == 255 ? 0 : 1;
}
