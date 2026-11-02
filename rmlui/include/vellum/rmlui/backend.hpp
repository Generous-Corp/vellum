#pragma once

#include <vellum/rmlui/export.hpp>

#include <array>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

// The RmlUi backend. RmlUi and Lua headers stay behind this interface, so a
// consumer sees only renderer-neutral draw commands, an asset seam and typed
// model/action/extension hosts.
//
// Renderer seam: a RenderInterface receives one DrawCommand per RmlUi
// geometry draw. The soft renderer (VellumRmlUi::SoftRenderer) is one
// implementation; a GPU renderer implements the same three calls.
//
// Threading: RmlUi keeps its file, system and font-engine interfaces in
// process globals and is not thread-safe. Every Backend in a process must be
// driven from the same UI thread.
namespace vellum::rmlui {

enum class Status : std::uint8_t {
    ok,
    already_open,
    not_open,
    missing_asset,
    rejected,
};

struct Asset {
    std::string path;
    std::string bytes;
};

class VELLUM_RMLUI_API AssetProvider {
  public:
    virtual ~AssetProvider();
    virtual Status load(std::string_view path, Asset& out) = 0;
};

struct AssetManifestEntry {
    std::string path;
    std::string sha256;
};

// Returns the lowercase hex SHA-256 digest of its input. The module carries
// no cryptography of its own; the host supplies the digest it already trusts.
using Sha256Hasher = std::function<std::string(std::string_view)>;

// Font metadata kept in this header so callers do not need to include RmlUi
// headers. A zero weight asks RmlUi to discover all weights in a variable
// face; explicit weights must be in RmlUi's [1, 1000] range.
enum class FontStyle : std::uint8_t { normal, italic };

struct FontFaceOptions {
    FontStyle style = FontStyle::normal;
    std::uint16_t weight = 0;
    bool fallback = false;
    int face_index = 0;
};

// Positions are in pixels. Colours are packed premultiplied RGBA
// (red in the high byte), as RmlUi produces them.
struct DrawVertex {
    float x = 0.0f;
    float y = 0.0f;
    float u = 0.0f;
    float v = 0.0f;
    std::uint32_t colour = 0;
};

struct ScissorRect {
    int x = 0;
    int y = 0;
    int width = 0;
    int height = 0;
};

// Reads only regular files inside a declared asset root. The manifest is a
// content closure: a missing hasher, an empty digest or a mismatched digest
// rejects the read.
class VELLUM_RMLUI_API RootedAssetProvider final : public AssetProvider {
  public:
    RootedAssetProvider(std::filesystem::path root, std::vector<AssetManifestEntry> manifest,
                        Sha256Hasher sha256_hex, std::uint64_t max_bytes = 16 * 1024 * 1024);

    Status load(std::string_view path, Asset& out) override;

  private:
    std::filesystem::path root_;
    std::vector<AssetManifestEntry> manifest_;
    Sha256Hasher sha256_hex_;
    std::uint64_t max_bytes_;
};

struct DrawCommand {
    enum class Kind : std::uint8_t { rectangle, texture, text };
    Kind kind = Kind::rectangle;
    // Bounding box of the translated, untransformed geometry.
    float x = 0.0f;
    float y = 0.0f;
    float width = 0.0f;
    float height = 0.0f;
    std::string text;
    // For texture commands this is the asset path of an image RmlUi loaded.
    // An empty path identifies a texture RmlUi generated (font glyphs,
    // gradients); its bytes are then premultiplied RGBA8 rows.
    std::string texture_path{};
    std::shared_ptr<const std::string> texture_bytes{};
    std::uint32_t texture_width = 0;
    std::uint32_t texture_height = 0;
    // Compiled geometry is shared between draws, so vertices are untranslated:
    // a renderer adds (translation_x, translation_y) before the transform.
    float translation_x = 0.0f;
    float translation_y = 0.0f;
    std::shared_ptr<const std::vector<DrawVertex>> vertices{};
    std::shared_ptr<const std::vector<std::uint32_t>> indices{};
    bool scissor_enabled = false;
    ScissorRect scissor{};
    // Column-major 4x4 matrix applied to vertex positions when enabled.
    bool transform_enabled = false;
    std::array<float, 16> transform{};
};

class VELLUM_RMLUI_API RenderInterface {
  public:
    virtual ~RenderInterface();
    virtual void begin_frame(std::uint32_t width, std::uint32_t height) = 0;
    virtual void submit(const DrawCommand& command) = 0;
    virtual void end_frame() = 0;
};

class VELLUM_RMLUI_API LuaInterface {
  public:
    virtual ~LuaInterface();
    virtual Status load(std::string_view source, std::string_view source_name) = 0;
    virtual Status dispatch(std::string_view callback) = 0;
};

class VELLUM_RMLUI_API ActionSink {
  public:
    virtual ~ActionSink();
    virtual Status invoke(std::string_view action) = 0;
};

// Renderer-neutral model seam. Concrete hosts expose their state through this
// interface; the backend owns the RmlUi data-model bindings.
struct ModelValue {
    enum class Kind : std::uint8_t { number, boolean, string };
    Kind kind = Kind::number;
    double number = 0.0;
    bool boolean = false;
    std::string string;

