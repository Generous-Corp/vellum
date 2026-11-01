#include <vellum/rmlui/backend.hpp>

#include "session.hpp"

#include <RmlUi/Core.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <climits>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace vellum::rmlui {

AssetProvider::~AssetProvider() = default;
RenderInterface::~RenderInterface() = default;
LuaInterface::~LuaInterface() = default;
ActionSink::~ActionSink() = default;
ExtensionHost::~ExtensionHost() = default;
ModelHost::~ModelHost() = default;

namespace {

class RenderAdapter final : public Rml::RenderInterface {
  public:
    // Bound per-instance resource admission so repeated document rebuilds
    // cannot grow handle tables or backing payloads without limit.
    static constexpr std::size_t max_geometry_handles = 4096;
    static constexpr std::size_t max_geometry_vertices = 1u << 20;
    static constexpr std::size_t max_geometry_indices = 1u << 20;
    static constexpr std::size_t max_geometry_bytes = 64u * 1024u * 1024u;
    static constexpr std::size_t max_texture_handles = 4096;
    static constexpr std::size_t max_texture_bytes = 64u * 1024u * 1024u;

    RenderAdapter(AssetProvider& assets, vellum::rmlui::RenderInterface& sink,
                  const std::string& scope_prefix)
        : assets_(assets), sink_(sink), scope_prefix_(scope_prefix) {}

    Rml::CompiledGeometryHandle CompileGeometry(Rml::Span<const Rml::Vertex> vertices,
                                                Rml::Span<const int> indices) override {
        if (vertices.empty() || indices.empty() || vertices.size() > max_geometry_vertices ||
            indices.size() > max_geometry_indices)
            return 0;
        for (const auto index : indices)
            if (index < 0 || static_cast<std::size_t>(index) >= vertices.size())
                return 0;
        const auto payload_bytes =
            vertices.size() * sizeof(DrawVertex) + indices.size() * sizeof(std::uint32_t);
        if (payload_bytes > max_geometry_bytes ||
            geometry_bytes_ > max_geometry_bytes - payload_bytes)
            return 0;
        Geometry geometry;
        float min_x = vertices[0].position.x;
        float min_y = vertices[0].position.y;
        float max_x = min_x;
        float max_y = min_y;
        for (const auto& vertex : vertices) {
            min_x = std::min(min_x, vertex.position.x);
            min_y = std::min(min_y, vertex.position.y);
            max_x = std::max(max_x, vertex.position.x);
            max_y = std::max(max_y, vertex.position.y);
        }
        geometry.x = min_x;
        geometry.y = min_y;
        geometry.width = max_x - min_x;
        geometry.height = max_y - min_y;
        auto vertex_payload = std::make_shared<std::vector<DrawVertex>>();
        vertex_payload->reserve(vertices.size());
        for (const auto& vertex : vertices) {
            const auto& colour = vertex.colour;
            const auto packed = (static_cast<std::uint32_t>(colour.red) << 24) |
                                (static_cast<std::uint32_t>(colour.green) << 16) |
                                (static_cast<std::uint32_t>(colour.blue) << 8) |
                                static_cast<std::uint32_t>(colour.alpha);
            vertex_payload->push_back(DrawVertex{vertex.position.x, vertex.position.y,
                                                 vertex.tex_coord.x, vertex.tex_coord.y, packed});
        }
        auto index_payload = std::make_shared<std::vector<std::uint32_t>>();
        index_payload->reserve(indices.size());
        for (const auto index : indices)
            index_payload->push_back(static_cast<std::uint32_t>(index));
        geometry.vertices = std::move(vertex_payload);
        geometry.indices = std::move(index_payload);
        geometry.bytes = payload_bytes;
        Rml::CompiledGeometryHandle handle = 0;
        if (!free_geometry_handles_.empty()) {
            handle = free_geometry_handles_.back();
            free_geometry_handles_.pop_back();
            geometries_[static_cast<std::size_t>(handle - 1)] = std::move(geometry);
        } else {
            if (geometries_.size() >= max_geometry_handles)
                return 0;
            geometries_.push_back(std::move(geometry));
            handle = static_cast<Rml::CompiledGeometryHandle>(geometries_.size());
        }
        geometry_bytes_ += payload_bytes;
        return handle;
    }

