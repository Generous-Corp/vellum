#include <vellum/graphics/layer_cache.hpp>

#include <algorithm>
#include <cstring>
#include <limits>
#include <string_view>
#include <unordered_map>
#include <utility>

namespace vellum::graphics {
namespace {

#if defined(__APPLE__) && defined(__aarch64__) && defined(_LIBCPP_VERSION)
// `paint_commands_equal` and `hash_command` must compare and hash every
// PaintCommand field: a field they skip lets a cached image survive a change
// to it. This size changes when a field is added; update both functions, and
// the field-coverage test in layer_cache_test.cpp, then this number.
static_assert(sizeof(PaintCommand) == 392,
              "PaintCommand changed: update paint_commands_equal and hash_command");
static_assert(sizeof(TextStyle) == 56 && sizeof(TextRun) == 80,
              "TextStyle or TextRun changed: update same_style, same_runs and hash_command");
static_assert(sizeof(BoxShadow) == 32, "BoxShadow changed: update same_shadows and hash_command");
static_assert(sizeof(Color) == 16 && sizeof(Affine2D) == 24 && sizeof(CornerRadii) == 16 &&
                  sizeof(Rect) == 16,
              "a PaintCommand value type changed: update the comparisons and hash_command");
#endif

// Frames a subtree waits before it may be cached again after an image was
// dropped unused or the budget refused it; doubles on each repeat.
constexpr std::uint64_t kFirstBackoff = 2;
constexpr std::uint64_t kLongestBackoff = 64;
constexpr std::uint64_t kRefusalBackoff = 8;

bool wasted_build_reason(LayerInvalidation reason) {
    return reason == LayerInvalidation::content || reason == LayerInvalidation::bounds ||
           reason == LayerInvalidation::structure || reason == LayerInvalidation::resource;
}

bool same_float(float a, float b) {
    return std::memcmp(&a, &b, sizeof(float)) == 0;
}

bool same_color(const Color& a, const Color& b) {
    return same_float(a.red, b.red) && same_float(a.green, b.green) &&
           same_float(a.blue, b.blue) && same_float(a.alpha, b.alpha);
}

bool same_rect(const Rect& a, const Rect& b) {
    return same_float(a.x, b.x) && same_float(a.y, b.y) && same_float(a.width, b.width) &&
           same_float(a.height, b.height);
}

bool same_transform(const Affine2D& a, const Affine2D& b) {
    return same_float(a.a, b.a) && same_float(a.b, b.b) && same_float(a.c, b.c) &&
           same_float(a.d, b.d) && same_float(a.tx, b.tx) && same_float(a.ty, b.ty);
}

bool same_radii(const CornerRadii& a, const CornerRadii& b) {
    return same_float(a.top_left, b.top_left) && same_float(a.top_right, b.top_right) &&
           same_float(a.bottom_right, b.bottom_right) &&
           same_float(a.bottom_left, b.bottom_left);
}

bool same_gradients(const std::vector<LinearGradient>& a, const std::vector<LinearGradient>& b) {
    if (a.size() != b.size()) return false;
    for (std::size_t index = 0; index < a.size(); ++index) {
        const auto& left = a[index];
        const auto& right = b[index];
        if (left.kind != right.kind || left.spread != right.spread || left.interpolation != right.interpolation || left.color_space != right.color_space ||
            !same_float(left.angle_degrees, right.angle_degrees) ||
            left.repeating != right.repeating ||
            !same_float(left.repeat_length, right.repeat_length) ||
            !same_float(left.center_x, right.center_x) || !same_float(left.center_y, right.center_y) ||
            !same_float(left.radius_x, right.radius_x) || !same_float(left.radius_y, right.radius_y) ||
            !same_float(left.focal_x, right.focal_x) || !same_float(left.focal_y, right.focal_y) ||
            left.stops.size() != right.stops.size()) {
            return false;
        }
        for (std::size_t stop = 0; stop < left.stops.size(); ++stop) {
            if (!same_float(left.stops[stop].position, right.stops[stop].position) ||
                !same_color(left.stops[stop].color, right.stops[stop].color)) {
                return false;
            }
        }
    }
    return true;
}

bool same_shadows(const std::vector<BoxShadow>& a, const std::vector<BoxShadow>& b) {
    if (a.size() != b.size()) return false;
    for (std::size_t index = 0; index < a.size(); ++index) {
        const auto& left = a[index];
        const auto& right = b[index];
        if (!same_float(left.offset_x, right.offset_x) ||
            !same_float(left.offset_y, right.offset_y) ||
            !same_float(left.blur_radius, right.blur_radius) ||
            !same_float(left.spread_radius, right.spread_radius) ||
            !same_color(left.color, right.color)) {
            return false;
        }
    }
    return true;
}

bool same_style(const TextStyle& a, const TextStyle& b) {
    return a.font_family == b.font_family && a.font_weight == b.font_weight &&
           same_float(a.font_size, b.font_size) &&
           same_float(a.letter_spacing, b.letter_spacing) && same_color(a.color, b.color) &&
           a.underline == b.underline && a.strikethrough == b.strikethrough;
}

bool same_runs(const std::vector<TextRun>& a, const std::vector<TextRun>& b) {
    if (a.size() != b.size()) return false;
    for (std::size_t index = 0; index < a.size(); ++index) {
        if (a[index].text != b[index].text || !same_style(a[index].style, b[index].style)) {
            return false;
        }
    }
    return true;
}

bool same_floats(const std::vector<float>& a, const std::vector<float>& b) {
    return a.size() == b.size() &&
           std::equal(a.begin(), a.end(), b.begin(),
                      [](float left, float right) { return same_float(left, right); });
}

bool same_path(const std::shared_ptr<const PathGeometry>& a,
               const std::shared_ptr<const PathGeometry>& b) {
    if (a == b) return true;
    if (!a || !b) return false;
    return a->verbs == b->verbs && same_floats(a->values, b->values) &&
           a->fill_rule == b->fill_rule && a->antialias == b->antialias &&
           same_color(a->stroke.color, b->stroke.color) &&
           same_float(a->stroke.width, b->stroke.width) &&
           a->stroke.join == b->stroke.join && a->stroke.cap == b->stroke.cap &&
           same_float(a->stroke.miter_limit, b->stroke.miter_limit) &&
           same_floats(a->stroke.dash_array, b->stroke.dash_array) &&
           same_float(a->stroke.dash_offset, b->stroke.dash_offset) &&
           a->stroke.gradient.has_value() == b->stroke.gradient.has_value() &&
           (!a->stroke.gradient || same_gradients({*a->stroke.gradient}, {*b->stroke.gradient}));
}

// Everything except `bounds`, `transform`, `mesh` and `path`.
bool same_image_reference(const std::optional<ImageReference>& a,
                          const std::optional<ImageReference>& b) {
    if (a.has_value() != b.has_value()) return false;
    if (!a) return true;
    if (a->content_hash != b->content_hash || a->fit != b->fit || a->sampling != b->sampling ||
        a->source_rect.has_value() != b->source_rect.has_value()) {
        return false;
    }
    return !a->source_rect || same_rect(*a->source_rect, *b->source_rect);
}

bool same_paint(const PaintCommand& a, const PaintCommand& b) {
    return a.kind == b.kind && a.node_id == b.node_id && same_color(a.fill, b.fill) &&
           same_gradients(a.fill_gradients, b.fill_gradients) &&
           same_shadows(a.box_shadows, b.box_shadows) &&
           same_float(a.corner_radius, b.corner_radius) && a.text == b.text &&
           same_float(a.font_size, b.font_size) && a.font_family == b.font_family &&
           a.font_weight == b.font_weight && same_float(a.letter_spacing, b.letter_spacing) &&
           a.underline == b.underline && a.strikethrough == b.strikethrough &&
           same_runs(a.text_runs, b.text_runs) && same_float(a.opacity, b.opacity) &&
           same_radii(a.corner_radii, b.corner_radii) &&
           a.layer_unbounded == b.layer_unbounded && a.layer_isolated == b.layer_isolated &&
           same_rect(a.clip_rect, b.clip_rect) && a.mask_mode == b.mask_mode &&
           same_image_reference(a.image, b.image) && a.image_asset == b.image_asset;
}

class Hasher final {
public:
    void bytes(const void* data, std::size_t size) {
        const auto* begin = static_cast<const unsigned char*>(data);
        for (std::size_t index = 0; index < size; ++index) {
            value_ = (value_ ^ begin[index]) * 0x100000001B3ULL;
        }
    }
    void number(std::uint64_t number) { bytes(&number, sizeof(number)); }
    void real(float number) { bytes(&number, sizeof(number)); }
    void text(std::string_view text) {
        number(text.size());
        bytes(text.data(), text.size());
    }
    void color(const Color& color) {
        real(color.red);
        real(color.green);
        real(color.blue);
        real(color.alpha);
    }
    [[nodiscard]] std::uint64_t value() const noexcept { return value_; }

private:
    std::uint64_t value_ = 0xCBF29CE484222325ULL;
};

void path(Hasher& hash, const std::shared_ptr<const PathGeometry>& geometry) {
    hash.number(geometry ? 1U : 0U);
    if (!geometry) return;
    hash.number(geometry->verbs.size());
    for (const auto verb : geometry->verbs) hash.number(static_cast<std::uint64_t>(verb));
    hash.number(geometry->values.size());
    for (const float value : geometry->values) hash.real(value);
    hash.number(static_cast<std::uint64_t>(geometry->fill_rule));
    hash.number(geometry->antialias ? 1U : 0U);
    hash.color(geometry->stroke.color);
    hash.real(geometry->stroke.width);
    hash.number(static_cast<std::uint64_t>(geometry->stroke.join));
    hash.number(static_cast<std::uint64_t>(geometry->stroke.cap));
    hash.real(geometry->stroke.miter_limit);
    hash.number(geometry->stroke.dash_array.size());
    for (const float value : geometry->stroke.dash_array) hash.real(value);
    hash.real(geometry->stroke.dash_offset);
    hash.number(geometry->stroke.gradient ? 1U : 0U);
    if (geometry->stroke.gradient) {
        const auto& gradient = *geometry->stroke.gradient;
        hash.number(static_cast<std::uint64_t>(gradient.kind));
        hash.real(gradient.angle_degrees);
        hash.number(gradient.repeating ? 1U : 0U);
        hash.real(gradient.repeat_length);
        hash.number(static_cast<std::uint64_t>(gradient.spread));
        hash.number(static_cast<std::uint64_t>(gradient.interpolation));
        hash.text(gradient.color_space);
        hash.real(gradient.center_x); hash.real(gradient.center_y);
        hash.real(gradient.radius_x); hash.real(gradient.radius_y);
        hash.real(gradient.focal_x); hash.real(gradient.focal_y);
        hash.number(gradient.stops.size());
        for (const auto& stop : gradient.stops) {
            hash.real(stop.position);
            hash.color(stop.color);
        }
    }
}

std::uint64_t hash_command(const PaintCommand& command) {
    Hasher hash;
    hash.number(static_cast<std::uint64_t>(command.kind));
    hash.text(command.node_id);
    hash.real(command.bounds.x);
    hash.real(command.bounds.y);
    hash.real(command.bounds.width);
    hash.real(command.bounds.height);
    hash.color(command.fill);
    hash.number(command.fill_gradients.size());
    for (const auto& gradient : command.fill_gradients) {
        hash.real(gradient.angle_degrees);
        hash.number(gradient.repeating ? 1U : 0U);
        hash.real(gradient.repeat_length);
        hash.number(static_cast<std::uint64_t>(gradient.kind));
        hash.number(static_cast<std::uint64_t>(gradient.spread));
        hash.number(static_cast<std::uint64_t>(gradient.interpolation));
        hash.text(gradient.color_space);
        hash.real(gradient.center_x); hash.real(gradient.center_y);
        hash.real(gradient.radius_x); hash.real(gradient.radius_y);
        hash.real(gradient.focal_x); hash.real(gradient.focal_y);
        hash.number(gradient.stops.size());
        for (const auto& stop : gradient.stops) {
            hash.real(stop.position);
            hash.color(stop.color);
        }
    }
    hash.number(command.box_shadows.size());
    for (const auto& shadow : command.box_shadows) {
        hash.real(shadow.offset_x);
        hash.real(shadow.offset_y);
        hash.real(shadow.blur_radius);
        hash.real(shadow.spread_radius);
        hash.color(shadow.color);
    }
    hash.real(command.corner_radius);
    hash.text(command.text);
    hash.real(command.font_size);
    hash.text(command.font_family);
    hash.number(static_cast<std::uint64_t>(command.font_weight));
    hash.real(command.letter_spacing);
    hash.number((command.underline ? 1U : 0U) | (command.strikethrough ? 2U : 0U) |
                (command.layer_unbounded ? 4U : 0U) | (command.layer_isolated ? 8U : 0U));
    hash.real(command.clip_rect.x);
    hash.real(command.clip_rect.y);
    hash.real(command.clip_rect.width);
    hash.real(command.clip_rect.height);
    hash.number(static_cast<std::uint64_t>(command.mask_mode));
    hash.number(command.text_runs.size());
    for (const auto& run : command.text_runs) {
        hash.text(run.text);
        hash.text(run.style.font_family);
        hash.number(static_cast<std::uint64_t>(run.style.font_weight));
        hash.real(run.style.font_size);
        hash.real(run.style.letter_spacing);
        hash.color(run.style.color);
        hash.number((run.style.underline ? 1U : 0U) | (run.style.strikethrough ? 2U : 0U));
    }
    hash.number(reinterpret_cast<std::uintptr_t>(command.mesh.get()));
    hash.number(command.mesh ? reinterpret_cast<std::uintptr_t>(command.mesh->texture.get())
                             : 0U);
    path(hash, command.path);
    hash.real(command.transform.a);
    hash.real(command.transform.b);
    hash.real(command.transform.c);
    hash.real(command.transform.d);
    hash.real(command.transform.tx);
    hash.real(command.transform.ty);
    hash.real(command.opacity);
    hash.real(command.corner_radii.top_left);
    hash.real(command.corner_radii.top_right);
    hash.real(command.corner_radii.bottom_right);
    hash.real(command.corner_radii.bottom_left);
    hash.number(command.image ? 1U : 0U);
    if (command.image) {
        hash.text(command.image->content_hash);
        hash.number(command.image->source_rect ? 1U : 0U);
        if (command.image->source_rect) {
            hash.real(command.image->source_rect->x);
            hash.real(command.image->source_rect->y);
            hash.real(command.image->source_rect->width);
            hash.real(command.image->source_rect->height);
        }
        hash.number(static_cast<std::uint64_t>(command.image->fit));
        hash.number(static_cast<std::uint64_t>(command.image->sampling));
    }
    hash.number(reinterpret_cast<std::uintptr_t>(command.image_asset.get()));
    return hash.value();
}

bool same_commands(const std::vector<PaintCommand>& cached,
                   const std::vector<PaintCommand>& commands, std::size_t begin,
                   std::size_t end) {
    if (cached.size() != end - begin) return false;
    for (std::size_t index = 0; index < cached.size(); ++index) {
        if (!paint_commands_equal(cached[index], commands[begin + index])) return false;
    }
    return true;
}

}  // namespace

std::string_view to_string(LayerInvalidation reason) noexcept {
    switch (reason) {
        case LayerInvalidation::content: return "content";
        case LayerInvalidation::bounds: return "bounds";
        case LayerInvalidation::structure: return "structure";
        case LayerInvalidation::resource: return "resource";
        case LayerInvalidation::visibility: return "visibility";
        case LayerInvalidation::superseded: return "superseded";
        case LayerInvalidation::resize: return "resize";
        case LayerInvalidation::scale: return "scale";
        case LayerInvalidation::device_lost: return "device_lost";
        case LayerInvalidation::frame_failed: return "frame_failed";
        case LayerInvalidation::host_invalidate: return "host_invalidate";
        case LayerInvalidation::reload: return "reload";
        case LayerInvalidation::policy: return "policy";
    }
    return "unknown";
}

std::string_view to_string(LayerCacheTrigger trigger) noexcept {
    switch (trigger) {
        case LayerCacheTrigger::opacity: return "opacity";
        case LayerCacheTrigger::expensive: return "expensive";
        case LayerCacheTrigger::static_content: return "static_content";
    }
    return "unknown";
}

bool paint_commands_equal(const PaintCommand& a, const PaintCommand& b) {
    return same_paint(a, b) && same_rect(a.bounds, b.bounds) &&
           same_transform(a.transform, b.transform) && a.mesh == b.mesh &&
           same_path(a.path, b.path);
}

LayerInvalidation classify_change(
    const std::vector<PaintCommand>& previous, const std::vector<PaintCommand>& next) {
    if (previous.size() != next.size()) return LayerInvalidation::structure;
    bool geometry = false;
    for (std::size_t index = 0; index < previous.size(); ++index) {
        const auto& before = previous[index];
        const auto& after = next[index];
        if (before.kind != after.kind) return LayerInvalidation::structure;
        if (before.mesh != after.mesh || !same_path(before.path, after.path) ||
            before.image_asset != after.image_asset) {
            return LayerInvalidation::resource;
        }
    }
    for (std::size_t index = 0; index < previous.size(); ++index) {
        const auto& before = previous[index];
        const auto& after = next[index];
        if (!same_paint(before, after)) return LayerInvalidation::content;
        geometry = geometry || !same_rect(before.bounds, after.bounds) ||
                   !same_transform(before.transform, after.transform);
    }
    return geometry ? LayerInvalidation::bounds : LayerInvalidation::content;
}

LayerCache::LayerCache(LayerCachePolicy policy) : policy_(policy) {}

void LayerCache::set_policy(const LayerCachePolicy& policy) {
    invalidate_all(LayerInvalidation::policy);
    entries_.clear();
    policy_ = policy;
}

std::int64_t LayerCache::pixel_budget() const noexcept {
    if (policy_.max_pixels > 0) return policy_.max_pixels;
    constexpr auto largest = std::numeric_limits<std::int64_t>::max();
    return target_pixels_ > largest / 2 ? largest : target_pixels_ * 2;
}

void LayerCache::drop(const std::string& node_id, Entry& entry, LayerInvalidation reason) {
    if (!entry.has_image) return;
    if (entry.inexact_image && entry.hits_since_build == 0U && wasted_build_reason(reason)) {
        // The image changed before it was ever drawn from: back off so a
        // subtree that changes every other frame is not rebuilt each time.
        entry.backoff = entry.backoff == 0U ? kFirstBackoff
                                            : std::min(entry.backoff * 2U, kLongestBackoff);
        entry.blocked_until = plan_ + entry.backoff;
    }
    entry.has_image = false;
    entry.inexact_image = false;
    entry.pixels = 0;
    entry.image_commands.clear();
    entry.image_node_ids.clear();
    ++stats_.invalidations[static_cast<std::size_t>(reason)];
    dropped_.push_back({node_id, reason});
}

std::vector<LayerCache::Layer> LayerCache::plan(
    const std::vector<PaintCommand>& commands, const std::vector<PaintCommandRange>& ranges,
    std::int64_t target_pixels, LayerPlanOptions options) {
    const bool count_frame = options.count_frame;
    ++plan_;
    target_pixels_ = std::max<std::int64_t>(0, target_pixels);
    const auto invalidations = stats_.invalidations;
    stats_ = {};
    stats_.invalidations = invalidations;
    std::vector<Layer> layers;
    if (!policy_.enabled) {
        invalidate_all(LayerInvalidation::policy);
        entries_.clear();
        return layers;
    }

    const bool exact_only = options.exact_only || !policy_.allow_inexact;
    if (exact_only &&
        std::none_of(commands.begin(), commands.end(), [](const PaintCommand& command) {
            return command.kind == PaintCommand::Kind::begin_layer;
        })) {
        // Without a faded group nothing can be cached; skip the bookkeeping.
        for (auto& [id, entry] : entries_) drop(id, entry, LayerInvalidation::superseded);
        entries_.clear();
        reserved_pixels_ = 0;
        reserved_layers_ = 0;
        return layers;
    }

    std::unordered_map<std::string_view, std::size_t> id_counts;
    for (const auto& range : ranges) {
        if (range.node != nullptr && !range.node->id.empty()) ++id_counts[range.node->id];
    }
    std::vector<std::uint64_t> hashes;
    hashes.reserve(commands.size());
    std::vector<std::size_t> draws_before(commands.size() + 1U, 0U);
    for (std::size_t index = 0; index < commands.size(); ++index) {
        hashes.push_back(hash_command(commands[index]));
        draws_before[index + 1U] =
            draws_before[index] + (draws_content(commands[index].kind) ? 1U : 0U);
    }

    std::size_t selected_end = 0;
    // The first range is the scene root's: the retained surface already holds
    // its pixels, so it is never cached.
    for (std::size_t position = 1; position < ranges.size(); ++position) {
        const auto& range = ranges[position];
        if (range.node == nullptr || range.node->id.empty() || range.begin >= range.end ||
            range.end > commands.size() || id_counts[range.node->id] != 1U) {
            continue;
        }
        const std::size_t draws = draws_before[range.end] - draws_before[range.begin];
        if (draws < std::max<std::size_t>(policy_.min_draws, 1U)) continue;
        const auto& id = range.node->id;
        const bool opacity_layer = commands[range.begin].kind == PaintCommand::Kind::begin_layer &&
                                   commands[range.begin].node_id == id &&
                                   commands[range.end - 1U].kind == PaintCommand::Kind::end_layer;
        const std::size_t content_begin = opacity_layer ? range.begin + 1U : range.begin;
        const std::size_t content_end = opacity_layer ? range.end - 1U : range.end;
        ++stats_.candidates;

        Hasher hash;
        hash.number(content_end - content_begin);
        for (auto index = content_begin; index < content_end; ++index) {
            hash.number(hashes[index]);
        }
        auto [found, inserted] = entries_.try_emplace(id);
        auto& entry = found->second;
        if (count_frame) {
            const bool unchanged =
                !inserted && entry.seen_plan + 1U == plan_ && entry.hash == hash.value();
            entry.unchanged_frames = unchanged ? entry.unchanged_frames + 1U : 0U;
            entry.hash = hash.value();
        }
        entry.seen_plan = plan_;
        if (entry.has_image &&
            !same_commands(entry.image_commands, commands, content_begin, content_end)) {
            const std::vector<PaintCommand> next(
                commands.begin() + static_cast<std::ptrdiff_t>(content_begin),
                commands.begin() + static_cast<std::ptrdiff_t>(content_end));
            drop(id, entry, classify_change(entry.image_commands, next));
        }

        if (range.begin < selected_end) {
            drop(id, entry, LayerInvalidation::superseded);
            continue;
        }
        bool chosen = true;
        LayerCacheTrigger trigger = LayerCacheTrigger::opacity;
        if (!entry.has_image && plan_ < entry.blocked_until) {
            chosen = false;
        } else if (opacity_layer) {
            trigger = LayerCacheTrigger::opacity;
        } else if (exact_only) {
            chosen = false;
        } else if (policy_.expensive_draws > 0U && draws >= policy_.expensive_draws &&
                   entry.unchanged_frames >= 1U) {
            trigger = LayerCacheTrigger::expensive;
        } else if (policy_.static_frames > 0U &&
                   entry.unchanged_frames >= policy_.static_frames) {
            trigger = LayerCacheTrigger::static_content;
        } else {
            chosen = false;
        }
        if (!chosen) {
            drop(id, entry, LayerInvalidation::superseded);
            continue;
        }

        const bool build = !entry.has_image;
        if (build) {
            entry.pending_inexact = !opacity_layer;
            entry.pending_commands.assign(
                commands.begin() + static_cast<std::ptrdiff_t>(content_begin),
                commands.begin() + static_cast<std::ptrdiff_t>(content_end));
            entry.pending_node_ids.clear();
            for (auto inner = position; inner < ranges.size(); ++inner) {
                const auto& member = ranges[inner];
                if (inner > position && member.begin >= range.end) break;
                if (member.node != nullptr && !member.node->id.empty()) {
                    entry.pending_node_ids.push_back(member.node->id);
                }
            }
        }
        layers.push_back({
            .node_id = id,
            .begin = range.begin,
            .end = range.end,
            .opacity_layer = opacity_layer,
            .trigger = trigger,
            .build = build,
        });
        selected_end = range.end;
    }

    reserved_pixels_ = 0;
    reserved_layers_ = 0;
    for (auto entry = entries_.begin(); entry != entries_.end();) {
        if (entry->second.seen_plan != plan_) {
            drop(entry->first, entry->second, LayerInvalidation::visibility);
            entry = entries_.erase(entry);
            continue;
        }
        if (entry->second.has_image) {
            reserved_pixels_ += entry->second.pixels;
            ++reserved_layers_;
        }
        ++entry;
    }
    stats_.layers = reserved_layers_;
    stats_.layer_pixels = reserved_pixels_;
    return layers;
}

bool LayerCache::admit(std::string_view node_id, std::int64_t pixels) {
    const auto found = entries_.find(std::string(node_id));
    if (found == entries_.end() || found->second.has_image) return false;
    auto& entry = found->second;
    if (pixels <= 0 || reserved_layers_ >= policy_.max_layers ||
        pixels > pixel_budget() - reserved_pixels_) {
        entry.pending_commands.clear();
        entry.pending_node_ids.clear();
        // Not retried for a while, so a refused subtree is not measured and
        // copied again every frame.
        entry.blocked_until = plan_ + kRefusalBackoff;
        if (pixels > 0) ++stats_.refused_over_budget;
        return false;
    }
    entry.has_image = true;
    entry.inexact_image = entry.pending_inexact;
    entry.hits_since_build = 0;
    entry.pixels = pixels;
    entry.image_commands = std::move(entry.pending_commands);
    entry.image_node_ids = std::move(entry.pending_node_ids);
    entry.pending_commands.clear();
    entry.pending_node_ids.clear();
    reserved_pixels_ += pixels;
    ++reserved_layers_;
    ++stats_.builds;
    stats_.layers = reserved_layers_;
    stats_.layer_pixels = reserved_pixels_;
    return true;
}

void LayerCache::record_hit(std::string_view node_id) {
    const auto found = entries_.find(std::string(node_id));
    if (found == entries_.end() || !found->second.has_image) return;
    ++found->second.hits_since_build;
    found->second.backoff = 0;
    ++stats_.hits;
    stats_.commands_from_cache += found->second.image_commands.size();
}

void LayerCache::abandon(std::string_view node_id, LayerInvalidation reason) {
    const auto found = entries_.find(std::string(node_id));
    if (found == entries_.end()) return;
    auto& entry = found->second;
    if (entry.has_image) {
        reserved_pixels_ -= entry.pixels;
        --reserved_layers_;
        if (stats_.builds > 0U) --stats_.builds;
        drop(found->first, entry, reason);
    }
    entry.pending_commands.clear();
    entry.pending_node_ids.clear();
    stats_.layers = reserved_layers_;
    stats_.layer_pixels = reserved_pixels_;
}

void LayerCache::invalidate_all(LayerInvalidation reason) {
    for (auto& [id, entry] : entries_) {
        if (entry.has_image) drop(id, entry, reason);
        entry.unchanged_frames = 0;
    }
    reserved_pixels_ = 0;
    reserved_layers_ = 0;
    stats_.layers = 0;
    stats_.layer_pixels = 0;
}

void LayerCache::invalidate_node(std::string_view node_id) {
    for (auto& [id, entry] : entries_) {
        if (!entry.has_image) continue;
        const bool contains =
            std::find(entry.image_node_ids.begin(), entry.image_node_ids.end(), node_id) !=
            entry.image_node_ids.end();
        if (contains) {
            reserved_pixels_ -= entry.pixels;
            --reserved_layers_;
            drop(id, entry, LayerInvalidation::host_invalidate);
            entry.unchanged_frames = 0;
        }
    }
    stats_.layers = reserved_layers_;
    stats_.layer_pixels = reserved_pixels_;
}

std::vector<LayerCache::Dropped> LayerCache::take_dropped() {
    return std::exchange(dropped_, {});
}

bool LayerCache::has_image(std::string_view node_id) const {
    const auto found = entries_.find(std::string(node_id));
    return found != entries_.end() && found->second.has_image;
}

}  // namespace vellum::graphics
