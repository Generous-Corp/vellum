#include <vellum/components/abi.h>

#include <array>

namespace {

const std::array<char, 1024U * 1024U + 1U> kUnterminatedText = [] {
    std::array<char, 1024U * 1024U + 1U> value{};
    value.fill('x');
    return value;
}();

int render(const vellum_component_render_context_v1* context) {
    vellum_component_paint_command_v1 command{};
    command.struct_size = sizeof(command);
    command.kind = VELLUM_COMPONENT_PAINT_TEXT_V1;
    command.id_suffix = "label";
    command.bounds = {0.0F, 0.0F, 100.0F, 20.0F};
    command.fill = {1.0F, 1.0F, 1.0F, 1.0F};
    command.text = kUnterminatedText.data();
    command.font_size = 14.0F;
    return context->emit(context->emit_user_data, &command);
}

const vellum_component_descriptor_v1 kDescriptor{
    sizeof(vellum_component_descriptor_v1),
    VELLUM_COMPONENT_ABI_VERSION,
    "level-meter",
    render,
};

}  // namespace

extern "C" VELLUM_COMPONENT_EXPORT const vellum_component_descriptor_v1*
vellum_component_entry_v1(void) {
    return &kDescriptor;
}