    void RenderGeometry(Rml::CompiledGeometryHandle handle, Rml::Vector2f translation,
                        Rml::TextureHandle texture_handle) override {
        if (handle == 0 || handle > geometries_.size())
            return;
        const auto& geometry = geometries_[static_cast<std::size_t>(handle - 1)];
        if (!geometry.vertices)
            return;
        DrawCommand command;
        command.kind = DrawCommand::Kind::rectangle;
        command.x = geometry.x + translation.x;
        command.y = geometry.y + translation.y;
        command.width = geometry.width;
        command.height = geometry.height;
        if (texture_handle != 0 && texture_handle <= textures_.size() &&
            textures_[static_cast<std::size_t>(texture_handle - 1)].bytes) {
            command.kind = DrawCommand::Kind::texture;
            const auto& texture = textures_[static_cast<std::size_t>(texture_handle - 1)];
            command.texture_path = texture.path;
            command.texture_bytes = texture.bytes;
            command.texture_width = texture.width;
            command.texture_height = texture.height;
        }
        command.translation_x = translation.x;
        command.translation_y = translation.y;
        command.vertices = geometry.vertices;
        command.indices = geometry.indices;
        command.scissor_enabled = scissor_enabled_;
        command.scissor = scissor_;
        command.transform_enabled = transform_enabled_;
        command.transform = transform_;
        sink_.submit(command);
    }

    void ReleaseGeometry(Rml::CompiledGeometryHandle handle) override {
        if (handle != 0 && handle <= geometries_.size() &&
            geometries_[static_cast<std::size_t>(handle - 1)].vertices) {
            geometry_bytes_ -= geometries_[static_cast<std::size_t>(handle - 1)].bytes;
            geometries_[static_cast<std::size_t>(handle - 1)] = {};
            free_geometry_handles_.push_back(handle);
        }
    }

    Rml::TextureHandle LoadTexture(Rml::Vector2i& dimensions, const Rml::String& source) override {
        Asset asset;
        if (assets_.load(detail::strip_scope_prefix(source, scope_prefix_), asset) != Status::ok)
            return 0;
        const auto size = image_dimensions(asset.bytes);
        if (!size)
            return 0;
        dimensions = {size->first, size->second};
        return store_texture(Texture{
            std::move(asset.path), std::make_shared<const std::string>(std::move(asset.bytes)),
            static_cast<std::uint32_t>(size->first), static_cast<std::uint32_t>(size->second)});
    }

    Rml::TextureHandle GenerateTexture(Rml::Span<const Rml::byte> bytes,
                                       Rml::Vector2i dimensions) override {
        if (dimensions.x <= 0 || dimensions.y <= 0 || bytes.size() > max_texture_bytes ||
            texture_bytes_ > max_texture_bytes - bytes.size() ||
            bytes.size() != static_cast<std::size_t>(dimensions.x) *
                                static_cast<std::size_t>(dimensions.y) * 4u)
            return 0;
        auto payload = std::make_shared<std::string>(reinterpret_cast<const char*>(bytes.data()),
                                                     bytes.size());
        return store_texture(Texture{"", std::move(payload),
                                     static_cast<std::uint32_t>(dimensions.x),
                                     static_cast<std::uint32_t>(dimensions.y)});
    }

    void ReleaseTexture(Rml::TextureHandle handle) override {
        if (handle != 0 && handle <= textures_.size() &&
            textures_[static_cast<std::size_t>(handle - 1)].bytes) {
            texture_bytes_ -= textures_[static_cast<std::size_t>(handle - 1)].bytes->size();
            textures_[static_cast<std::size_t>(handle - 1)] = {};
            free_texture_handles_.push_back(handle);
        }
    }

