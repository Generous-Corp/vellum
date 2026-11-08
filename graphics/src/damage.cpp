#include <vellum/graphics/damage.hpp>

#include <algorithm>
#include <cmath>
#include <limits>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace vellum::graphics {
namespace {

constexpr double kPixelLimit = 1 << 30;
constexpr float kShadowBlurExtent = 1.5F;

std::int32_t clamp_pixel(double value) noexcept {
    return static_cast<std::int32_t>(std::clamp(value, -kPixelLimit, kPixelLimit));
}

bool finite_rect(const Rect& rect) noexcept {
    return std::isfinite(rect.x) && std::isfinite(rect.y) && std::isfinite(rect.width) &&
           std::isfinite(rect.height);
}

bool rect_empty(const Rect& rect) noexcept {
    return !(rect.width > 0.0F) || !(rect.height > 0.0F);
}

Rect outset(const Rect& rect, float amount) noexcept {
    return {rect.x - amount, rect.y - amount, rect.width + 2.0F * amount, rect.height + 2.0F * amount};
}

Rect unite(const Rect& a, const Rect& b) noexcept {
    const float left = std::min(a.x, b.x);
    const float top = std::min(a.y, b.y);
    const float right = std::max(a.x + a.width, b.x + b.width);
    const float bottom = std::max(a.y + a.height, b.y + b.height);
    return {left, top, right - left, bottom - top};
}

std::optional<Rect> unite(const std::optional<Rect>& a, const std::optional<Rect>& b) noexcept {
    if (!a) return b;
    if (!b) return a;
    return unite(*a, *b);
}

std::optional<Rect> clip_to(const Rect& rect, const Rect& clip) noexcept {
    const float left = std::max(rect.x, clip.x);
    const float top = std::max(rect.y, clip.y);
    const float right = std::min(rect.x + rect.width, clip.x + clip.width);
    const float bottom = std::min(rect.y + rect.height, clip.y + clip.height);
    if (!(right > left) || !(bottom > top)) return std::nullopt;
    return Rect{left, top, right - left, bottom - top};
}

bool same_rect(const Rect& a, const Rect& b) noexcept {
    return a.x == b.x && a.y == b.y && a.width == b.width && a.height == b.height;
}

bool same_optional_rect(const std::optional<Rect>& a, const std::optional<Rect>& b) noexcept {
    if (a.has_value() != b.has_value()) return false;
    return !a || same_rect(*a, *b);
}

bool same_color(const Color& a, const Color& b) noexcept {
    return a.red == b.red && a.green == b.green && a.blue == b.blue && a.alpha == b.alpha;
}

bool same_gradients(const std::vector<LinearGradient>& a, const std::vector<LinearGradient>& b) {
    return std::equal(a.begin(), a.end(), b.begin(), b.end(),
        [](const LinearGradient& left, const LinearGradient& right) {
            return left.angle_degrees == right.angle_degrees && left.repeating == right.repeating &&
                   left.repeat_length == right.repeat_length &&
                   std::equal(left.stops.begin(), left.stops.end(), right.stops.begin(),
                              right.stops.end(),
                              [](const GradientStop& l, const GradientStop& r) {
                                  return l.position == r.position && same_color(l.color, r.color);
                              });
        });
}

bool same_shadows(const std::vector<BoxShadow>& a, const std::vector<BoxShadow>& b) {
    return std::equal(a.begin(), a.end(), b.begin(), b.end(),
        [](const BoxShadow& left, const BoxShadow& right) {
            return left.offset_x == right.offset_x && left.offset_y == right.offset_y &&
                   left.blur_radius == right.blur_radius &&
                   left.spread_radius == right.spread_radius &&
                   same_color(left.color, right.color);
        });
}

bool same_style(const TextStyle& a, const TextStyle& b) {
    return a.font_family == b.font_family && a.font_weight == b.font_weight &&
           a.font_size == b.font_size && a.letter_spacing == b.letter_spacing &&
           same_color(a.color, b.color) && a.underline == b.underline &&
           a.strikethrough == b.strikethrough;
}

bool same_runs(const std::vector<TextRun>& a, const std::vector<TextRun>& b) {
    return std::equal(a.begin(), a.end(), b.begin(), b.end(),
        [](const TextRun& left, const TextRun& right) {
            return left.text == right.text && same_style(left.style, right.style);
        });
}

std::optional<Rect> box_paint_bounds(const SceneNode& node, const Rect& absolute) {
    std::optional<Rect> painted;
    if (!rect_empty(absolute)) painted = absolute;
    for (const auto& shadow : node.box_shadows) {
        const Rect moved{absolute.x + shadow.offset_x, absolute.y + shadow.offset_y,
                         absolute.width, absolute.height};
        const float blur = std::max(0.0F, shadow.blur_radius) * kShadowBlurExtent;
        const Rect spread = outset(moved, shadow.spread_radius + blur);
        if (!rect_empty(spread)) painted = unite(painted, spread);
    }
    return painted;
}

std::optional<Rect> text_paint_bounds(const SceneNode& node, const Rect& absolute) {
    float largest = std::max(1.0F, node.font_size);
    for (const auto& run : node.text_runs) {
        largest = std::max(largest, run.style.font_size);
    }
    const Rect normalized{absolute.x, absolute.y, std::max(0.0F, absolute.width),
                          std::max(0.0F, absolute.height)};
    return outset(normalized, largest);
}

std::optional<Rect> mesh_paint_bounds(const TriangleMesh& mesh, const Rect& absolute) {
    std::optional<Rect> painted;
    for (const auto& vertex : mesh.vertices) {
        if (!std::isfinite(vertex.x) || !std::isfinite(vertex.y)) continue;
        const Rect point{absolute.x + vertex.x, absolute.y + vertex.y, 0.0F, 0.0F};
        painted = unite(painted, point);
    }
    if (!painted) return std::nullopt;
    painted = outset(*painted, 0.5F);
    if (mesh.clip_enabled) {
        const Rect clip{absolute.x + mesh.clip.x, absolute.y + mesh.clip.y,
                        std::max(0.0F, mesh.clip.width), std::max(0.0F, mesh.clip.height)};
        return clip_to(*painted, clip);
    }
    return painted;
}

const DamagePropagation& default_propagation() {
    static const DamagePropagation instance;
    return instance;
}

struct NodeRecord final {
    const SceneNode* node = nullptr;
    std::string parent_key;
    Rect absolute{};
    std::optional<Rect> damage;
    std::size_t first = 0;
    std::size_t last = 0;
};

class SceneIndex final {
public:
    SceneIndex(const Scene& scene, const DamagePropagation& propagation)
        : propagation_(propagation) {
        walk(scene.root, std::string{}, 0, 0.0F, 0.0F, std::nullopt);
    }

