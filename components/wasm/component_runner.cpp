#include <vellum/components/abi.h>

#include <cmath>
#include <cstdint>
#include <iostream>
#include <set>
#include <string>

namespace {

struct Evidence final {
    std::set<std::string> ids;
    std::uint32_t commands = 0;
    std::uint32_t digest = 2166136261U;
};

void hash(std::uint32_t& value, std::uint32_t item) {
    for (unsigned shift = 0; shift < 32; shift += 8) {
        value ^= (item >> shift) & 0xFFU;
        value *= 16777619U;
    }
}

int emit(void* user_data, const vellum_component_paint_command_v1* command) {
    auto* evidence = static_cast<Evidence*>(user_data);
    if (evidence == nullptr || command == nullptr ||
        command->struct_size != sizeof(*command) || command->id_suffix == nullptr ||
        !evidence->ids.insert(command->id_suffix).second ||
        !std::isfinite(command->bounds.x) || !std::isfinite(command->bounds.y) ||
        !std::isfinite(command->bounds.width) || !std::isfinite(command->bounds.height) ||
        command->bounds.width < 0.0F || command->bounds.height < 0.0F ||
        (command->kind != VELLUM_COMPONENT_PAINT_RECTANGLE_V1 &&
         command->kind != VELLUM_COMPONENT_PAINT_TEXT_V1)) {
        return 0;
    }
    ++evidence->commands;
    hash(evidence->digest, command->kind);
    for (const char* byte = command->id_suffix; *byte != '\0'; ++byte) {
        evidence->digest ^= static_cast<unsigned char>(*byte);
        evidence->digest *= 16777619U;
    }
    return evidence->commands <= 4096U ? 1 : 0;
}

}  // namespace

int main() {
    const auto* descriptor = vellum_component_entry_v1();
    if (descriptor == nullptr || descriptor->struct_size != sizeof(*descriptor) ||
        descriptor->abi_version != VELLUM_COMPONENT_ABI_VERSION ||
        descriptor->component_id == nullptr || descriptor->render == nullptr) {
        std::cerr << "invalid component descriptor\n";
        return 1;
    }
    Evidence evidence;
    const vellum_component_render_context_v1 context{
        .struct_size = sizeof(vellum_component_render_context_v1),
        .abi_version = VELLUM_COMPONENT_ABI_VERSION,
        .component_id = descriptor->component_id,
        .node_id = "wasm-proof",
        .properties_json = R"({"boost":true})",
        .bounds = {0.0F, 0.0F, 560.0F, 240.0F},
        .emit_user_data = &evidence,
        .emit = emit,
    };
    if (descriptor->render(&context) != 1 || evidence.commands == 0U) {
        std::cerr << "component render failed\n";
        return 1;
    }
    std::cout << "vellum-component-wasm: id=" << descriptor->component_id
              << " commands=" << evidence.commands
              << " digest=" << evidence.digest << '\n';
    return 0;
}
