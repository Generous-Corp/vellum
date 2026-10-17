#include <dlfcn.h>

#include <pulp/canvas/canvas.hpp>

int main() {
    void* const frontend = dlopen("@rpath/libvellum-mirror-canvas.dylib", RTLD_NOW | RTLD_GLOBAL);
    if (frontend == nullptr)
        return 2;

    using FromHsv = pulp::canvas::Color (*)(pulp::canvas::Color::HSV, float);
    auto const from_hsv = reinterpret_cast<FromHsv>(dlsym(
        frontend, "_ZN4pulp6canvas5Color8from_hsvENS1_3HSVEf"));
    if (from_hsv == nullptr)
        return 3;

    const auto color = from_hsv({210.0f, 1.0f, 1.0f}, 1.0f);
    return color.r8() == 0 && color.g8() == 128 && color.b8() == 255 ? 0 : 1;
}