    template <typename Visit>
    void for_each_in_subtree(const NodeRecord& record, Visit&& visit) const {
        for (std::size_t position = record.first; position < record.last; ++position) {
            visit(records_.at(order_[position]));
        }
    }

    [[nodiscard]] const NodeRecord* find(const std::string& key) const {
        const auto found = records_.find(key);
        return found == records_.end() ? nullptr : &found->second;
    }

    [[nodiscard]] const NodeRecord* find_by_id(std::string_view id) const {
        for (const auto& key : order_) {
            const auto& record = records_.at(key);
            if (record.node->id == id) return &record;
        }
        return nullptr;
    }

    [[nodiscard]] const std::vector<std::string>& order() const noexcept { return order_; }
    [[nodiscard]] const std::unordered_map<std::string, NodeRecord>& records() const noexcept {
        return records_;
    }
    [[nodiscard]] const std::vector<std::string>& children(const std::string& parent) const {
        static const std::vector<std::string> none;
        const auto found = children_.find(parent);
        return found == children_.end() ? none : found->second;
    }

private:
    void walk(const SceneNode& node, const std::string& parent_key, std::size_t index,
                             float parent_x, float parent_y, std::optional<Rect> clip) {
        std::string key = make_key(node, parent_key, index);
        const Rect absolute{parent_x + node.bounds.x, parent_y + node.bounds.y, node.bounds.width,
                            node.bounds.height};
        if (const auto own_clip = propagation_.clip_bounds(node, absolute)) {
            if (clip) {
                const auto narrowed = clip_to(*clip, *own_clip);
                clip = narrowed.value_or(Rect{own_clip->x, own_clip->y, 0.0F, 0.0F});
            } else {
                clip = own_clip;
            }
        }

        std::optional<Rect> damage = node_paint_bounds(node, absolute);
        if (damage) {
            const float radius = propagation_.effect_radius(node);
            if (std::isfinite(radius) && radius > 0.0F) damage = outset(*damage, radius);
            if (clip) damage = clip_to(*damage, *clip);
        }

        const std::size_t first = order_.size();
        order_.push_back(key);
        children_[parent_key].push_back(key);
        for (std::size_t child = 0; child < node.children.size(); ++child) {
            walk(node.children[child], key, child, absolute.x, absolute.y, clip);
        }
        records_.emplace(key, NodeRecord{&node, parent_key, absolute, damage, first, order_.size()});
    }

