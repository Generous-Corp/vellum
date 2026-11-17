#include <vellum/graphics/hit_test.hpp>

#include <vellum/graphics/animation.hpp>
#include <vellum/graphics/paint_command.hpp>
#include <vellum/graphics/path_geometry.hpp>

#include <algorithm>
#include <cmath>
#include <memory>
#include <vector>

namespace vellum::graphics {

namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr double kFlatness = 0.05;
constexpr int kMaxSegments = 512;
constexpr double kMinDeterminant = 1e-12;

struct Pt final {
    double x = 0.0;
    double y = 0.0;
};

struct Mat final {
    double a = 1.0;
    double b = 0.0;
    double c = 0.0;
    double d = 1.0;
    double tx = 0.0;
    double ty = 0.0;
};

Mat to_mat(const Affine2D& m) {
    return {m.a, m.b, m.c, m.d, m.tx, m.ty};
}

Mat translate(double x, double y) {
    return {1.0, 0.0, 0.0, 1.0, x, y};
}

Mat operator*(const Mat& outer, const Mat& inner) {
    return {
        outer.a * inner.a + outer.c * inner.b,
        outer.b * inner.a + outer.d * inner.b,
        outer.a * inner.c + outer.c * inner.d,
        outer.b * inner.c + outer.d * inner.d,
        outer.a * inner.tx + outer.c * inner.ty + outer.tx,
        outer.b * inner.tx + outer.d * inner.ty + outer.ty,
    };
}

Pt apply(const Mat& m, Pt p) {
    return {m.a * p.x + m.c * p.y + m.tx, m.b * p.x + m.d * p.y + m.ty};
}

double determinant(const Mat& m) {
    return m.a * m.d - m.b * m.c;
}

std::optional<Mat> invert(const Mat& m) {
    const double det = determinant(m);
    if (!std::isfinite(det) || std::abs(det) < kMinDeterminant) return std::nullopt;
    const Mat inverse{
        m.d / det,
        -m.b / det,
        -m.c / det,
        m.a / det,
        (m.c * m.ty - m.d * m.tx) / det,
        (m.b * m.tx - m.a * m.ty) / det,
    };
    if (!std::isfinite(inverse.a) || !std::isfinite(inverse.b) || !std::isfinite(inverse.c) ||
        !std::isfinite(inverse.d) || !std::isfinite(inverse.tx) || !std::isfinite(inverse.ty)) {
        return std::nullopt;
    }
    return inverse;
}

double max_stretch(const Mat& m) {
    const double p = (m.a * m.a + m.b * m.b + m.c * m.c + m.d * m.d) / 2.0;
    const double q = (m.a * m.a + m.b * m.b - m.c * m.c - m.d * m.d) / 2.0;
    const double r = m.a * m.c + m.b * m.d;
    return std::sqrt(p + std::sqrt(q * q + r * r));
}

struct Subpath final {
    std::vector<Pt> points;
    bool closed = false;
};

void add_arc(std::vector<Pt>& out, Pt from, const float* arc, double scale) {
    const double x2 = arc[5];
    const double y2 = arc[6];
    double rx = std::abs(static_cast<double>(arc[0]));
    double ry = std::abs(static_cast<double>(arc[1]));
    const double phi = static_cast<double>(arc[2]) * kPi / 180.0;
    const bool large = arc[3] != 0.0F;
    const bool sweep = arc[4] != 0.0F;
    if (from.x == x2 && from.y == y2) return;
    if (rx == 0.0 || ry == 0.0) {
        out.push_back({x2, y2});
        return;
    }
    const double cos_phi = std::cos(phi);
    const double sin_phi = std::sin(phi);
    const double dx = (from.x - x2) / 2.0;
    const double dy = (from.y - y2) / 2.0;
    const double x1p = cos_phi * dx + sin_phi * dy;
    const double y1p = -sin_phi * dx + cos_phi * dy;
    const double lambda = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry);
    if (lambda > 1.0) {
        rx *= std::sqrt(lambda);
        ry *= std::sqrt(lambda);
    }
    const double numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
    const double denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
    double coefficient = denominator == 0.0 ? 0.0 : std::sqrt(std::max(0.0, numerator / denominator));
    if (large == sweep) coefficient = -coefficient;
    const double cxp = coefficient * rx * y1p / ry;
    const double cyp = -coefficient * ry * x1p / rx;
    const double cx = cos_phi * cxp - sin_phi * cyp + (from.x + x2) / 2.0;
    const double cy = sin_phi * cxp + cos_phi * cyp + (from.y + y2) / 2.0;
    const double theta1 = std::atan2((y1p - cyp) / ry, (x1p - cxp) / rx);
    const double theta2 = std::atan2((-y1p - cyp) / ry, (-x1p - cxp) / rx);
    double delta = theta2 - theta1;
    if (!sweep && delta > 0.0) delta -= 2.0 * kPi;
    if (sweep && delta < 0.0) delta += 2.0 * kPi;
    const double radius = std::max(rx, ry) * scale;
    const double step_angle = radius <= kFlatness ? kPi : 2.0 * std::acos(1.0 - kFlatness / radius);
    const int steps = std::clamp(static_cast<int>(std::ceil(std::abs(delta) / step_angle)), 2,
                                 kMaxSegments);
    for (int step = 1; step <= steps; ++step) {
        const double theta = theta1 + delta * static_cast<double>(step) / steps;
        const double ex = rx * std::cos(theta);
        const double ey = ry * std::sin(theta);
        out.push_back({cx + ex * cos_phi - ey * sin_phi, cy + ex * sin_phi + ey * cos_phi});
    }
    out.back() = {x2, y2};
}