    static ModelValue number_value(double value) {
        ModelValue result;
        result.number = value;
        return result;
    }
    static ModelValue boolean_value(bool value) {
        ModelValue result;
        result.kind = Kind::boolean;
        result.boolean = value;
        return result;
    }
    static ModelValue string_value(std::string value) {
        ModelValue result;
        result.kind = Kind::string;
        result.string = std::move(value);
        return result;
    }
};

// Optional compatibility declarations for skins that use custom RCSS
// properties or element tags. The ExtensionHost implementation owns the RmlUi
// property parser and element-instancer objects. RmlUi's property and
// instancer registries are process-wide, so an ExtensionHost must tolerate a
// registration that an earlier Backend in the same process already made.
struct CustomPropertyRegistration {
    std::string name;
    std::string default_value;
    std::string parser;
    bool inherited = false;
};

struct ElementInstancerRegistration {
    std::string name;
};

class VELLUM_RMLUI_API ExtensionHost {
  public:
    virtual ~ExtensionHost();
    virtual Status register_custom_property(CustomPropertyRegistration registration) = 0;
    virtual Status register_element_instancer(ElementInstancerRegistration registration) = 0;
};

class VELLUM_RMLUI_API ModelHost {
  public:
    virtual ~ModelHost();
    // Called on the owning UI thread while the RmlUi context is active.
    virtual Status read(std::string_view field, ModelValue& out) = 0;
    // Called on the owning UI thread by an RmlUi setter. Implementations must
    // enqueue realtime work rather than touching realtime state directly.
    virtual Status write(std::string_view field, const ModelValue& value) = 0;
};

// One RmlUi context. Any number of Backends may be open in one process: the
// first open() initialises RmlUi, the last close() shuts it down, and file
// reads made while a Backend calls into RmlUi resolve through that Backend's
// own AssetProvider.
class VELLUM_RMLUI_API Backend {
  public:
    Backend(AssetProvider&, RenderInterface&, LuaInterface&, ActionSink* = nullptr,
            ExtensionHost* = nullptr);
    ~Backend();

    Backend(const Backend&) = delete;
    Backend& operator=(const Backend&) = delete;