    std::string make_key(const SceneNode& node, const std::string& parent_key, std::size_t index) {
        if (!node.id.empty() && used_.insert(node.id).second) return node.id;
        std::string key = parent_key + "\x1f#" + std::to_string(index);
        while (!used_.insert(key).second) key += "'";
        return key;
    }

    const DamagePropagation& propagation_;
    std::unordered_map<std::string, NodeRecord> records_;
    std::unordered_map<std::string, std::vector<std::string>> children_;
    std::unordered_set<std::string> used_;
    std::vector<std::string> order_;
};

void add_logical(DamageRegion& region, const std::optional<Rect>& rect, float scale) {
    if (!rect) return;
    if (!finite_rect(*rect)) {
        region.add_full_frame();
        return;
    }
    const auto pixels = to_pixel_rect(*rect, scale);
    if (!pixels) {
        region.add_full_frame();
        return;
    }
    if (pixels->empty()) return;
    const std::int32_t margin = std::max<std::int32_t>(0, region.policy().antialias_margin);
    region.add({pixels->left - margin, pixels->top - margin, pixels->right + margin,
                pixels->bottom + margin});
}

void add_subtree(DamageRegion& region, const SceneIndex& index, const NodeRecord& record, float scale) {
    index.for_each_in_subtree(record, [&](const NodeRecord& member) {
        add_logical(region, member.damage, scale);
    });
}

std::vector<std::string> shared_children(const SceneIndex& from, const SceneIndex& other,
                                         const std::string& parent) {
    std::vector<std::string> shared;
    for (const auto& key : from.children(parent)) {
        const auto* record = other.find(key);
        if (record != nullptr && record->parent_key == parent) shared.push_back(key);
    }
    return shared;
}

std::vector<PixelRect> subtract(const PixelRect& from, const PixelRect& cut) {
    if (!overlaps(from, cut)) return {from};
    const PixelRect inner = intersection(from, cut);
    std::vector<PixelRect> pieces;
    if (inner.top > from.top) pieces.push_back({from.left, from.top, from.right, inner.top});
    if (inner.bottom < from.bottom) pieces.push_back({from.left, inner.bottom, from.right, from.bottom});
    if (inner.left > from.left) pieces.push_back({from.left, inner.top, inner.left, inner.bottom});
    if (inner.right < from.right) pieces.push_back({inner.right, inner.top, from.right, inner.bottom});
    return pieces;
}

}  // namespace

PixelRect intersection(const PixelRect& a, const PixelRect& b) noexcept {
    const PixelRect result{std::max(a.left, b.left), std::max(a.top, b.top),
                           std::min(a.right, b.right), std::min(a.bottom, b.bottom)};
    return result.empty() ? PixelRect{} : result;
}

PixelRect bounding_union(const PixelRect& a, const PixelRect& b) noexcept {
    if (a.empty()) return b.empty() ? PixelRect{} : b;
    if (b.empty()) return a;
    return {std::min(a.left, b.left), std::min(a.top, b.top), std::max(a.right, b.right),
            std::max(a.bottom, b.bottom)};
}

bool overlaps(const PixelRect& a, const PixelRect& b) noexcept {
    return !a.empty() && !b.empty() && a.left < b.right && b.left < a.right && a.top < b.bottom &&
           b.top < a.bottom;
}

std::optional<PixelRect> to_pixel_rect(const Rect& logical, float scale) noexcept {
    if (!finite_rect(logical) || !std::isfinite(scale) || !(scale > 0.0F)) return std::nullopt;
    if (rect_empty(logical)) return PixelRect{};
    const double s = scale;
    return PixelRect{
        clamp_pixel(std::floor(static_cast<double>(logical.x) * s)),
        clamp_pixel(std::floor(static_cast<double>(logical.y) * s)),
        clamp_pixel(std::ceil((static_cast<double>(logical.x) + logical.width) * s)),
        clamp_pixel(std::ceil((static_cast<double>(logical.y) + logical.height) * s)),
    };
}

