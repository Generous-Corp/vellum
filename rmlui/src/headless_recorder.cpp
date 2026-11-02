#include <vellum/rmlui/backend.hpp>

#include <algorithm>
#include <string>
#include <utility>

namespace vellum::rmlui {

Status HeadlessRecorder::load(std::string_view path, Asset& out) {
    if (std::find(rejected_paths_.begin(), rejected_paths_.end(), path) != rejected_paths_.end())
        return Status::rejected;
    const auto it = std::find_if(assets_.begin(), assets_.end(),
                                 [&](const Asset& asset) { return asset.path == path; });
    if (it == assets_.end())
        return Status::missing_asset;
    out = *it;
    return Status::ok;
}

void HeadlessRecorder::begin_frame(std::uint32_t width, std::uint32_t height) {
    frame_width_ = width;
    frame_height_ = height;
    commands_.clear();
}

void HeadlessRecorder::submit(const DrawCommand& command) {
    commands_.push_back(command);
}
void HeadlessRecorder::end_frame() {}

Status HeadlessRecorder::load(std::string_view source, std::string_view source_name) {
    if (source.empty() || source_name.empty())
        return Status::rejected;
    callbacks_.push_back(std::string(source_name));
    return Status::ok;
}

Status HeadlessRecorder::dispatch(std::string_view callback) {
    if (callback.empty())
        return Status::rejected;
    callbacks_.push_back(std::string(callback));
    return Status::ok;
}

Status HeadlessRecorder::invoke(std::string_view action) {
    if (action.empty())
        return Status::rejected;
    actions_.push_back(std::string(action));
    return Status::ok;
}

Status HeadlessRecorder::register_custom_property(CustomPropertyRegistration registration) {
    if (reject_extensions_ || registration.name.empty() || registration.parser.empty() ||
        registration.default_value.empty())
        return Status::rejected;
    custom_properties_.push_back(std::move(registration));
    return Status::ok;
}

Status HeadlessRecorder::register_element_instancer(ElementInstancerRegistration registration) {
    if (reject_extensions_ || registration.name.empty())
        return Status::rejected;
    element_instancers_.push_back(std::move(registration));
    return Status::ok;
}

void HeadlessRecorder::add_asset(std::string path, std::string bytes) {
    assets_.push_back(Asset{std::move(path), std::move(bytes)});
}

void HeadlessRecorder::reject_path(std::string path) {
    rejected_paths_.push_back(std::move(path));
}

} // namespace vellum::rmlui