std::vector<Subpath> flatten(const PathGeometry& path, double scale) {
    std::vector<Subpath> result;
    std::size_t offset = 0;
    Pt current{};
    Pt start{};
    bool open = false;
    const auto ensure_open = [&] {
        if (open) return;
        result.push_back({{current}, false});
        open = true;
    };
    for (const auto verb : path.verbs) {
        const auto arity = path_verb_arity(verb);
        if (offset + arity > path.values.size()) break;
        const float* v = path.values.data() + offset;
        offset += arity;
        switch (verb) {
        case PathVerb::move:
            current = start = {v[0], v[1]};
            result.push_back({{current}, false});
            open = true;
            break;
        case PathVerb::line:
            ensure_open();
            current = {v[0], v[1]};
            result.back().points.push_back(current);
            break;
        case PathVerb::quadratic: {
            ensure_open();
            const Pt p0 = current;
            const Pt p1{v[0], v[1]};
            const Pt p2{v[2], v[3]};
            const double bend = std::hypot(p0.x - 2.0 * p1.x + p2.x, p0.y - 2.0 * p1.y + p2.y);
            const int segments = std::clamp(
                static_cast<int>(std::ceil(std::sqrt(bend * scale / (4.0 * kFlatness)))), 2,
                kMaxSegments);
            for (int step = 1; step <= segments; ++step) {
                const double t = static_cast<double>(step) / segments;
                const double u = 1.0 - t;
                result.back().points.push_back({u * u * p0.x + 2.0 * u * t * p1.x + t * t * p2.x,
                                                u * u * p0.y + 2.0 * u * t * p1.y + t * t * p2.y});
            }
            current = p2;
            break;
        }
        case PathVerb::cubic: {
            ensure_open();
            const Pt p0 = current;
            const Pt p1{v[0], v[1]};
            const Pt p2{v[2], v[3]};
            const Pt p3{v[4], v[5]};
            const double bend = std::max(
                std::hypot(p0.x - 2.0 * p1.x + p2.x, p0.y - 2.0 * p1.y + p2.y),
                std::hypot(p1.x - 2.0 * p2.x + p3.x, p1.y - 2.0 * p2.y + p3.y));
            const int segments = std::clamp(
                static_cast<int>(std::ceil(std::sqrt(0.75 * bend * scale / kFlatness))), 2,
                kMaxSegments);
            for (int step = 1; step <= segments; ++step) {
                const double t = static_cast<double>(step) / segments;
                const double u = 1.0 - t;
                const double w0 = u * u * u;
                const double w1 = 3.0 * u * u * t;
                const double w2 = 3.0 * u * t * t;
                const double w3 = t * t * t;
                result.back().points.push_back({w0 * p0.x + w1 * p1.x + w2 * p2.x + w3 * p3.x,
                                                w0 * p0.y + w1 * p1.y + w2 * p2.y + w3 * p3.y});
            }
            current = p3;
            break;
        }
        case PathVerb::arc:
            ensure_open();
            add_arc(result.back().points, current, v, scale);
            current = {v[5], v[6]};
            break;
        case PathVerb::close:
            if (open) result.back().closed = true;
            current = start;
            open = false;
            break;
        }
    }
    for (auto& subpath : result) {
        for (const auto& point : subpath.points) {
            if (!std::isfinite(point.x) || !std::isfinite(point.y)) {
                subpath.points.clear();
                break;
            }
        }
    }
    return result;
}