    void EnableScissorRegion(bool enable) override {
        scissor_enabled_ = enable;
    }

    void SetScissorRegion(Rml::Rectanglei region) override {
        scissor_ = {region.Left(), region.Top(), region.Width(), region.Height()};
    }

    void SetTransform(const Rml::Matrix4f* transform) override {
        transform_enabled_ = transform != nullptr;
        if (transform)
            std::copy_n(transform->data(), transform_.size(), transform_.begin());
        else
            transform_.fill(0.0f);
    }

  private:
    struct Texture {
        std::string path;
        std::shared_ptr<const std::string> bytes;
        std::uint32_t width = 0;
        std::uint32_t height = 0;
    };

    struct Geometry {
        float x = 0.0f;
        float y = 0.0f;
        float width = 0.0f;
        float height = 0.0f;
        std::shared_ptr<const std::vector<DrawVertex>> vertices;
        std::shared_ptr<const std::vector<std::uint32_t>> indices;
        std::size_t bytes = 0;
    };

    Rml::TextureHandle store_texture(Texture texture) {
        if (!texture.bytes || texture.bytes->size() > max_texture_bytes ||
            texture_bytes_ > max_texture_bytes - texture.bytes->size())
            return 0;
        Rml::TextureHandle handle = 0;
        if (!free_texture_handles_.empty()) {
            handle = free_texture_handles_.back();
            free_texture_handles_.pop_back();
            textures_[static_cast<std::size_t>(handle - 1)] = std::move(texture);
        } else {
            if (textures_.size() >= max_texture_handles)
                return 0;
            textures_.push_back(std::move(texture));
            handle = static_cast<Rml::TextureHandle>(textures_.size());
        }
        texture_bytes_ += textures_[static_cast<std::size_t>(handle - 1)].bytes->size();
        return handle;
    }

    static std::optional<std::pair<int, int>> image_dimensions(const std::string& bytes) {
        // PNG: signature, IHDR length/type, then big-endian width/height.
        if (bytes.size() >= 24 && static_cast<unsigned char>(bytes[0]) == 0x89 && bytes[1] == 'P' &&
            bytes[2] == 'N' && bytes[3] == 'G' && bytes[12] == 'I' && bytes[13] == 'H' &&
            bytes[14] == 'D' && bytes[15] == 'R') {
            const auto u32 = [&](std::size_t offset) {
                return (static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[offset]))
                        << 24) |
                       (static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[offset + 1]))
                        << 16) |
                       (static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[offset + 2]))
                        << 8) |
                       static_cast<std::uint32_t>(static_cast<unsigned char>(bytes[offset + 3]));
            };
            const auto width = u32(16);
            const auto height = u32(20);
            if (width > 0 && height > 0 && width <= INT_MAX && height <= INT_MAX)
                return std::pair{static_cast<int>(width), static_cast<int>(height)};
        }
        return std::nullopt;
    }

    AssetProvider& assets_;
    vellum::rmlui::RenderInterface& sink_;
    const std::string& scope_prefix_;
    std::vector<Geometry> geometries_;
    std::vector<Rml::CompiledGeometryHandle> free_geometry_handles_;
    std::vector<Texture> textures_;
    std::vector<Rml::TextureHandle> free_texture_handles_;
    std::size_t geometry_bytes_ = 0;
    std::size_t texture_bytes_ = 0;
    bool scissor_enabled_ = false;
    ScissorRect scissor_{};
    bool transform_enabled_ = false;
    std::array<float, 16> transform_{};
};

std::atomic_uint64_t next_context_id{1};

} // namespace

struct Backend::Runtime {
    Runtime(AssetProvider& assets, vellum::rmlui::RenderInterface& renderer)
        : context_name("vellum-rmlui-" + std::to_string(next_context_id.fetch_add(1))),
          scope_prefix(context_name + "/"), render(assets, renderer, scope_prefix) {}

