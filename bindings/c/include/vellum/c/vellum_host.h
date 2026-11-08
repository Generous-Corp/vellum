#ifndef VELLUM_C_VELLUM_HOST_H
#define VELLUM_C_VELLUM_HOST_H

#include <stddef.h>
#include <stdint.h>

#if defined(__GNUC__) || defined(__clang__)
#define VELLUM_HOST_API __attribute__((visibility("default")))
#else
#define VELLUM_HOST_API
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define VELLUM_HOST_ABI_VERSION 1u

typedef int32_t vellum_host_status;

#define VELLUM_HOST_OK 0
#define VELLUM_HOST_ERROR_INVALID_ARGUMENT 1
#define VELLUM_HOST_ERROR_VERSION 2
#define VELLUM_HOST_ERROR_UNAVAILABLE 3
#define VELLUM_HOST_ERROR_FAILED 4
#define VELLUM_HOST_ERROR_NOT_FOUND 5
#define VELLUM_HOST_ERROR_WRONG_THREAD 6
#define VELLUM_HOST_ERROR_OUT_OF_RANGE 7

typedef struct vellum_host_surface vellum_host_surface;
typedef struct vellum_host_app vellum_host_app;

typedef struct vellum_host_rect {
    float x;
    float y;
    float width;
    float height;
} vellum_host_rect;

typedef struct vellum_host_surface_config {
    uint32_t struct_size;
    uint32_t width;
    uint32_t height;
    float scale;
    uint32_t vsync;
    void* metal_layer;
    const char* font_directory;
} vellum_host_surface_config;

typedef struct vellum_host_surface_info {
    uint32_t struct_size;
    uint32_t available;
    uint32_t native_surface;
    uint32_t fallback;
    const char* renderer;
    const char* backend;
    const char* adapter;
    const char* texture_format;
} vellum_host_surface_info;

typedef struct vellum_host_image {
    uint32_t struct_size;
    uint32_t width;
    uint32_t height;
    uint32_t row_bytes;
    const uint8_t* pixels;
    size_t byte_count;
} vellum_host_image;

typedef struct vellum_host_content_stats {
    uint32_t struct_size;
    uint32_t passes_content_floor;
    size_t pixel_count;
    size_t unique_colors;
    size_t opaque_pixels;
    size_t non_background_pixels;
    double luminance_standard_deviation;
} vellum_host_content_stats;

typedef struct vellum_host_app_config {
    uint32_t struct_size;
    uint32_t settle_max_tasks;
    const char* bundle;
    size_t bundle_length;
} vellum_host_app_config;

typedef struct vellum_host_pump_result {
    uint32_t struct_size;
    uint32_t rendered;
    uint32_t idle;
    size_t tasks_executed;
} vellum_host_pump_result;

#define VELLUM_HOST_POINTER_DOWN 1u
#define VELLUM_HOST_POINTER_UP 2u
#define VELLUM_HOST_POINTER_MOVE 3u
#define VELLUM_HOST_POINTER_CANCEL 4u

#define VELLUM_HOST_POINTER_BUTTON_PRIMARY 0u

#define VELLUM_HOST_POINTER_MOUSE 1u
#define VELLUM_HOST_POINTER_TOUCH 2u
#define VELLUM_HOST_POINTER_PEN 3u

typedef struct vellum_host_pointer_event {
    uint32_t struct_size;
    uint32_t phase;
    uint32_t device;
    uint32_t button;
    float x;
    float y;
} vellum_host_pointer_event;

typedef struct vellum_host_key_event {
    uint32_t struct_size;
    uint32_t repeat;
    const char* key;
} vellum_host_key_event;

#define VELLUM_HOST_EVENT_IGNORED 0u
#define VELLUM_HOST_EVENT_DISPATCHED 1u
#define VELLUM_HOST_EVENT_FOCUSED 2u
#define VELLUM_HOST_EVENT_UNFOCUSED 3u

#define VELLUM_HOST_A11Y_DISABLED (1u << 0)
#define VELLUM_HOST_A11Y_SELECTED (1u << 1)
#define VELLUM_HOST_A11Y_CHECKED (1u << 2)
#define VELLUM_HOST_A11Y_MIXED (1u << 3)
#define VELLUM_HOST_A11Y_EXPANDED (1u << 4)
#define VELLUM_HOST_A11Y_HAS_CHECKED (1u << 5)
#define VELLUM_HOST_A11Y_HAS_EXPANDED (1u << 6)

