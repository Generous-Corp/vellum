#include <vellum/graphics/damage.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/path_geometry.hpp>

#include <algorithm>
#include <bit>
#include <cmath>
#include <limits>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>

namespace vellum::graphics {
namespace {

constexpr double kPixelLimit = kMaxPixelCoordinate;
constexpr float kShadowBlurExtent = 1.5F;
constexpr float kLineHeightFactor = 1.4F;

std::int32_t clamp_pixel(double value) noexcept {
    return static_cast<std::int32_t>(std::clamp(value, -kPixelLimit, kPixelLimit));
}

bool same_float(float a, float b) noexcept {
    return std::bit_cast<std::uint32_t>(a) == std::bit_cast<std::uint32_t>(b);
}

PixelRect clamp_rect(const PixelRect& rect) noexcept {
    const auto limit = [](std::int32_t value) {
        return std::clamp(value, -kMaxPixelCoordinate, kMaxPixelCoordinate);
    };
    return {limit(rect.left), limit(rect.top), limit(rect.right), limit(rect.bottom)};
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
    return same_float(a.x, b.x) && same_float(a.y, b.y) && same_float(a.width, b.width) &&
           same_float(a.height, b.height);
}

bool same_optional_rect(const std::optional<Rect>& a, const std::optional<Rect>& b) noexcept {
    if (a.has_value() != b.has_value()) return false;
    return !a || same_rect(*a, *b);
}

bool same_color(const Color& a, const Color& b) noexcept {
    return same_float(a.red, b.red) && same_float(a.green, b.green) && same_float(a.blue, b.blue) &&
           same_float(a.alpha, b.alpha);
}

bool same_gradients(const std::vector<LinearGradient>& a, const std::vector<LinearGradient>& b) {
    return std::equal(a.begin(), a.end(), b.begin(), b.end(),
        [](const LinearGradient& left, const LinearGradient& right) {
            return same_float(left.angle_degrees, right.angle_degrees) &&
                   left.repeating == right.repeating &&
                   same_float(left.repeat_length, right.repeat_length) &&
                   std::equal(left.stops.begin(), left.stops.end(), right.stops.begin(),
                              right.stops.end(),
                              [](const GradientStop& l, const GradientStop& r) {
                                  return same_float(l.position, r.position) &&
                                         same_color(l.color, r.color);
                              });
        });
}

bool same_shadows(const std::vector<BoxShadow>& a, const std::vector<BoxShadow>& b) {
    return std::equal(a.begin(), a.end(), b.begin(), b.end(),
        [](const BoxShadow& left, const BoxShadow& right) {
            return same_float(left.offset_x, right.offset_x) &&
                   same_float(left.offset_y, right.offset_y) &&
                   same_float(left.blur_radius, right.blur_radius) &&
                   same_float(left.spread_radius, right.spread_radius) &&
                   same_color(left.color, right.color);
        });
}

bool same_style(const TextStyle& a, const TextStyle& b) {
    return a.font_family == b.font_family && a.font_weight == b.font_weight &&
           same_float(a.font_size, b.font_size) && same_float(a.letter_spacing, b.letter_spacing) &&
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

std::optional<Rect> path_paint_bounds(const SceneNode& node, const Rect& absolute,
                                      const Rect& frame) {
    if (!node.path || !path_is_valid(*node.path)) return frame;
    if (node.path->verbs.empty()) return std::nullopt;
    const auto control = path_control_bounds(*node.path);
    if (!control) return std::nullopt;
    const Rect moved{absolute.x + control->x, absolute.y + control->y, control->width,
                     control->height};
    return outset(moved, path_stroke_outset(node.path->stroke));
}

bool same_floats(const std::vector<float>& a, const std::vector<float>& b) {
    return std::equal(a.begin(), a.end(), b.begin(), b.end(),
                      [](float left, float right) { return same_float(left, right); });
}

bool same_path(const std::shared_ptr<const PathGeometry>& left,
               const std::shared_ptr<const PathGeometry>& right) {
    if (left == right) return true;
    if (!left || !right) return false;
    const PathGeometry& a = *left;
    const PathGeometry& b = *right;
    return a.verbs == b.verbs && same_floats(a.values, b.values) && a.fill_rule == b.fill_rule &&
           a.antialias == b.antialias && same_color(a.stroke.color, b.stroke.color) &&
           same_float(a.stroke.width, b.stroke.width) && a.stroke.join == b.stroke.join &&
           a.stroke.cap == b.stroke.cap && same_float(a.stroke.miter_limit, b.stroke.miter_limit) &&
           same_floats(a.stroke.dash_array, b.stroke.dash_array) &&
           same_float(a.stroke.dash_offset, b.stroke.dash_offset);
}

const DamagePropagation& default_propagation() {
    static const DamagePropagation instance;
    return instance;
}

SceneNode paint_fields(const SceneNode& node) {
    SceneNode copy;
    copy.id = node.id;
    copy.kind = node.kind;
    copy.bounds = node.bounds;
    copy.fill = node.fill;
    copy.fill_gradients = node.fill_gradients;
    copy.box_shadows = node.box_shadows;
    copy.corner_radius = node.corner_radius;
    copy.text = node.text;
    copy.font_size = node.font_size;
    copy.font_family = node.font_family;
    copy.font_weight = node.font_weight;
    copy.letter_spacing = node.letter_spacing;
    copy.underline = node.underline;
    copy.strikethrough = node.strikethrough;
    copy.text_runs = node.text_runs;
    copy.custom_component = node.custom_component;
    copy.custom_properties_json = node.custom_properties_json;
    copy.mesh = node.mesh;
    copy.path = node.path;
    copy.clip = node.clip;
    copy.opacity = node.opacity;
    copy.transform = node.transform;
    copy.mask = node.mask;
    copy.isolate = node.isolate;
    return copy;
}

bool same_radii(const CornerRadii& a, const CornerRadii& b) noexcept {
    return same_float(a.top_left, b.top_left) && same_float(a.top_right, b.top_right) &&
           same_float(a.bottom_right, b.bottom_right) &&
           same_float(a.bottom_left, b.bottom_left);
}

bool same_clip(const std::optional<NodeClip>& a, const std::optional<NodeClip>& b) {
    if (a.has_value() != b.has_value()) return false;
    if (!a) return true;
    return same_float(a->rect.x, b->rect.x) && same_float(a->rect.y, b->rect.y) &&
           same_float(a->rect.width, b->rect.width) &&
           same_float(a->rect.height, b->rect.height) && same_radii(a->radii, b->radii) &&
           a->clips_own_paint == b->clips_own_paint && same_path(a->path, b->path);
}

bool same_transform(const std::optional<Affine2D>& a, const std::optional<Affine2D>& b) {
    const auto x = a.value_or(Affine2D{});
    const auto y = b.value_or(Affine2D{});
    return same_float(x.a, y.a) && same_float(x.b, y.b) && same_float(x.c, y.c) &&
           same_float(x.d, y.d) && same_float(x.tx, y.tx) && same_float(x.ty, y.ty);
}

}  // namespace

namespace damage_detail {

struct NodeRecord final {
    std::string key;
    std::string parent_key;
    SceneNode paint;
    Rect absolute{};
    std::optional<Rect> damage;
    std::size_t subtree_end = 0;
};

}  // namespace damage_detail

using damage_detail::NodeRecord;

class DamageTracker::Snapshot final {
public:
    Snapshot(const Scene& scene, float scale, const DamagePropagation& propagation)
        : width_(scene.width), height_(scene.height), scale_(scale), background_(scene.background),
          version_(scene.scene_version), frame_(frame_rect(scene, scale)) {
        walk(propagation, scene.root, std::string{}, 0, 0.0F, 0.0F, std::nullopt, false);
    }

