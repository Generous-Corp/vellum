#include <vellum/view/property_panel_view.hpp>
#include <vellum/runtime/base64.hpp>

#include <string>

namespace vellum::view {

namespace detail { const char* property_panel_view_svg_b64(); }

namespace {
std::string decode_property_panel_view_svg() {
    if (auto bytes = runtime::base64_decode(detail::property_panel_view_svg_b64()))
        return std::string(bytes->begin(), bytes->end());
    return {};
}
}  // namespace

PropertyPanelView::PropertyPanelView() : DesignFrameView(decode_property_panel_view_svg(), /*elements=*/{}) {}

}  // namespace vellum::view