    std::string context_name;
    std::string scope_prefix;
    RenderAdapter render;
    Rml::Context* context = nullptr;
    Rml::ElementDocument* document = nullptr;
    struct ModelBinding {
        std::shared_ptr<ModelHost> host;
        Rml::DataModelHandle handle;
    };
    std::unordered_map<std::string, ModelBinding> models;
    bool holds_runtime = false;
};

Backend::Backend(AssetProvider& assets, RenderInterface& renderer, LuaInterface& lua,
                 ActionSink* actions, ExtensionHost* extensions)
    : assets_(assets), renderer_(renderer), lua_(lua), actions_(actions), extensions_(extensions) {}

Backend::~Backend() {
    close();
}

bool Backend::fonts_enabled() noexcept {
    return VELLUM_RMLUI_FONTS_ENABLED != 0;
}

std::size_t Backend::open_instances() noexcept {
    return detail::runtime_references();
}

Status Backend::open(std::uint32_t width, std::uint32_t height) {
    if (open_)
        return Status::already_open;
    if (width == 0 || height == 0 || width > INT_MAX || height > INT_MAX)
        return Status::rejected;
    if ((!custom_properties_.empty() || !element_instancers_.empty()) && !extensions_)
        return Status::rejected;
    auto runtime = std::make_unique<Runtime>(assets_, renderer_);
    if (!detail::acquire_runtime())
        return Status::rejected;
    runtime->holds_runtime = true;
    // From here a failure must hand the reference back through close().
    runtime_ = std::move(runtime);
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    try {
        if (extensions_) {
            for (const auto& registration : custom_properties_) {
                if (extensions_->register_custom_property(registration) != Status::ok)
                    throw std::runtime_error("RmlUi custom property admission rejected");
            }
            for (const auto& registration : element_instancers_) {
                if (extensions_->register_element_instancer(registration) != Status::ok)
                    throw std::runtime_error("RmlUi element instancer admission rejected");
            }
        }
    } catch (...) {
        close();
        return Status::rejected;
    }
    runtime_->context =
        Rml::CreateContext(runtime_->context_name,
                           {static_cast<int>(width), static_cast<int>(height)}, &runtime_->render);
    if (!runtime_->context) {
        close();
        return Status::rejected;
    }
    width_ = width;
    height_ = height;
    open_ = true;
    return Status::ok;
}

Status Backend::register_custom_property(CustomPropertyRegistration registration) {
    if (open_ || registration.name.empty() || registration.parser.empty() ||
        registration.default_value.empty())
        return Status::rejected;
    if (std::any_of(custom_properties_.begin(), custom_properties_.end(),
                    [&](const auto& item) { return item.name == registration.name; }))
        return Status::rejected;
    custom_properties_.push_back(std::move(registration));
    return Status::ok;
}

Status Backend::register_element_instancer(ElementInstancerRegistration registration) {
    if (open_ || registration.name.empty())
        return Status::rejected;
    if (std::any_of(element_instancers_.begin(), element_instancers_.end(),
                    [&](const auto& item) { return item.name == registration.name; }))
        return Status::rejected;
    element_instancers_.push_back(std::move(registration));
    return Status::ok;
}

Status Backend::load_document(std::string_view path) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    Asset asset;
    auto status = assets_.load(path, asset);
    if (status != Status::ok)
        return status;
    const auto is_lua = asset.path.size() >= 4 && asset.path.ends_with(".lua");
    if (is_lua) {
        status = lua_.load(asset.bytes, asset.path);
        if (status != Status::ok)
            return status;
    } else {
        detail::AssetScope scope(&assets_, runtime_->scope_prefix);
        auto* document = runtime_->context->LoadDocumentFromMemory(asset.bytes, runtime_->scope_prefix + asset.path);
        if (!document)
            return Status::rejected;
        if (runtime_->document)
            runtime_->context->UnloadDocument(runtime_->document);
        runtime_->document = document;
        runtime_->document->Show();
    }
    document_ = std::move(asset);
    return Status::ok;
}