typedef struct vellum_host_accessibility_node {
    uint32_t struct_size;
    uint32_t state;
    const char* node_id;
    const char* role;
    const char* label;
    const char* value;
    size_t action_count;
    vellum_host_rect bounds;
} vellum_host_accessibility_node;

typedef struct vellum_host_text_input {
    uint32_t struct_size;
    uint32_t focused;
    const char* node_id;
    const char* value;
    const char* placeholder;
    size_t selection_start;
    size_t selection_end;
    vellum_host_rect bounds;
} vellum_host_text_input;

VELLUM_HOST_API uint32_t vellum_host_abi_version(void);
VELLUM_HOST_API const char* vellum_host_last_error(void);

VELLUM_HOST_API vellum_host_status vellum_host_content_floor(
    const uint8_t* rgba, size_t byte_count, uint32_t width, uint32_t height,
    vellum_host_content_stats* stats);

VELLUM_HOST_API vellum_host_status vellum_host_surface_create(
    const vellum_host_surface_config* config, vellum_host_surface** surface);
VELLUM_HOST_API vellum_host_status vellum_host_surface_destroy(vellum_host_surface* surface);
VELLUM_HOST_API vellum_host_status vellum_host_surface_info_get(
    const vellum_host_surface* surface, vellum_host_surface_info* info);
VELLUM_HOST_API vellum_host_status vellum_host_surface_resize(
    vellum_host_surface* surface, uint32_t width, uint32_t height, float scale);
VELLUM_HOST_API vellum_host_status vellum_host_surface_render(
    vellum_host_surface* surface, const vellum_host_app* app);
VELLUM_HOST_API vellum_host_status vellum_host_surface_capture_rgba(
    vellum_host_surface* surface, vellum_host_image* image);

VELLUM_HOST_API vellum_host_status vellum_host_app_create(
    const vellum_host_app_config* config, vellum_host_app** app);
VELLUM_HOST_API vellum_host_status vellum_host_app_destroy(vellum_host_app* app);
VELLUM_HOST_API vellum_host_status vellum_host_app_viewport(
    const vellum_host_app* app, float* width, float* height);
VELLUM_HOST_API vellum_host_status vellum_host_app_pump(
    vellum_host_app* app, uint64_t advance_milliseconds, uint32_t max_tasks,
    vellum_host_pump_result* result);
VELLUM_HOST_API vellum_host_status vellum_host_app_dispatch(
    vellum_host_app* app, const char* action, const char* payload_json);
VELLUM_HOST_API vellum_host_status vellum_host_app_pointer(
    vellum_host_app* app, const vellum_host_pointer_event* event, uint32_t* outcome);
VELLUM_HOST_API vellum_host_status vellum_host_app_focus(
    vellum_host_app* app, const char* node_id);
VELLUM_HOST_API vellum_host_status vellum_host_app_key(
    vellum_host_app* app, const vellum_host_key_event* event, uint32_t* outcome);
VELLUM_HOST_API vellum_host_status vellum_host_app_insert_text(
    vellum_host_app* app, const char* utf8, uint32_t* outcome);

VELLUM_HOST_API vellum_host_status vellum_host_app_accessibility_count(
    const vellum_host_app* app, size_t* count);
VELLUM_HOST_API vellum_host_status vellum_host_app_accessibility_node(
    const vellum_host_app* app, size_t index, vellum_host_accessibility_node* node);
VELLUM_HOST_API vellum_host_status vellum_host_app_accessibility_action(
    const vellum_host_app* app, size_t node_index, size_t action_index,
    const char** action);
VELLUM_HOST_API vellum_host_status vellum_host_app_accessibility_perform(
    vellum_host_app* app, const char* node_id, const char* action);
VELLUM_HOST_API vellum_host_status vellum_host_app_text_input_count(
    const vellum_host_app* app, size_t* count);
VELLUM_HOST_API vellum_host_status vellum_host_app_text_input(
    const vellum_host_app* app, size_t index, vellum_host_text_input* input);

#ifdef __cplusplus
}
#endif

#endif
