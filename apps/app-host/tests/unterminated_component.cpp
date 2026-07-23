#include <vellum/components/abi.h>

#include <array>

namespace {

const std::array<char, 65> kUnterminatedId = [] {
    std::array<char, 65> value{};
    value.fill('a');
    return value;
}();

int render(const vellum_component_render_context_v1*) { return 0; }

const vellum_component_descriptor_v1 kDescriptor{
    sizeof(vellum_component_descriptor_v1),
    VELLUM_COMPONENT_ABI_VERSION,
    kUnterminatedId.data(),
    render,
};

}  // namespace

extern "C" VELLUM_COMPONENT_EXPORT const vellum_component_descriptor_v1*
vellum_component_entry_v1(void) {
    return &kDescriptor;
}