Status Backend::reload_document() {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    if (document_.path.empty())
        return Status::rejected;
    // Linked style sheets and templates are cached process-wide by path.
    Rml::Factory::ClearStyleSheetCache();
    Rml::Factory::ClearTemplateCache();
    const auto path = document_.path;
    return load_document(path);
}

Status Backend::load_font_face(std::string_view path, std::string_view family, bool fallback) {
    return load_font_face(path, family, FontFaceOptions{FontStyle::normal, 0, fallback, 0});
}

Status Backend::load_font_face(std::string_view path, std::string_view family,
                               FontFaceOptions options) {
    if (!open_)
        return Status::not_open;
    if (path.empty() || family.empty() || options.face_index < 0 || options.weight > 1000)
        return Status::rejected;
#if !VELLUM_RMLUI_FONTS_ENABLED
    return Status::rejected;
#else
    Asset asset;
    const auto status = assets_.load(path, asset);
    if (status != Status::ok)
        return status;
    const auto& owned = detail::retain_font_bytes(std::move(asset.bytes));
    const auto* bytes = reinterpret_cast<const Rml::byte*>(owned.data());
    const auto style = options.style == FontStyle::italic ? Rml::Style::FontStyle::Italic
                                                          : Rml::Style::FontStyle::Normal;
    const auto weight = static_cast<Rml::Style::FontWeight>(options.weight);
    const auto loaded =
        Rml::LoadFontFace(Rml::Span<const Rml::byte>(bytes, owned.size()), Rml::String(family),
                          style, weight, options.fallback, options.face_index);
    if (!loaded)
        detail::drop_last_font_bytes();
    return loaded ? Status::ok : Status::rejected;
#endif
}

Status Backend::bind_model(std::string_view name, std::shared_ptr<ModelHost> host,
                           std::vector<std::string> fields) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    if (name.empty() || !host || fields.empty() || runtime_->models.contains(std::string(name)))
        return Status::rejected;
    std::sort(fields.begin(), fields.end());
    if (std::adjacent_find(fields.begin(), fields.end()) != fields.end())
        return Status::rejected;
    for (const auto& field : fields)
        if (field.empty())
            return Status::rejected;

    auto constructor = runtime_->context->CreateDataModel(Rml::String(name));
    if (!constructor)
        return Status::rejected;
    for (const auto& field : fields) {
        const auto field_name = Rml::String(field);
        const auto getter_host = host;
        const auto setter_host = host;
        if (!constructor.BindFunc(
                field_name,
                [getter_host, field](Rml::Variant& value) {
                    ModelValue model_value;
                    if (getter_host->read(field, model_value) != Status::ok)
                        return;
                    switch (model_value.kind) {
                    case ModelValue::Kind::number:
                        value = model_value.number;
                        break;
                    case ModelValue::Kind::boolean:
                        value = model_value.boolean;
                        break;
                    case ModelValue::Kind::string:
                        value = Rml::String(model_value.string);
                        break;
                    }
                },
                [setter_host, field](const Rml::Variant& value) {
                    ModelValue model_value;
                    switch (value.GetType()) {
                    case Rml::Variant::BOOL:
                        model_value = ModelValue::boolean_value(value.Get<bool>());
                        break;
                    case Rml::Variant::STRING:
                        model_value = ModelValue::string_value(value.Get<Rml::String>());
                        break;
                    case Rml::Variant::BYTE:
                    case Rml::Variant::CHAR:
                    case Rml::Variant::FLOAT:
                    case Rml::Variant::DOUBLE:
                    case Rml::Variant::INT:
                    case Rml::Variant::INT64:
                    case Rml::Variant::UINT:
                    case Rml::Variant::UINT64:
                        model_value = ModelValue::number_value(value.Get<double>());
                        break;
                    default:
                        return;
                    }
                    (void)setter_host->write(field, model_value);
                }))
            return Status::rejected;
    }
    runtime_->models.emplace(std::string(name),
                             Runtime::ModelBinding{std::move(host), constructor.GetModelHandle()});
    return Status::ok;
}