double cross(Pt a, Pt b, Pt p) {
    return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x);
}

double distance_to_segment(Pt a, Pt b, Pt p) {
    const double dx = b.x - a.x;
    const double dy = b.y - a.y;
    const double length2 = dx * dx + dy * dy;
    double t = length2 == 0.0 ? 0.0 : ((p.x - a.x) * dx + (p.y - a.y) * dy) / length2;
    t = std::clamp(t, 0.0, 1.0);
    return std::hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

bool inside_fill(const std::vector<Subpath>& paths, Pt p, FillRule rule) {
    int winding = 0;
    bool odd = false;
    for (const auto& path : paths) {
        const auto& pts = path.points;
        if (pts.size() < 2U) continue;
        for (std::size_t index = 0; index < pts.size(); ++index) {
            const Pt a = pts[index];
            const Pt b = pts[(index + 1U) % pts.size()];
            if ((a.y > p.y) != (b.y > p.y)) {
                const double x = a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y);
                if (p.x < x) odd = !odd;
            }
            if (a.y <= p.y) {
                if (b.y > p.y && cross(a, b, p) > 0.0) ++winding;
            } else if (b.y <= p.y && cross(a, b, p) < 0.0) {
                --winding;
            }
        }
    }
    return rule == FillRule::evenodd ? odd : winding != 0;
}

bool fill_hit(const std::vector<Subpath>& paths, Pt p, FillRule rule) {
    return inside_fill(paths, p, rule);
}

bool in_triangle(Pt a, Pt b, Pt c, Pt p) {
    const double d1 = cross(a, b, p);
    const double d2 = cross(b, c, p);
    const double d3 = cross(c, a, p);
    const bool negative = d1 < 0.0 || d2 < 0.0 || d3 < 0.0;
    const bool positive = d1 > 0.0 || d2 > 0.0 || d3 > 0.0;
    return !(negative && positive);
}

bool stroke_hit(const std::vector<Subpath>& paths, Pt p, const PathStroke& stroke) {
    const double reach = static_cast<double>(stroke.width) / 2.0;
    for (const auto& subpath : paths) {
        std::vector<Pt> pts;
        for (const auto& point : subpath.points) {
            if (pts.empty() || point.x != pts.back().x || point.y != pts.back().y) {
                pts.push_back(point);
            }
        }
        bool closed = subpath.closed;
        if (closed && pts.size() > 1U && pts.front().x == pts.back().x &&
            pts.front().y == pts.back().y) {
            pts.pop_back();
        }
        if (pts.empty()) continue;
        if (pts.size() == 1U) {
            if (stroke.cap == StrokeCap::round && std::hypot(p.x - pts[0].x, p.y - pts[0].y) <= reach) {
                return true;
            }
            if (stroke.cap == StrokeCap::square && std::abs(p.x - pts[0].x) <= reach &&
                std::abs(p.y - pts[0].y) <= reach) {
                return true;
            }
            continue;
        }
        if (pts.size() == 2U) closed = false;
        const std::size_t count = pts.size();
        const std::size_t segments = closed ? count : count - 1U;
        const auto direction = [&](std::size_t index, double& length) {
            const Pt a = pts[index];
            const Pt b = pts[(index + 1U) % count];
            length = std::hypot(b.x - a.x, b.y - a.y);
            return Pt{(b.x - a.x) / length, (b.y - a.y) / length};
        };
        for (std::size_t index = 0; index < segments; ++index) {
            double length = 0.0;
            const Pt u = direction(index, length);
            const Pt a = pts[index];
            const double along = (p.x - a.x) * u.x + (p.y - a.y) * u.y;
            const double across = -(p.x - a.x) * u.y + (p.y - a.y) * u.x;
            double before = 0.0;
            double after = 0.0;
            if (!closed && stroke.cap == StrokeCap::square) {
                if (index == 0U) before += static_cast<double>(stroke.width) / 2.0;
                if (index + 1U == segments) after += static_cast<double>(stroke.width) / 2.0;
            }
            if (along >= -before && along <= length + after && std::abs(across) <= reach) return true;
        }
        if (!closed && stroke.cap == StrokeCap::round) {
            if (std::hypot(p.x - pts.front().x, p.y - pts.front().y) <= reach) return true;
            if (std::hypot(p.x - pts.back().x, p.y - pts.back().y) <= reach) return true;
        }
        const std::size_t first_join = closed ? 0U : 1U;
        const std::size_t last_join = closed ? count : count - 1U;
        for (std::size_t index = first_join; index < last_join; ++index) {
            const Pt v = pts[index];
            if (stroke.join == StrokeJoin::round) {
                if (std::hypot(p.x - v.x, p.y - v.y) <= reach) return true;
                continue;
            }
            double length = 0.0;
            const Pt in = direction((index + count - 1U) % count, length);
            const Pt out = direction(index, length);
            for (const double side : {1.0, -1.0}) {
                const Pt n1{-in.y * side * reach, in.x * side * reach};
                const Pt n2{-out.y * side * reach, out.x * side * reach};
                if (in_triangle(v, {v.x + n1.x, v.y + n1.y}, {v.x + n2.x, v.y + n2.y}, p)) return true;
            }
        }
    }
    return false;
}