PixelRect frame_rect(const Scene& scene, float scale) noexcept {
    if (!std::isfinite(scene.width) || !std::isfinite(scene.height) || !std::isfinite(scale) ||
        !(scale > 0.0F) || !(scene.width > 0.0F) || !(scene.height > 0.0F)) {
        return {};
    }
    return {0, 0, clamp_pixel(std::ceil(static_cast<double>(scene.width) * scale)),
            clamp_pixel(std::ceil(static_cast<double>(scene.height) * scale))};
}

DamageRegion::DamageRegion(PixelRect frame, DamagePolicy policy)
    : frame_(frame.empty() ? PixelRect{} : frame), policy_(policy) {
    policy_.max_rects = std::max<std::size_t>(1, policy_.max_rects);
}

void DamageRegion::add(const PixelRect& rect) {
    const PixelRect clipped = intersection(rect, frame_);
    if (clipped.empty()) return;
    ++input_rects_;
    if (is_full_frame()) return;
    append_disjoint(clipped);
    peak_before_coalescing_ = std::max(peak_before_coalescing_, rects_.size());
    if (rects_.size() > policy_.max_rects) coalesce();
    apply_threshold();
}

void DamageRegion::add(const DamageRegion& other) {
    for (const auto& rect : other.rects_) add(rect);
    collapsed_ = collapsed_ || (other.collapsed_ && is_full_frame());
}

void DamageRegion::add_full_frame() {
    if (frame_.empty()) return;
    ++input_rects_;
    rects_.assign(1, frame_);
    peak_before_coalescing_ = std::max<std::size_t>(peak_before_coalescing_, 1);
}

void DamageRegion::intersect(const PixelRect& rect) {
    std::vector<PixelRect> kept;
    for (const auto& existing : rects_) {
        const PixelRect overlap = intersection(existing, rect);
        if (!overlap.empty()) kept.push_back(overlap);
    }
    rects_ = std::move(kept);
}

void DamageRegion::intersect(const DamageRegion& other) {
    std::vector<PixelRect> kept;
    for (const auto& mine : rects_) {
        for (const auto& theirs : other.rects_) {
            const PixelRect overlap = intersection(mine, theirs);
            if (!overlap.empty()) kept.push_back(overlap);
        }
    }
    rects_ = std::move(kept);
    peak_before_coalescing_ = std::max(peak_before_coalescing_, rects_.size());
    if (rects_.size() > policy_.max_rects) coalesce();
}

void DamageRegion::clear() {
    rects_.clear();
    input_rects_ = 0;
    peak_before_coalescing_ = 0;
    coalescing_passes_ = 0;
    collapsed_ = false;
}

bool DamageRegion::is_full_frame() const noexcept {
    return !frame_.empty() && area() == frame_.area();
}

bool DamageRegion::intersects(const PixelRect& rect) const noexcept {
    return std::any_of(rects_.begin(), rects_.end(),
                       [&rect](const PixelRect& existing) { return overlaps(existing, rect); });
}

std::int64_t DamageRegion::area() const noexcept {
    std::int64_t total = 0;
    for (const auto& rect : rects_) total += rect.area();
    return total;
}

PixelRect DamageRegion::bounds() const noexcept {
    PixelRect result{};
    for (const auto& rect : rects_) result = bounding_union(result, rect);
    return result;
}

DamageStats DamageRegion::stats() const noexcept {
    DamageStats stats;
    stats.area_pixels = area();
    stats.frame_area_pixels = frame_.area();
    stats.frame_fraction = stats.frame_area_pixels > 0
        ? static_cast<double>(stats.area_pixels) / static_cast<double>(stats.frame_area_pixels)
        : 0.0;
    stats.input_rects = input_rects_;
    stats.rects_before_coalescing = std::max(peak_before_coalescing_, rects_.size());
    stats.rects_after_coalescing = rects_.size();
    stats.coalescing_passes = coalescing_passes_;
    stats.collapsed_to_full_frame = collapsed_;
    return stats;
}

void DamageRegion::append_disjoint(const PixelRect& rect) {
    std::vector<PixelRect> pieces{rect};
    for (const auto& existing : rects_) {
        std::vector<PixelRect> remaining;
        for (const auto& piece : pieces) {
            auto cut = subtract(piece, existing);
            remaining.insert(remaining.end(), cut.begin(), cut.end());
        }
        pieces = std::move(remaining);
        if (pieces.empty()) return;
    }
    rects_.insert(rects_.end(), pieces.begin(), pieces.end());
}

