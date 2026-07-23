#ifndef VELLUM_COMPONENTS_ABI_H
#define VELLUM_COMPONENTS_ABI_H

#include <stdint.h>

#define VELLUM_COMPONENT_ABI_VERSION 1u
#define VELLUM_COMPONENT_ENTRY_SYMBOL "vellum_component_entry_v1"

#if defined(_WIN32)
#define VELLUM_COMPONENT_EXPORT __declspec(dllexport)
#else
#define VELLUM_COMPONENT_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

typedef struct vellum_component_rect_v1 {
    float x;
    float y;
    float width;
    float height;
} vellum_component_rect_v1;

typedef struct vellum_component_color_v1 {
    float red;
    float green;
    float blue;
    float alpha;
} vellum_component_color_v1;

typedef enum vellum_component_paint_kind_v1 {
    VELLUM_COMPONENT_PAINT_RECTANGLE_V1 = 1,
    VELLUM_COMPONENT_PAINT_TEXT_V1 = 2,
} vellum_component_paint_kind_v1;

/* String pointers are borrowed only for the duration of emit(). Bounds are
   relative to the custom component node. */
typedef struct vellum_component_paint_command_v1 {
    uint32_t struct_size;
    uint32_t kind;
    const char* id_suffix;
    vellum_component_rect_v1 bounds;
    vellum_component_color_v1 fill;
    float corner_radius;
    const char* text;
    float font_size;
} vellum_component_paint_command_v1;

typedef int (*vellum_component_emit_v1)(
    void* user_data, const vellum_component_paint_command_v1* command);

typedef struct vellum_component_render_context_v1 {
    uint32_t struct_size;
    uint32_t abi_version;
    const char* component_id;
    const char* node_id;
    const char* properties_json;
    vellum_component_rect_v1 bounds;
    void* emit_user_data;
    vellum_component_emit_v1 emit;
} vellum_component_render_context_v1;

typedef int (*vellum_component_render_v1)(
    const vellum_component_render_context_v1* context);

typedef struct vellum_component_descriptor_v1 {
    uint32_t struct_size;
    uint32_t abi_version;
    const char* component_id;
    vellum_component_render_v1 render;
} vellum_component_descriptor_v1;

/* Every app-built module exports exactly this entry point and one descriptor. */
VELLUM_COMPONENT_EXPORT const vellum_component_descriptor_v1*
vellum_component_entry_v1(void);

#ifdef __cplusplus
}
#endif

#endif
