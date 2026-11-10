#include <vellum/view/number_box_states_view.hpp>
#include <vellum/runtime/base64.hpp>

#include <string>

namespace vellum::view {

namespace detail { const char* number_box_states_view_svg_b64(); }

namespace {
std::string decode_number_box_states_view_svg() {
    if (auto bytes = runtime::base64_decode(detail::number_box_states_view_svg_b64()))
        return std::string(bytes->begin(), bytes->end());
    return {};
}
}  // namespace

NumberBoxStatesView::NumberBoxStatesView() : DesignFrameView(decode_number_box_states_view_svg(), /*elements=*/{}) {}

}  // namespace vellum::view