    Status open(std::uint32_t width, std::uint32_t height);
    Status load_document(std::string_view path);
    // Reloads the current document from the AssetProvider, discarding RmlUi's
    // process-wide style sheet and template caches first so edited linked
    // RCSS and templates are re-read. Intended for development hot reload.
    Status reload_document();
    // Loads a manifest-approved font through the RmlUi font engine. The path
    // is resolved by AssetProvider before it reaches RmlUi. Font faces are
    // process-wide in RmlUi, so a face one Backend loads is visible to every
    // open Backend; the bytes stay alive until the last Backend closes.
    Status load_font_face(std::string_view path, std::string_view family, bool fallback = false);
    Status load_font_face(std::string_view path, std::string_view family, FontFaceOptions options);
    // Queue compatibility declarations before open(). Registration is
    // admitted during open(), after RmlUi is initialised and before a document
    // can be loaded. A rejecting extension host fails open() closed.
    Status register_custom_property(CustomPropertyRegistration registration);
    Status register_element_instancer(ElementInstancerRegistration registration);
    // Create a named RmlUi data model and bind fields on the UI thread. The
    // host is retained until close(); callers publish changes with
    // dirty_model() after a successful state update.
    Status bind_model(std::string_view name, std::shared_ptr<ModelHost> host,
                      std::vector<std::string> fields);
    Status dirty_model(std::string_view name, std::string_view field);
    Status dispatch_lua(std::string_view callback);
    Status invoke_action(std::string_view action);
    Status process_mouse_move(int x, int y, int key_modifier_state = 0);
    Status process_mouse_button_down(int button_index, int key_modifier_state = 0);
    Status process_mouse_button_up(int button_index, int key_modifier_state = 0);
    Status process_mouse_wheel(float x, float y, int key_modifier_state = 0);
    Status process_mouse_leave();
    Status process_key_down(int key_identifier, int key_modifier_state = 0);
    Status process_key_up(int key_identifier, int key_modifier_state = 0);
    Status process_text_input(std::string_view text);
    [[nodiscard]] bool mouse_interacting() const noexcept;
    void render();
    void close();
    [[nodiscard]] bool is_open() const noexcept {
        return open_;
    }

    // Whether this build carries RmlUi's FreeType font engine.
    [[nodiscard]] static bool fonts_enabled() noexcept;
    // Number of Backends currently holding the process-wide RmlUi runtime.
    [[nodiscard]] static std::size_t open_instances() noexcept;

  private:
    struct Runtime;
    AssetProvider& assets_;
    RenderInterface& renderer_;
    LuaInterface& lua_;
    ActionSink* actions_ = nullptr;
    ExtensionHost* extensions_ = nullptr;
    std::uint32_t width_ = 0;
    std::uint32_t height_ = 0;
    bool open_ = false;
    Asset document_;
    std::vector<CustomPropertyRegistration> custom_properties_;
    std::vector<ElementInstancerRegistration> element_instancers_;
    std::unique_ptr<Runtime> runtime_;
};

// Records everything a Backend emits, with no window and no renderer. Used by
// tests and headless tooling to observe the contract directly.
class VELLUM_RMLUI_API HeadlessRecorder final : public AssetProvider,
                                                public RenderInterface,
                                                public LuaInterface,
                                                public ActionSink,
                                                public ExtensionHost {
  public:
    Status load(std::string_view path, Asset& out) override;
    void begin_frame(std::uint32_t width, std::uint32_t height) override;
    void submit(const DrawCommand& command) override;
    void end_frame() override;
    Status load(std::string_view source, std::string_view source_name) override;
    Status dispatch(std::string_view callback) override;
    Status invoke(std::string_view action) override;
    Status register_custom_property(CustomPropertyRegistration registration) override;
    Status register_element_instancer(ElementInstancerRegistration registration) override;

    void add_asset(std::string path, std::string bytes);
    void reject_path(std::string path);
    void reject_extensions(bool reject) noexcept {
        reject_extensions_ = reject;
    }
    [[nodiscard]] const std::vector<DrawCommand>& commands() const noexcept {
        return commands_;
    }
    [[nodiscard]] const std::vector<std::string>& callbacks() const noexcept {
        return callbacks_;
    }
    [[nodiscard]] const std::vector<std::string>& actions() const noexcept {
        return actions_;
    }
    [[nodiscard]] const std::vector<CustomPropertyRegistration>& custom_properties() const noexcept {
        return custom_properties_;
    }
    [[nodiscard]] const std::vector<ElementInstancerRegistration>& element_instancers() const noexcept {
        return element_instancers_;
    }
    [[nodiscard]] std::uint32_t frame_width() const noexcept {
        return frame_width_;
    }
    [[nodiscard]] std::uint32_t frame_height() const noexcept {
        return frame_height_;
    }

  private:
    std::vector<Asset> assets_;
    std::vector<std::string> rejected_paths_;
    std::vector<DrawCommand> commands_;
    std::vector<std::string> callbacks_;
    std::vector<std::string> actions_;
    std::vector<CustomPropertyRegistration> custom_properties_;
    std::vector<ElementInstancerRegistration> element_instancers_;
    bool reject_extensions_ = false;
    std::uint32_t frame_width_ = 0;
    std::uint32_t frame_height_ = 0;
};

} // namespace vellum::rmlui