Status Backend::dirty_model(std::string_view name, std::string_view field) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    const auto it = runtime_->models.find(std::string(name));
    if (it == runtime_->models.end() || field.empty())
        return Status::rejected;
    it->second.handle.DirtyVariable(Rml::String(field));
    return Status::ok;
}

Status Backend::dispatch_lua(std::string_view callback) {
    if (!open_)
        return Status::not_open;
    return lua_.dispatch(callback);
}

Status Backend::invoke_action(std::string_view action) {
    if (!open_)
        return Status::not_open;
    return actions_ ? actions_->invoke(action) : Status::rejected;
}

Status Backend::process_mouse_move(int x, int y, int key_modifier_state) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessMouseMove(x, y, key_modifier_state);
    return Status::ok;
}

Status Backend::process_mouse_button_down(int button_index, int key_modifier_state) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessMouseButtonDown(button_index, key_modifier_state);
    return Status::ok;
}

Status Backend::process_mouse_button_up(int button_index, int key_modifier_state) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessMouseButtonUp(button_index, key_modifier_state);
    return Status::ok;
}

Status Backend::process_mouse_wheel(float x, float y, int key_modifier_state) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessMouseWheel({x, y}, key_modifier_state);
    return Status::ok;
}

Status Backend::process_mouse_leave() {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessMouseLeave();
    return Status::ok;
}

Status Backend::process_key_down(int key_identifier, int key_modifier_state) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessKeyDown(static_cast<Rml::Input::KeyIdentifier>(key_identifier),
                                      key_modifier_state);
    return Status::ok;
}

Status Backend::process_key_up(int key_identifier, int key_modifier_state) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessKeyUp(static_cast<Rml::Input::KeyIdentifier>(key_identifier),
                                    key_modifier_state);
    return Status::ok;
}

Status Backend::process_text_input(std::string_view text) {
    if (!open_ || !runtime_ || !runtime_->context)
        return Status::not_open;
    if (text.empty())
        return Status::rejected;
    detail::AssetScope scope(&assets_, runtime_->scope_prefix);
    runtime_->context->ProcessTextInput(Rml::String{text});
    return Status::ok;
}

bool Backend::mouse_interacting() const noexcept {
    return open_ && runtime_ && runtime_->context && runtime_->context->IsMouseInteracting();
}

void Backend::render() {
    if (!open_)
        return;
    renderer_.begin_frame(width_, height_);
    if (runtime_ && runtime_->context) {
        detail::AssetScope scope(&assets_, runtime_->scope_prefix);
        runtime_->context->Update();
        runtime_->context->Render();
    }
    renderer_.end_frame();
}

void Backend::close() {
    if (runtime_) {
        if (runtime_->context) {
            detail::AssetScope scope(&assets_, runtime_->scope_prefix);
            Rml::RemoveContext(runtime_->context_name);
            runtime_->context = nullptr;
            runtime_->document = nullptr;
        }
        if (runtime_->holds_runtime) {
            // While other instances keep RmlUi alive, drop this instance's
            // render manager now: it holds a pointer to this instance's render
            // adapter, which is destroyed below. This refreshes the other
            // contexts' font resources, so no instance's assets are in scope.
            if (detail::runtime_references() > 1) {
                detail::AssetScope scope(nullptr);
                Rml::ReleaseRenderManagers();
            }
            detail::release_runtime();
            runtime_->holds_runtime = false;
        }
        runtime_.reset();
    }
    document_ = {};
    open_ = false;
    width_ = height_ = 0;
}

} // namespace vellum::rmlui