void DamageRegion::coalesce() {
    ++coalescing_passes_;
    while (rects_.size() > policy_.max_rects) {
        std::size_t best_a = 0;
        std::size_t best_b = 1;
        std::int64_t best_waste = std::numeric_limits<std::int64_t>::max();
        for (std::size_t a = 0; a < rects_.size(); ++a) {
            for (std::size_t b = a + 1; b < rects_.size(); ++b) {
                const std::int64_t waste =
                    bounding_union(rects_[a], rects_[b]).area() - rects_[a].area() - rects_[b].area();
                if (waste < best_waste) {
                    best_waste = waste;
                    best_a = a;
                    best_b = b;
                }
            }
        }
        PixelRect merged = bounding_union(rects_[best_a], rects_[best_b]);
        rects_.erase(rects_.begin() + static_cast<std::ptrdiff_t>(best_b));
        rects_.erase(rects_.begin() + static_cast<std::ptrdiff_t>(best_a));
        bool grew = true;
        while (grew) {
            grew = false;
            for (auto it = rects_.begin(); it != rects_.end();) {
                if (overlaps(*it, merged)) {
                    merged = bounding_union(merged, *it);
                    it = rects_.erase(it);
                    grew = true;
                } else {
                    ++it;
                }
            }
        }
        rects_.push_back(merged);
    }
}

void DamageRegion::apply_threshold() {
    const std::int64_t frame_area = frame_.area();
    if (frame_area == 0 || rects_.empty()) return;
    if (rects_.size() == 1 && rects_.front() == frame_) return;
    if (static_cast<double>(area()) >= policy_.full_repaint_fraction * static_cast<double>(frame_area)) {
        rects_.assign(1, frame_);
        collapsed_ = true;
    }
}

void visit_absolute_nodes(const Scene& scene, const std::function<void(const AbsoluteNode&)>& visit) {
    const std::function<void(const SceneNode&, const SceneNode*, float, float, std::size_t)> walk =
        [&](const SceneNode& node, const SceneNode* parent, float parent_x, float parent_y,
            std::size_t depth) {
            const Rect absolute{parent_x + node.bounds.x, parent_y + node.bounds.y,
                                node.bounds.width, node.bounds.height};
            visit({&node, parent, absolute, depth});
            for (const auto& child : node.children) {
                walk(child, &node, absolute.x, absolute.y, depth + 1);
            }
        };
    walk(scene.root, nullptr, 0.0F, 0.0F, 0);
}

float DamagePropagation::effect_radius(const SceneNode&) const {
    return 0.0F;
}

std::optional<Rect> DamagePropagation::clip_bounds(const SceneNode&, const Rect&) const {
    return std::nullopt;
}

std::optional<Rect> node_paint_bounds(const SceneNode& node, const Rect& absolute_bounds) {
    switch (node.kind) {
    case SceneNode::Kind::group:
        return std::nullopt;
    case SceneNode::Kind::rectangle:
        return box_paint_bounds(node, absolute_bounds);
    case SceneNode::Kind::custom:
        return rect_empty(absolute_bounds) ? std::nullopt : std::optional<Rect>(absolute_bounds);
    case SceneNode::Kind::text: {
        if (node.text.empty() && node.text_runs.empty()) return std::nullopt;
        std::optional<Rect> painted = text_paint_bounds(node, absolute_bounds);
        if (!node.fill_gradients.empty() || !node.box_shadows.empty()) {
            painted = unite(painted, box_paint_bounds(node, absolute_bounds));
        }
        return painted;
    }
    case SceneNode::Kind::mesh:
        return node.mesh ? mesh_paint_bounds(*node.mesh, absolute_bounds) : std::nullopt;
    }
    return std::nullopt;
}

bool paint_differs(const SceneNode& previous, const SceneNode& next) {
    if (previous.kind != next.kind) return true;
    switch (next.kind) {
    case SceneNode::Kind::group:
        return false;
    case SceneNode::Kind::rectangle:
        return !same_color(previous.fill, next.fill) ||
               !same_gradients(previous.fill_gradients, next.fill_gradients) ||
               !same_shadows(previous.box_shadows, next.box_shadows) ||
               previous.corner_radius != next.corner_radius;
    case SceneNode::Kind::text:
        return previous.text != next.text || !same_runs(previous.text_runs, next.text_runs) ||
               !same_color(previous.fill, next.fill) || previous.font_size != next.font_size ||
               previous.font_family != next.font_family ||
               previous.font_weight != next.font_weight ||
               previous.letter_spacing != next.letter_spacing ||
               previous.underline != next.underline ||
               previous.strikethrough != next.strikethrough ||
               !same_gradients(previous.fill_gradients, next.fill_gradients) ||
               !same_shadows(previous.box_shadows, next.box_shadows) ||
               previous.corner_radius != next.corner_radius;
    case SceneNode::Kind::mesh:
        return previous.mesh != next.mesh;
    case SceneNode::Kind::custom:
        return previous.custom_component != next.custom_component ||
               previous.custom_properties_json != next.custom_properties_json;
    }
    return true;
}

