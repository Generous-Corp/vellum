// Compile the public WebView header as an independent consumer translation unit.

#include <vellum/view/web_view.hpp>

int main() {
    vellum::view::WebViewOptions::Resource resource;
    resource.data.push_back(0);
    vellum::view::WebViewMessage message;
    message.payload_json = "null";
    vellum::view::WebViewPanel* panel = nullptr;
    return resource.data.empty() || message.payload_json.empty() || panel != nullptr;
}