bool in_rounded_rect(double x, double y, double width, double height, const CornerRadii& radii, Pt p) {
    if (width < 0.0 || height < 0.0) return false;
    const double px = p.x - x;
    const double py = p.y - y;
    if (px < 0.0 || py < 0.0 || px > width || py > height) return false;
    double tl = std::max(0.0F, radii.top_left);
    double tr = std::max(0.0F, radii.top_right);
    double br = std::max(0.0F, radii.bottom_right);
    double bl = std::max(0.0F, radii.bottom_left);
    double factor = 1.0;
    const auto limit = [&](double extent, double sum) {
        if (sum > extent && sum > 0.0) factor = std::min(factor, extent / sum);
    };
    limit(width, tl + tr);
    limit(width, bl + br);
    limit(height, tl + bl);
    limit(height, tr + br);
    tl *= factor;
    tr *= factor;
    br *= factor;
    bl *= factor;
    const auto corner = [&](double radius, double cx, double cy) {
        return std::hypot(px - cx, py - cy) <= radius;
    };
    if (tl > 0.0 && px < tl && py < tl) return corner(tl, tl, tl);
    if (tr > 0.0 && px > width - tr && py < tr) return corner(tr, width - tr, tr);
    if (br > 0.0 && px > width - br && py > height - br) return corner(br, width - br, height - br);
    if (bl > 0.0 && px < bl && py > height - bl) return corner(bl, bl, height - bl);
    return true;
}

bool clip_contains(const NodeClip& clip, Pt local, double scale) {
    if (clip.path) {
        if (clip.path->verbs.empty()) return false;
        return fill_hit(flatten(*clip.path, scale), local, clip.path->fill_rule);
    }
    return in_rounded_rect(clip.rect.x, clip.rect.y, clip.rect.width, clip.rect.height, clip.radii,
                           local);
}

bool visible(const Color& color, bool has_gradient) {
    return color.alpha > 0.0F || has_gradient;
}

bool stroke_visible(const PathStroke& stroke, bool extra_gradient) {
    return stroke.width > 0.0F &&
           visible(stroke.color, stroke.gradient.has_value() || extra_gradient);
}

std::vector<Pt> tolerance_samples(Pt center, double tolerance) {
    std::vector<Pt> samples{center};
    if (!(tolerance > 0.0)) return samples;
    for (const double radius : {tolerance, tolerance / 2.0}) {
        for (int step = 0; step < 8; ++step) {
            const double angle = step * kPi / 4.0;
            samples.push_back({center.x + radius * std::cos(angle), center.y + radius * std::sin(angle)});
        }
    }
    return samples;
}

bool path_hit(const PathGeometry& path, bool fill_painted, bool stroke_painted, const Mat& to_frame,
              Pt frame_point, double tolerance) {
    const auto inverse = invert(to_frame);
    if (!inverse) return false;
    const auto subpaths = flatten(path, max_stretch(to_frame));
    for (const Pt sample : tolerance_samples(frame_point, tolerance)) {
        const Pt local = apply(*inverse, sample);
        if (fill_painted && fill_hit(subpaths, local, path.fill_rule)) return true;
        if (stroke_painted && stroke_hit(subpaths, local, path.stroke)) return true;
    }
    return false;
}

struct VectorOutcome final {
    bool painted = false;
    bool hit = false;
};