    [[nodiscard]] const NodeRecord* find(const std::string& key) const {
        const auto found = by_key_.find(key);
        return found == by_key_.end() ? nullptr : &records_[found->second];
    }

    [[nodiscard]] const std::vector<std::size_t>* find_id(std::string_view id) const {
        const auto found = by_id_.find(std::string(id));
        return found == by_id_.end() ? nullptr : &found->second;
    }

    [[nodiscard]] const std::vector<NodeRecord>& records() const noexcept { return records_; }
    [[nodiscard]] const NodeRecord& at(std::size_t position) const { return records_[position]; }
    [[nodiscard]] const PixelRect& frame() const noexcept { return frame_; }
    [[nodiscard]] float scale() const noexcept { return scale_; }

    [[nodiscard]] bool same_canvas(const Snapshot& other) const noexcept {
        return frame_ == other.frame_ && same_float(scale_, other.scale_) &&
               same_color(background_, other.background_) && version_ == other.version_;
    }

    [[nodiscard]] const std::vector<std::string>& children(const std::string& parent) const {
        static const std::vector<std::string> none;
        const auto found = children_.find(parent);
        return found == children_.end() ? none : found->second;
    }

private:
    void walk(const DamagePropagation& propagation, const SceneNode& node, const std::string& parent_key, std::size_t index,
              float parent_x, float parent_y, std::optional<Rect> clip, bool transformed) {
        std::string key = make_key(node, parent_key, index);
        const Rect absolute{parent_x + node.bounds.x, parent_y + node.bounds.y, node.bounds.width,
                            node.bounds.height};
        // The damage model does not map clips through transforms, so a clip
        // under any transformed ancestor bounds nothing.
        transformed = transformed || (node.transform && !is_identity(*node.transform));
        const std::optional<Rect> parent_clip = clip;
        const auto own_clip =
            transformed ? std::optional<Rect>{} : propagation.clip_bounds(node, absolute);
        if (own_clip) {
            if (clip) {
                const auto narrowed = clip_to(*clip, *own_clip);
                clip = narrowed.value_or(Rect{own_clip->x, own_clip->y, 0.0F, 0.0F});
            } else {
                clip = own_clip;
            }
        }
        // A clip that spares the node's own paint bounds only its subtree.
        const auto& paint_clip =
            own_clip && node.clip && !node.clip->clips_own_paint ? parent_clip : clip;

        const Rect logical_frame{0.0F, 0.0F, width_, height_};
        std::optional<Rect> damage = node_paint_bounds(node, absolute, logical_frame, propagation);
        if (damage) {
            const float radius = propagation.effect_radius(node);
            if (std::isfinite(radius) && radius > 0.0F) damage = outset(*damage, radius);
            if (paint_clip) damage = clip_to(*damage, *paint_clip);
        }

        const std::size_t position = records_.size();
        by_key_.emplace(key, position);
        by_id_[node.id].push_back(position);
        children_[parent_key].push_back(key);
        records_.push_back({key, parent_key, paint_fields(node), absolute, damage, 0});
        for (std::size_t child = 0; child < node.children.size(); ++child) {
            walk(propagation, node.children[child], key, child, absolute.x, absolute.y, clip,
                 transformed);
        }
        records_[position].subtree_end = records_.size();
    }

