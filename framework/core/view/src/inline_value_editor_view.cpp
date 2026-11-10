#include <vellum/view/inline_value_editor_view.hpp>
#include <vellum/runtime/base64.hpp>

#include <string>

namespace vellum::view {

namespace detail { const char* inline_value_editor_view_svg_b64(); }

namespace {
std::string decode_inline_value_editor_view_svg() {
    if (auto bytes = runtime::base64_decode(detail::inline_value_editor_view_svg_b64()))
        return std::string(bytes->begin(), bytes->end());
    return {};
}
}  // namespace

InlineValueEditorView::InlineValueEditorView() : DesignFrameView(decode_inline_value_editor_view_svg(), /*elements=*/{}) {}

}  // namespace vellum::view