VectorOutcome vector_hit(const VectorAsset& asset, const Rect& bounds, const Mat& to_frame,
                         Pt frame_point, double tolerance) {
    VectorOutcome outcome;
    const Rect& box = asset.view_box;
    if (!(bounds.width > 0.0F) || !(bounds.height > 0.0F) || !(box.width > 0.0F) ||
        !(box.height > 0.0F)) {
        return outcome;
    }
    const double sx = static_cast<double>(bounds.width) / box.width;
    const double sy = static_cast<double>(bounds.height) / box.height;
    const Mat fit{sx, 0.0, 0.0, sy, -box.x * sx, -box.y * sy};
    struct Group final {
        Mat matrix;
        std::shared_ptr<const PathGeometry> clip;
        bool hidden = false;
    };
    std::vector<Group> groups{{to_frame * fit, nullptr, false}};
    for (const auto& op : asset.ops) {
        if (op.kind == VectorOp::Kind::pop_group) {
            if (groups.size() > 1U) groups.pop_back();
            continue;
        }
        const Mat matrix = groups.back().matrix * to_mat(op.transform);
        if (op.kind == VectorOp::Kind::push_group) {
            groups.push_back({matrix, op.clip,
                              groups.back().hidden || !(std::clamp(op.opacity, 0.0F, 1.0F) > 0.0F)});
            continue;
        }
        if (groups.back().hidden || !op.path) continue;
        const bool fill_painted = visible(op.fill.color, op.fill.gradient.has_value());
        const bool stroke_painted = op.path->stroke.width > 0.0F &&
                                    visible(op.stroke.color, op.stroke.gradient.has_value());
        if (!fill_painted && !stroke_painted) continue;
        outcome.painted = true;
        if (outcome.hit) continue;
        bool clipped = false;
        for (std::size_t index = 1; index < groups.size() && !clipped; ++index) {
            if (!groups[index].clip) continue;
            const auto group_inverse = invert(groups[index].matrix);
            if (!group_inverse) {
                clipped = true;
                continue;
            }
            const auto& clip = *groups[index].clip;
            clipped = clip.verbs.empty() ||
                      !fill_hit(flatten(clip, max_stretch(groups[index].matrix)),
                                apply(*group_inverse, frame_point), clip.fill_rule);
        }
        if (clipped) continue;
        if (path_hit(*op.path, fill_painted, stroke_painted, matrix, frame_point, tolerance)) {
            outcome.hit = true;
        }
    }
    return outcome;
}

bool mesh_hit(const TriangleMesh& mesh, Pt local) {
    if (mesh.clip_enabled &&
        !in_rounded_rect(mesh.clip.x, mesh.clip.y, mesh.clip.width, mesh.clip.height, {}, local)) {
        return false;
    }
    const auto& v = mesh.vertices;
    for (std::size_t index = 0; index + 2U < mesh.indices.size(); index += 3U) {
        const auto i0 = mesh.indices[index];
        const auto i1 = mesh.indices[index + 1U];
        const auto i2 = mesh.indices[index + 2U];
        if (i0 >= v.size() || i1 >= v.size() || i2 >= v.size()) continue;
        if (in_triangle({v[i0].x, v[i0].y}, {v[i1].x, v[i1].y}, {v[i2].x, v[i2].y}, local)) {
            return true;
        }
    }
    return false;
}

bool known_kind(SceneNode::Kind kind) {
    switch (kind) {
    case SceneNode::Kind::group:
    case SceneNode::Kind::rectangle:
    case SceneNode::Kind::text:
    case SceneNode::Kind::custom:
    case SceneNode::Kind::mesh:
    case SceneNode::Kind::path:
    case SceneNode::Kind::image:
    case SceneNode::Kind::vector:
    case SceneNode::Kind::plot: return true;
    }
    return false;
}

class Walker final {
public:
    Walker(const PickOptions& options, Pt frame_point)
        : options_(options), frame_point_(frame_point) {}

    bool visit(const SceneNode& node, float parent_x, float parent_y, const Mat& parent) {
        if (!known_kind(node.kind) || !compositing_is_finite(node)) return false;
        if (node.clip && !clip_is_valid(*node.clip)) return false;
        if (!(std::clamp(node.opacity, 0.0F, 1.0F) > 0.0F)) return false;
        const float ox = parent_x + node.bounds.x;
        const float oy = parent_y + node.bounds.y;
        Mat matrix = parent;
        if (node.transform && !is_identity(*node.transform)) {
            matrix = parent * translate(ox, oy) * to_mat(*node.transform) * translate(-ox, -oy);
        }
        const Mat to_frame = matrix * translate(ox, oy);
        const auto inverse = invert(to_frame);
        if (!inverse) return false;
        const Pt local = apply(*inverse, frame_point_);
        bool clip_ok = true;
        if (node.clip) {
            clip_ok = clip_contains(*node.clip, local, max_stretch(to_frame));
            if (!clip_ok && node.clip->clips_own_paint) return false;
        }
        chain_.push_back(&node);
        if (clip_ok && visit_children(node, ox, oy, matrix)) return true;
        if (own_hit(node, local, to_frame)) {
            hit_chain_ = chain_;
            return true;
        }
        chain_.pop_back();
        return false;
    }