    std::string make_key(const SceneNode& node, const std::string& parent_key, std::size_t index) {
        if (!node.id.empty() && used_.insert(node.id).second) return node.id;
        std::string key = parent_key + "\x1f#" + std::to_string(index);
        while (!used_.insert(key).second) key += "'";
        return key;
    }

    float width_;
    float height_;
    float scale_;
    Color background_;
    std::uint32_t version_;
    PixelRect frame_;
    std::vector<NodeRecord> records_;
    std::unordered_map<std::string, std::size_t> by_key_;
    std::unordered_map<std::string, std::vector<std::size_t>> by_id_;
    std::unordered_map<std::string, std::vector<std::string>> children_;
    std::unordered_set<std::string> used_;
};

namespace {

using Snapshot = DamageTracker::Snapshot;

void add_logical(DamageRegion& region, const std::optional<Rect>& rect, float scale) {
    if (!rect) return;
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

void add_subtree(DamageRegion& region, const Snapshot& snapshot, std::size_t position) {
    const std::size_t end = snapshot.at(position).subtree_end;
    for (std::size_t member = position; member < end; ++member) {
        add_logical(region, snapshot.at(member).damage, snapshot.scale());
    }
}

void add_subtree(DamageRegion& region, const Snapshot& snapshot, const NodeRecord& record) {
    add_subtree(region, snapshot, static_cast<std::size_t>(&record - snapshot.records().data()));
}

std::vector<std::string> shared_children(const Snapshot& from, const Snapshot& other,
                                         const std::string& parent) {
    std::vector<std::string> shared;
    for (const auto& key : from.children(parent)) {
        const auto* record = other.find(key);
        if (record != nullptr && record->parent_key == parent) shared.push_back(key);
    }
    return shared;
}

// A mask hides its siblings wherever it draws nothing, so adding, removing
// or retargeting one changes its parent's whole subtree.
void add_parent(DamageRegion& region, const Snapshot& snapshot, const NodeRecord& record) {
    if (const auto* parent = snapshot.find(record.parent_key); parent != nullptr) {
        add_subtree(region, snapshot, *parent);
    }
}

void add_mask_parent(DamageRegion& region, const Snapshot& snapshot, const NodeRecord& record) {
    if (record.paint.mask) add_parent(region, snapshot, record);
}

void diff_snapshots(const Snapshot& before, const Snapshot& after, DamageRegion& region) {
    if (!before.same_canvas(after)) {
        region.add_full_frame();
        return;
    }
    const float scale = after.scale();
    for (const auto& now : after.records()) {
        const NodeRecord* was = before.find(now.key);
        if (was == nullptr) {
            add_logical(region, now.damage, scale);
            add_mask_parent(region, after, now);
            continue;
        }
        if (was->parent_key != now.parent_key) {
            add_subtree(region, before, *was);
            add_subtree(region, after, now);
            add_mask_parent(region, before, *was);
            add_mask_parent(region, after, now);
            continue;
        }
        if (grouping_differs(was->paint, now.paint)) {
            add_subtree(region, before, *was);
            add_subtree(region, after, now);
            if (was->paint.mask != now.paint.mask) {
                add_parent(region, before, *was);
                add_parent(region, after, now);
            }
        }
        // A mask's opacity or transform changes where it covers, and a mask
        // that stops drawing hides its siblings everywhere.
        if ((was->paint.mask || now.paint.mask) &&
            (!same_float(was->paint.opacity, now.paint.opacity) ||
             !same_transform(was->paint.transform, now.paint.transform))) {
            add_mask_parent(region, before, *was);
            add_mask_parent(region, after, now);
        }
        if (!same_rect(was->absolute, now.absolute) || paint_differs(was->paint, now.paint) ||
            !same_optional_rect(was->damage, now.damage)) {
            add_logical(region, was->damage, scale);
            add_logical(region, now.damage, scale);
        }
    }
    for (const auto& was : before.records()) {
        if (after.find(was.key) != nullptr) continue;
        add_logical(region, was.damage, scale);
        add_mask_parent(region, before, was);
    }
    for (const auto& parent : after.records()) {
        const auto now_order = shared_children(after, before, parent.key);
        const auto was_order = shared_children(before, after, parent.key);
        if (now_order == was_order) continue;
        for (std::size_t index = 0; index < now_order.size(); ++index) {
            if (index < was_order.size() && was_order[index] == now_order[index]) continue;
            add_subtree(region, before, *before.find(now_order[index]));
            add_subtree(region, after, *after.find(now_order[index]));
        }
    }
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

std::size_t line_breaks(const SceneNode& node) {
    const auto count = [](const std::string& text) {
        return static_cast<std::size_t>(std::count(text.begin(), text.end(), '\n'));
    };
    if (node.text_runs.empty()) return count(node.text);
    std::size_t breaks = 0;
    for (const auto& run : node.text_runs) breaks += count(run.text);
    return breaks;
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
    : frame_(clamp_rect(frame).empty() ? PixelRect{} : clamp_rect(frame)), policy_(policy) {
    policy_.max_rects = std::clamp<std::size_t>(policy_.max_rects, 1, kMaxDamageRects);
}

void DamageRegion::add(const PixelRect& rect) {
    const PixelRect clipped = intersection(clamp_rect(rect), frame_);
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
    explicit_full_ = explicit_full_ || (other.explicit_full_ && is_full_frame());
}

void DamageRegion::add_full_frame() {
    if (frame_.empty()) return;
    ++input_rects_;
    rects_.assign(1, frame_);
    explicit_full_ = true;
    peak_before_coalescing_ = std::max<std::size_t>(peak_before_coalescing_, 1);
}

void DamageRegion::intersect(const PixelRect& rect) {
    std::vector<PixelRect> kept;
    for (const auto& existing : rects_) {
        const PixelRect overlap = intersection(existing, clamp_rect(rect));
        if (!overlap.empty()) kept.push_back(overlap);
    }
    rects_ = std::move(kept);
    clear_full_flags_unless_full();
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
    clear_full_flags_unless_full();
}

void DamageRegion::clear_full_flags_unless_full() noexcept {
    if (is_full_frame()) return;
    collapsed_ = false;
    explicit_full_ = false;
}

void DamageRegion::clear() {
    rects_.clear();
    input_rects_ = 0;
    peak_before_coalescing_ = 0;
    coalescing_passes_ = 0;
    collapsed_ = false;
    explicit_full_ = false;
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
    stats.explicit_full_frame = explicit_full_;
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

std::optional<Rect> DamagePropagation::clip_bounds(const SceneNode& node,
                                                   const Rect& absolute_bounds) const {
    if (!node.clip || !clip_is_valid(*node.clip)) return std::nullopt;
    if (node.transform && !is_identity(*node.transform)) return std::nullopt;
    return clip_extent(*node.clip, absolute_bounds.x, absolute_bounds.y);
}

bool grouping_differs(const SceneNode& previous, const SceneNode& next) {
    return !same_clip(previous.clip, next.clip) || previous.mask != next.mask ||
           previous.isolate != next.isolate;
}

Rect DamagePropagation::text_extent(const SceneNode& node, const Rect& absolute_bounds,
                                    const Rect& frame) const {
    float largest = std::max(1.0F, node.font_size);
    for (const auto& run : node.text_runs) largest = std::max(largest, run.style.font_size);
    const float lines = static_cast<float>(line_breaks(node) + 1U);
    const float width = std::max(0.0F, absolute_bounds.width);
    const float height = std::max({0.0F, absolute_bounds.height, lines * kLineHeightFactor * largest});
    const float right = std::max(absolute_bounds.x + width + largest, frame.x + frame.width);
    const float left = absolute_bounds.x - largest;
    const float top = absolute_bounds.y - largest;
    const float bottom = absolute_bounds.y + height + largest;
    return {left, top, right - left, bottom - top};
}

std::optional<Rect> node_paint_bounds(const SceneNode& node, const Rect& absolute_bounds,
                                      const Rect& frame, const DamagePropagation& propagation) {
    switch (node.kind) {
    case SceneNode::Kind::group:
        return std::nullopt;
    case SceneNode::Kind::rectangle:
        return box_paint_bounds(node, absolute_bounds);
    case SceneNode::Kind::custom:
        return rect_empty(absolute_bounds) ? std::nullopt : std::optional<Rect>(absolute_bounds);
    case SceneNode::Kind::text: {
        if (node.text.empty() && node.text_runs.empty()) return std::nullopt;
        std::optional<Rect> painted = propagation.text_extent(node, absolute_bounds, frame);
        if (!node.fill_gradients.empty() || !node.box_shadows.empty()) {
            painted = unite(painted, box_paint_bounds(node, absolute_bounds));
        }
        return painted;
    }
    case SceneNode::Kind::mesh:
        return node.mesh ? mesh_paint_bounds(*node.mesh, absolute_bounds) : std::nullopt;
    case SceneNode::Kind::path:
        return path_paint_bounds(node, absolute_bounds, frame);
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
               !same_float(previous.corner_radius, next.corner_radius);
    case SceneNode::Kind::text:
        return previous.text != next.text || !same_runs(previous.text_runs, next.text_runs) ||
               !same_color(previous.fill, next.fill) ||
               !same_float(previous.font_size, next.font_size) ||
               previous.font_family != next.font_family ||
               previous.font_weight != next.font_weight ||
               !same_float(previous.letter_spacing, next.letter_spacing) ||
               previous.underline != next.underline ||
               previous.strikethrough != next.strikethrough ||
               !same_gradients(previous.fill_gradients, next.fill_gradients) ||
               !same_shadows(previous.box_shadows, next.box_shadows) ||
               !same_float(previous.corner_radius, next.corner_radius);
    case SceneNode::Kind::mesh:
        return previous.mesh != next.mesh;
    case SceneNode::Kind::path:
        return !same_color(previous.fill, next.fill) ||
               !same_gradients(previous.fill_gradients, next.fill_gradients) ||
               !same_path(previous.path, next.path);
    case SceneNode::Kind::custom:
        return previous.custom_component != next.custom_component ||
               previous.custom_properties_json != next.custom_properties_json;
    }
    return true;
}

DamageRegion compute_scene_damage(const Scene& previous, const Scene& next, float scale,
                                  DamagePolicy policy, const DamagePropagation* propagation) {
    DamageRegion region(frame_rect(next, scale), policy);
    const DamagePropagation& effects = propagation != nullptr ? *propagation : default_propagation();
    if (frame_rect(previous, scale) != region.frame() ||
        !same_color(previous.background, next.background) ||
        previous.scene_version != next.scene_version) {
        region.add_full_frame();
        return region;
    }
    diff_snapshots(Snapshot(previous, scale, effects), Snapshot(next, scale, effects), region);
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
    const DamagePropagation& effects = propagation_ ? *propagation_ : default_propagation();
    auto snapshot = std::make_shared<const Snapshot>(next, scale, effects);
    if (!committed_ || snapshot->frame() != pending_.frame()) {
        pending_ = DamageRegion(snapshot->frame(), policy_);
        pending_.add_full_frame();
    } else {
        diff_snapshots(*committed_, *snapshot, pending_);
    }
    committed_ = std::move(snapshot);
    last_stats_ = pending_.stats();
}

void DamageTracker::mark_dirty(std::string_view node_id) {
    if (!committed_) return;
    const auto* positions = committed_->find_id(node_id);
    if (positions == nullptr) {
        pending_.add_full_frame();
    } else {
        for (const auto position : *positions) add_subtree(pending_, *committed_, position);
    }
    last_stats_ = pending_.stats();
}

void DamageTracker::mark_dirty_all(InvalidationReason) {
    if (!committed_) return;
    pending_.add_full_frame();
    last_stats_ = pending_.stats();
}

void DamageTracker::invalidate(InvalidationReason reason, std::string_view node_id) {
    if (reason == InvalidationReason::reload || node_id.empty()) {
        mark_dirty_all(reason);
    } else {
        mark_dirty(node_id);
    }
}

DamageRegion DamageTracker::take_damage() {
    DamageRegion taken = std::move(pending_);
    last_stats_ = taken.stats();
    pending_ = DamageRegion(committed_ ? committed_->frame() : PixelRect{}, policy_);
    return taken;
}

}  // namespace vellum::graphics
