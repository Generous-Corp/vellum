#include <vellum/components/abi.h>

#include <cstdlib>
#include <cstring>

namespace {

// Paints one band whose alpha is the material's "angle" over 360, read from
// the properties the host passes, so the test sees the value arrive.
int render(const vellum_component_render_context_v1* context) {
    const char* angle = std::strstr(context->properties_json, "\"angle\":");
    float alpha = 0.0F;
    if (angle != nullptr) alpha = static_cast<float>(std::atof(angle + 8)) / 360.0F;
    vellum_component_paint_command_v1 command{};
    command.struct_size = sizeof(command);
    command.kind = VELLUM_COMPONENT_PAINT_RECTANGLE_V1;
    command.id_suffix = "band";
    command.bounds = {0.0F, 0.0F, context->bounds.width, context->bounds.height * 0.5F};
    command.fill = {1.0F, 1.0F, 1.0F, alpha};
    if (context->emit(context->emit_user_data, &command) != 1) return 0;
    // Echoes the properties as text so the test can read what arrived.
    vellum_component_paint_command_v1 echo{};
    echo.struct_size = sizeof(echo);
    echo.kind = VELLUM_COMPONENT_PAINT_TEXT_V1;
    echo.id_suffix = "properties";
    echo.bounds = {0.0F, 0.0F, context->bounds.width, context->bounds.height};
    echo.fill = {0.0F, 0.0F, 0.0F, 0.0F};
    echo.text = context->properties_json;
    echo.font_size = 8.0F;
    return context->emit(context->emit_user_data, &echo);
}

const vellum_component_descriptor_v1 kDescriptor{
    sizeof(vellum_component_descriptor_v1),
    VELLUM_COMPONENT_ABI_VERSION,
    "sheen",
    render,
};

}  // namespace

extern "C" VELLUM_COMPONENT_EXPORT const vellum_component_descriptor_v1*
vellum_component_entry_v1(void) {
    return &kDescriptor;
}