DamageRegion compute_scene_damage(const Scene& previous, const Scene& next, float scale,
                                  DamagePolicy policy, const DamagePropagation* propagation) {
    const PixelRect frame = frame_rect(next, scale);
    DamageRegion region(frame, policy);
    if (frame != frame_rect(previous, scale) || !same_color(previous.background, next.background)) {
        region.add_full_frame();
        return region;
    }
    const DamagePropagation& effects = propagation != nullptr ? *propagation : default_propagation();
    const SceneIndex before(previous, effects);
    const SceneIndex after(next, effects);

    for (const auto& key : after.order()) {
        const NodeRecord& now = after.records().at(key);
        const NodeRecord* was = before.find(key);
        if (was == nullptr) {
            add_logical(region, now.damage, scale);
            continue;
        }
        if (was->parent_key != now.parent_key) {
            add_subtree(region, before, *was, scale);
            add_subtree(region, after, now, scale);
            continue;
        }
        if (!same_rect(was->absolute, now.absolute) || paint_differs(*was->node, *now.node) ||
            !same_optional_rect(was->damage, now.damage)) {
            add_logical(region, was->damage, scale);
            add_logical(region, now.damage, scale);
        }
    }
    for (const auto& key : before.order()) {
        if (after.find(key) == nullptr) add_logical(region, before.records().at(key).damage, scale);
    }

    for (const auto& parent : after.order()) {
        const auto now_order = shared_children(after, before, parent);
        const auto was_order = shared_children(before, after, parent);
        if (now_order == was_order) continue;
        for (std::size_t index = 0; index < now_order.size(); ++index) {
            if (index < was_order.size() && was_order[index] == now_order[index]) continue;
            add_subtree(region, before, *before.find(now_order[index]), scale);
            add_subtree(region, after, *after.find(now_order[index]), scale);
        }
    }
    return region;
}

std::optional<InvalidationReason> parse_invalidation_reason(std::string_view reason) noexcept {
    if (reason == "state") return InvalidationReason::state;
    if (reason == "timer") return InvalidationReason::timer;
    if (reason == "promise") return InvalidationReason::promise;
    if (reason == "service") return InvalidationReason::service;
    if (reason == "reload") return InvalidationReason::reload;
    return std::nullopt;
}

DamageTracker::DamageTracker(DamagePolicy policy, std::shared_ptr<const DamagePropagation> propagation)
    : policy_(policy), propagation_(std::move(propagation)), pending_(PixelRect{}, policy) {}

void DamageTracker::commit(const Scene& next, float scale) {
    const PixelRect frame = frame_rect(next, scale);
    if (!committed_ || scale != scale_ || frame != pending_.frame()) {
        pending_ = DamageRegion(frame, policy_);
        pending_.add_full_frame();
    } else {
        pending_.add(compute_scene_damage(*committed_, next, scale, policy_, propagation_.get()));
    }
    committed_ = next;
    scale_ = scale;
    last_stats_ = pending_.stats();
}

void DamageTracker::mark_dirty(std::string_view node_id) {
    if (!committed_) return;
    const DamagePropagation& effects = propagation_ ? *propagation_ : default_propagation();
    const SceneIndex index(*committed_, effects);
    const NodeRecord* record = index.find_by_id(node_id);
    if (record == nullptr) {
        pending_.add_full_frame();
    } else {
        add_subtree(pending_, index, *record, scale_);
    }
    last_stats_ = pending_.stats();
}

void DamageTracker::mark_dirty_all(InvalidationReason reason, std::string_view node_id) {
    if (reason == InvalidationReason::reload || node_id.empty()) {
        pending_.add_full_frame();
        last_stats_ = pending_.stats();
        return;
    }
    mark_dirty(node_id);
}

DamageRegion DamageTracker::take_damage() {
    DamageRegion taken = std::move(pending_);
    last_stats_ = taken.stats();
    reset_pending();
    return taken;
}

void DamageTracker::reset_pending() {
    pending_ = DamageRegion(frame_rect(committed_ ? *committed_ : Scene{}, scale_), policy_);
}

}  // namespace vellum::graphics
