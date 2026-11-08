#include <vellum/c/vellum_host.h>

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int failures = 0;

#define CHECK(condition)                                                              \
    do {                                                                              \
        if (!(condition)) {                                                           \
            fprintf(stderr, "%s:%d: check failed: %s (last error: %s)\n", __FILE__,   \
                    __LINE__, #condition, vellum_host_last_error());                  \
            ++failures;                                                               \
        }                                                                             \
    } while (0)

#define REQUIRE(condition)                                                            \
    do {                                                                              \
        if (!(condition)) {                                                           \
            fprintf(stderr, "%s:%d: required check failed: %s (last error: %s)\n",    \
                    __FILE__, __LINE__, #condition, vellum_host_last_error());        \
            return 1;                                                                 \
        }                                                                             \
    } while (0)

static char* read_file(const char* path, size_t* length) {
    FILE* file = fopen(path, "rb");
    if (file == NULL) return NULL;
    if (fseek(file, 0, SEEK_END) != 0) {
        fclose(file);
        return NULL;
    }
    const long size = ftell(file);
    if (size <= 0 || fseek(file, 0, SEEK_SET) != 0) {
        fclose(file);
        return NULL;
    }
    char* bytes = malloc((size_t)size + 1U);
    if (bytes != NULL && fread(bytes, 1, (size_t)size, file) == (size_t)size) {
        bytes[size] = '\0';
        *length = (size_t)size;
    } else {
        free(bytes);
        bytes = NULL;
    }
    fclose(file);
    return bytes;
}

static int find_node(const vellum_host_app* app, const char* id,
                     vellum_host_accessibility_node* node) {
    size_t count = 0;
    if (vellum_host_app_accessibility_count(app, &count) != VELLUM_HOST_OK) return 0;
    for (size_t index = 0; index < count; ++index) {
        memset(node, 0, sizeof *node);
        node->struct_size = sizeof *node;
        if (vellum_host_app_accessibility_node(app, index, node) == VELLUM_HOST_OK &&
            strcmp(node->node_id, id) == 0) {
            return 1;
        }
    }
    return 0;
}

static int input_value_is(const vellum_host_app* app, const char* expected) {
    vellum_host_text_input input;
    memset(&input, 0, sizeof input);
    input.struct_size = sizeof input;
    return vellum_host_app_text_input(app, 0, &input) == VELLUM_HOST_OK &&
           strcmp(input.value, expected) == 0;
}

static int capture_passes(vellum_host_surface* surface, uint32_t width, uint32_t height) {
    vellum_host_image image;
    memset(&image, 0, sizeof image);
    image.struct_size = sizeof image;
    if (vellum_host_surface_capture_rgba(surface, &image) != VELLUM_HOST_OK) return 0;
    if (image.width != width || image.height != height ||
        image.byte_count != (size_t)width * height * 4U || image.row_bytes != width * 4U) {
        return 0;
    }
    vellum_host_content_stats stats;
    memset(&stats, 0, sizeof stats);
    stats.struct_size = sizeof stats;
    return vellum_host_content_floor(image.pixels, image.byte_count, image.width,
                                     image.height, &stats) == VELLUM_HOST_OK &&
           stats.passes_content_floor == 1U;
}

static int captures_repeat(vellum_host_surface* surface, const vellum_host_app* app) {
    vellum_host_image image;
    memset(&image, 0, sizeof image);
    image.struct_size = sizeof image;
    if (vellum_host_surface_render(surface, app) != VELLUM_HOST_OK ||
        vellum_host_surface_capture_rgba(surface, &image) != VELLUM_HOST_OK) {
        return 0;
    }
    uint8_t* first = malloc(image.byte_count);
    if (first == NULL) return 0;
    memcpy(first, image.pixels, image.byte_count);
    const size_t first_count = image.byte_count;
    const int repeated = vellum_host_surface_render(surface, app) == VELLUM_HOST_OK &&
                         vellum_host_surface_capture_rgba(surface, &image) == VELLUM_HOST_OK &&
                         image.byte_count == first_count &&
                         memcmp(first, image.pixels, first_count) == 0;
    free(first);
    return repeated;
}

struct thread_probe {
    vellum_host_app* app;
    vellum_host_surface* surface;
    vellum_host_status viewport;
    vellum_host_status app_destroy;
    vellum_host_status surface_destroy;
};

static void* probe_from_other_thread(void* context) {
    struct thread_probe* probe = context;
    float width = 0.0F;
    float height = 0.0F;
    probe->viewport = vellum_host_app_viewport(probe->app, &width, &height);
    probe->app_destroy = vellum_host_app_destroy(probe->app);
    probe->surface_destroy = vellum_host_surface_destroy(probe->surface);
    return NULL;
}

struct future_pointer_event {
    vellum_host_pointer_event v1;
    uint32_t newer_field;
};

struct future_app_config {
    vellum_host_app_config v1;
    uint64_t newer_field;
};

static void check_forward_compatibility(vellum_host_app* app,
                                        const vellum_host_accessibility_node* button) {
    struct future_pointer_event future;
    memset(&future, 0, sizeof future);
    future.v1.struct_size = sizeof future;
    future.v1.phase = VELLUM_HOST_POINTER_MOVE;
    future.v1.device = VELLUM_HOST_POINTER_MOUSE;
    future.v1.x = button->bounds.x + 1.0F;
    future.v1.y = button->bounds.y + 1.0F;
    uint32_t outcome = 99U;
    CHECK(vellum_host_app_pointer(app, &future.v1, &outcome) == VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_IGNORED);
    future.newer_field = 1U;
    CHECK(vellum_host_app_pointer(app, &future.v1, &outcome) == VELLUM_HOST_ERROR_VERSION);
    CHECK(strlen(vellum_host_last_error()) > 0);

    struct future_app_config config;
    memset(&config, 0, sizeof config);
    config.v1.struct_size = sizeof config;
    config.v1.bundle = "x";
    config.v1.bundle_length = 1;
    config.newer_field = 7U;
    vellum_host_app* created = (vellum_host_app*)1;
    CHECK(vellum_host_app_create(&config.v1, &created) == VELLUM_HOST_ERROR_VERSION);
    CHECK(created == NULL);

    future.newer_field = 0U;
    future.v1.struct_size = 4097U;
    CHECK(vellum_host_app_pointer(app, &future.v1, &outcome) ==
          VELLUM_HOST_ERROR_INVALID_ARGUMENT);

    struct {
        vellum_host_text_input v1;
        uint64_t newer_field;
    } large_input;
    memset(&large_input, 0, sizeof large_input);
    large_input.v1.struct_size = sizeof large_input;
    large_input.newer_field = 0xA5A5A5A5A5A5A5A5ULL;
    CHECK(vellum_host_app_text_input(app, 0, &large_input.v1) == VELLUM_HOST_OK);
    CHECK(large_input.newer_field == 0xA5A5A5A5A5A5A5A5ULL);
    CHECK(strcmp(large_input.v1.node_id, "proof-title") == 0);
    vellum_host_text_input short_input;
    memset(&short_input, 0, sizeof short_input);
    short_input.struct_size = sizeof short_input - 8U;
    CHECK(vellum_host_app_text_input(app, 0, &short_input) == VELLUM_HOST_ERROR_VERSION);

    vellum_host_key_event key;
    memset(&key, 0, sizeof key);
    key.struct_size = sizeof key - 1U;
    key.key = "Enter";
    CHECK(vellum_host_app_key(app, &key, &outcome) == VELLUM_HOST_ERROR_VERSION);

    vellum_host_pointer_event secondary;
    memset(&secondary, 0, sizeof secondary);
    secondary.struct_size = sizeof secondary;
    secondary.phase = VELLUM_HOST_POINTER_DOWN;
    secondary.device = VELLUM_HOST_POINTER_MOUSE;
    secondary.button = 1U;
    secondary.x = button->bounds.x + button->bounds.width * 0.5F;
    secondary.y = button->bounds.y + button->bounds.height * 0.5F;
    CHECK(vellum_host_app_pointer(app, &secondary, &outcome) == VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_IGNORED);
}

static void check_contract_errors(void) {
    CHECK(vellum_host_abi_version() == VELLUM_HOST_ABI_VERSION);
    CHECK(strcmp(vellum_host_last_error(), "") == 0);

    vellum_host_app* app = (vellum_host_app*)1;
    CHECK(vellum_host_app_create(NULL, &app) == VELLUM_HOST_ERROR_INVALID_ARGUMENT);
    CHECK(app == NULL);
    CHECK(strlen(vellum_host_last_error()) > 0);

    vellum_host_app_config short_config;
    memset(&short_config, 0, sizeof short_config);
    short_config.struct_size = sizeof(uint32_t);
    CHECK(vellum_host_app_create(&short_config, &app) == VELLUM_HOST_ERROR_VERSION);

    vellum_host_app_config broken;
    memset(&broken, 0, sizeof broken);
    broken.struct_size = sizeof broken;
    broken.bundle = "globalThis.__vellum = 1;";
    broken.bundle_length = strlen(broken.bundle);
    CHECK(vellum_host_app_create(&broken, &app) == VELLUM_HOST_ERROR_FAILED);
    CHECK(app == NULL);

    vellum_host_surface* surface = (vellum_host_surface*)1;
    vellum_host_surface_config zero;
    memset(&zero, 0, sizeof zero);
    zero.struct_size = sizeof zero;
    zero.scale = 1.0F;
    CHECK(vellum_host_surface_create(&zero, &surface) == VELLUM_HOST_ERROR_INVALID_ARGUMENT);
    CHECK(surface == NULL);

    uint8_t blank[16 * 16 * 4];
    memset(blank, 0, sizeof blank);
    vellum_host_content_stats stats;
    memset(&stats, 0, sizeof stats);
    stats.struct_size = sizeof stats;
    CHECK(vellum_host_content_floor(blank, sizeof blank, 16, 16, &stats) == VELLUM_HOST_OK);
    CHECK(stats.passes_content_floor == 0U);
    CHECK(stats.pixel_count == 256U);
    CHECK(vellum_host_content_floor(blank, sizeof blank - 1, 16, 16, &stats) ==
          VELLUM_HOST_ERROR_INVALID_ARGUMENT);

    CHECK(vellum_host_app_destroy(NULL) == VELLUM_HOST_OK);
    CHECK(vellum_host_surface_destroy(NULL) == VELLUM_HOST_OK);
}

int main(int argc, char** argv) {
    REQUIRE(argc == 2);
    check_contract_errors();

    size_t length = 0;
    char* bundle = read_file(argv[1], &length);
    REQUIRE(bundle != NULL);

    vellum_host_app_config config;
    memset(&config, 0, sizeof config);
    config.struct_size = sizeof config;
    config.bundle = bundle;
    config.bundle_length = length;
    vellum_host_app* app = NULL;
    REQUIRE(vellum_host_app_create(&config, &app) == VELLUM_HOST_OK);
    free(bundle);

    float width = 0.0F;
    float height = 0.0F;
    REQUIRE(vellum_host_app_viewport(app, &width, &height) == VELLUM_HOST_OK);
    CHECK(width == 360.0F && height == 240.0F);

    vellum_host_accessibility_node button;
    REQUIRE(find_node(app, "proof-increment", &button));
    CHECK(strcmp(button.role, "button") == 0);
    CHECK(strcmp(button.label, "Increment, count 0") == 0);
    CHECK(button.action_count >= 1U);
    const char* action = NULL;
    CHECK(vellum_host_app_accessibility_action(app, 0, 99, &action) ==
          VELLUM_HOST_ERROR_OUT_OF_RANGE);

    vellum_host_accessibility_node field;
    REQUIRE(find_node(app, "proof-title", &field));
    CHECK(strcmp(field.role, "text-field") == 0);
    CHECK(strcmp(field.label, "Proof title") == 0);

    size_t inputs = 0;
    CHECK(vellum_host_app_text_input_count(app, &inputs) == VELLUM_HOST_OK && inputs == 1U);

    vellum_host_surface_config surface_config;
    memset(&surface_config, 0, sizeof surface_config);
    surface_config.struct_size = sizeof surface_config;
    surface_config.width = (uint32_t)width;
    surface_config.height = (uint32_t)height;
    surface_config.scale = 1.0F;
    vellum_host_surface* surface = NULL;
    REQUIRE(vellum_host_surface_create(&surface_config, &surface) == VELLUM_HOST_OK);

    vellum_host_surface_info info;
    memset(&info, 0, sizeof info);
    info.struct_size = sizeof info;
    REQUIRE(vellum_host_surface_info_get(surface, &info) == VELLUM_HOST_OK);

    struct thread_probe probe = {app, surface, VELLUM_HOST_OK, VELLUM_HOST_OK, VELLUM_HOST_OK};
    pthread_t thread;
    REQUIRE(pthread_create(&thread, NULL, probe_from_other_thread, &probe) == 0);
    pthread_join(thread, NULL);
    CHECK(probe.viewport == VELLUM_HOST_ERROR_WRONG_THREAD);
    CHECK(probe.app_destroy == VELLUM_HOST_ERROR_WRONG_THREAD);
    CHECK(probe.surface_destroy == VELLUM_HOST_ERROR_WRONG_THREAD);
    CHECK(vellum_host_app_viewport(app, &width, &height) == VELLUM_HOST_OK);
    CHECK(vellum_host_surface_info_get(surface, &info) == VELLUM_HOST_OK);
    CHECK(info.available == 1U && info.native_surface == 0U && info.fallback == 0U);
    CHECK(strcmp(info.renderer, "Skia Graphite") == 0);
    CHECK(strcmp(info.backend, "Metal") == 0);

    REQUIRE(vellum_host_surface_render(surface, app) == VELLUM_HOST_OK);
    CHECK(capture_passes(surface, 360U, 240U));
    CHECK(captures_repeat(surface, app));

    check_forward_compatibility(app, &button);

    vellum_host_pointer_event press;
    memset(&press, 0, sizeof press);
    press.struct_size = sizeof press;
    press.phase = VELLUM_HOST_POINTER_DOWN;
    press.device = VELLUM_HOST_POINTER_MOUSE;
    press.x = button.bounds.x + button.bounds.width * 0.5F;
    press.y = button.bounds.y + button.bounds.height * 0.5F;
    uint32_t outcome = 99U;
    CHECK(vellum_host_app_pointer(app, &press, &outcome) == VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_DISPATCHED);
    CHECK(find_node(app, "proof-increment", &button) &&
          strcmp(button.label, "Increment, count 1") == 0);

    CHECK(vellum_host_app_accessibility_perform(app, "proof-increment", "press") ==
          VELLUM_HOST_OK);
    CHECK(find_node(app, "proof-increment", &button) &&
          strcmp(button.label, "Increment, count 2") == 0);
    CHECK(vellum_host_app_accessibility_perform(app, "proof-increment", "missing") ==
          VELLUM_HOST_ERROR_NOT_FOUND);

    CHECK(vellum_host_app_insert_text(app, "ignored", &outcome) == VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_IGNORED);

    press.x = field.bounds.x + 4.0F;
    press.y = field.bounds.y + field.bounds.height * 0.5F;
    CHECK(vellum_host_app_pointer(app, &press, &outcome) == VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_FOCUSED);
    CHECK(vellum_host_app_insert_text(app, "H\xC3\xA9llo \xF0\x9F\x98\x80", &outcome) ==
          VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_DISPATCHED);
    CHECK(input_value_is(app, "H\xC3\xA9llo \xF0\x9F\x98\x80"));
    CHECK(vellum_host_app_insert_text(app, "\xC3", &outcome) ==
          VELLUM_HOST_ERROR_INVALID_ARGUMENT);

    vellum_host_key_event key;
    memset(&key, 0, sizeof key);
    key.struct_size = sizeof key;
    key.key = "Backspace";
    CHECK(vellum_host_app_key(app, &key, &outcome) == VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_DISPATCHED);
    CHECK(input_value_is(app, "H\xC3\xA9llo "));
    key.key = "Enter";
    CHECK(vellum_host_app_key(app, &key, &outcome) == VELLUM_HOST_OK);
    CHECK(outcome == VELLUM_HOST_EVENT_DISPATCHED);

    vellum_host_text_input input;
    memset(&input, 0, sizeof input);
    input.struct_size = sizeof input;
    CHECK(vellum_host_app_text_input(app, 0, &input) == VELLUM_HOST_OK && input.focused == 1U);
    CHECK(vellum_host_app_focus(app, "missing") == VELLUM_HOST_ERROR_NOT_FOUND);
    CHECK(vellum_host_app_focus(app, NULL) == VELLUM_HOST_OK);
    CHECK(vellum_host_app_text_input(app, 0, &input) == VELLUM_HOST_OK && input.focused == 0U);

    vellum_host_pump_result pump;
    memset(&pump, 0, sizeof pump);
    pump.struct_size = sizeof pump;
    CHECK(vellum_host_app_pump(app, 16U, 64U, &pump) == VELLUM_HOST_OK);
    CHECK(vellum_host_app_dispatch(app, "proof:increment", NULL) == VELLUM_HOST_OK);
    CHECK(vellum_host_app_dispatch(app, "", NULL) == VELLUM_HOST_ERROR_INVALID_ARGUMENT);

    REQUIRE(vellum_host_surface_render(surface, app) == VELLUM_HOST_OK);
    CHECK(capture_passes(surface, 360U, 240U));

    REQUIRE(vellum_host_surface_resize(surface, 360U, 240U, 2.0F) == VELLUM_HOST_OK);
    REQUIRE(vellum_host_surface_render(surface, app) == VELLUM_HOST_OK);
    CHECK(capture_passes(surface, 720U, 480U));
    CHECK(vellum_host_surface_resize(surface, 0U, 240U, 1.0F) ==
          VELLUM_HOST_ERROR_INVALID_ARGUMENT);

    CHECK(vellum_host_surface_destroy(surface) == VELLUM_HOST_OK);
    CHECK(vellum_host_app_destroy(app) == VELLUM_HOST_OK);

    if (failures != 0) {
        fprintf(stderr, "vellum-host-c-test: %d failure(s)\n", failures);
        return 1;
    }
    printf("vellum-host-c-test: passed\n");
    return 0;
}