    [[nodiscard]] const std::vector<const SceneNode*>& hit_chain() const noexcept {
        return hit_chain_;
    }

private:
    bool visit_children(const SceneNode& node, float ox, float oy, const Mat& matrix) {
        const bool masked = std::any_of(node.children.begin(), node.children.end(),
            [](const SceneNode& child) { return child.mask.has_value(); });
        if (masked) {
            PickOptions painted_only = options_;
            painted_only.interactive = nullptr;
            for (const auto& child : node.children) {
                if (!child.mask) continue;
                Walker coverage(painted_only, frame_point_);
                if (!coverage.visit(child, ox, oy, matrix)) return false;
            }
        }
        for (auto it = node.children.rbegin(); it != node.children.rend(); ++it) {
            if (it->mask) continue;
            if (visit(*it, ox, oy, matrix)) return true;
        }
        return false;
    }

    [[nodiscard]] bool interactive(const SceneNode& node) const {
        return options_.interactive != nullptr && options_.interactive->count(node.id) != 0U;
    }

    [[nodiscard]] bool box_hit(const SceneNode& node, Pt local) const {
        return in_rounded_rect(0.0, 0.0, node.bounds.width, node.bounds.height,
                               resolved_corner_radii(node), local);
    }

    bool own_hit(const SceneNode& node, Pt local, const Mat& to_frame) const {
        const bool fill_painted = visible(node.fill, !node.fill_gradients.empty());
        const double tolerance = std::max(0.0F, options_.tolerance);
        switch (node.kind) {
        case SceneNode::Kind::path: {
            if (node.path) {
                const bool stroke_painted = stroke_visible(node.path->stroke, node.stroke_gradient.has_value());
                if (fill_painted || stroke_painted) {
                    return path_hit(*node.path, fill_painted, stroke_painted, to_frame, frame_point_, tolerance);
                }
            }
            return interactive(node) && box_hit(node, local);
        }
        case SceneNode::Kind::vector: {
            if (node.vector) {
                const auto outcome =
                    vector_hit(*node.vector, node.bounds, to_frame, frame_point_, tolerance);
                if (outcome.painted) return outcome.hit;
            }
            return interactive(node) && box_hit(node, local);
        }
        case SceneNode::Kind::mesh: {
            if (node.mesh && !node.mesh->indices.empty()) return mesh_hit(*node.mesh, local);
            return interactive(node) && box_hit(node, local);
        }
        case SceneNode::Kind::text:
            if (!node.text.empty() || !node.text_runs.empty()) return box_hit(node, local);
            break;
        case SceneNode::Kind::image:
        case SceneNode::Kind::plot: return box_hit(node, local);
        default: break;
        }
        if ((node.kind == SceneNode::Kind::rectangle && fill_painted) || interactive(node)) {
            return box_hit(node, local);
        }
        return false;
    }

    PickOptions options_;
    Pt frame_point_;
    std::vector<const SceneNode*> chain_;
    std::vector<const SceneNode*> hit_chain_;
};

std::optional<NodeId> pick_evaluated(const Scene& scene, Point point, const PickOptions& options) {
    if (!std::isfinite(point.x) || !std::isfinite(point.y)) return std::nullopt;
    Walker walker(options, {point.x, point.y});
    if (!walker.visit(scene.root, 0.0F, 0.0F, Mat{})) return std::nullopt;
    const auto& chain = walker.hit_chain();
    if (options.interactive == nullptr) return chain.back()->id;
    for (auto it = chain.rbegin(); it != chain.rend(); ++it) {
        if (options.interactive->count((*it)->id) != 0U) return (*it)->id;
    }
    return std::nullopt;
}

}  // namespace

std::optional<NodeId> pick(const Scene& scene, Point point, const PickOptions& options) {
    if (find_version_11_fields(scene.root) != nullptr) {
        return pick_evaluated(evaluate_tracks(scene), point, options);
    }
    return pick_evaluated(scene, point, options);
}

}  // namespace vellum::graphics
