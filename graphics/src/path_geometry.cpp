#include <vellum/graphics/path_geometry.hpp>

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace vellum::graphics {
namespace {

constexpr double kPi = 3.14159265358979323846;
constexpr float kArcBoundsSlack = 0.01F;

bool known_verb(PathVerb verb) noexcept {
    switch (verb) {
    case PathVerb::move:
    case PathVerb::line:
    case PathVerb::quadratic:
    case PathVerb::cubic:
    case PathVerb::arc:
    case PathVerb::close:
        return true;
    }
    return false;
}

bool known_fill_rule(FillRule rule) noexcept {
    switch (rule) {
    case FillRule::nonzero:
    case FillRule::evenodd:
        return true;
    }
    return false;
}

bool known_join(StrokeJoin join) noexcept {
    switch (join) {
    case StrokeJoin::miter:
    case StrokeJoin::round:
    case StrokeJoin::bevel:
        return true;
    }
    return false;
}

bool known_cap(StrokeCap cap) noexcept {
    switch (cap) {
    case StrokeCap::butt:
    case StrokeCap::round:
    case StrokeCap::square:
        return true;
    }
    return false;
}

bool finite_color(const Color& color) noexcept {
    return std::isfinite(color.red) && std::isfinite(color.green) &&
           std::isfinite(color.blue) && std::isfinite(color.alpha);
}

struct Box final {
    bool empty = true;
    float left = 0.0F;
    float top = 0.0F;
    float right = 0.0F;
    float bottom = 0.0F;

    void add(float x, float y) noexcept {
        if (empty) {
            left = right = x;
            top = bottom = y;
            empty = false;
            return;
        }
        left = std::min(left, x);
        top = std::min(top, y);
        right = std::max(right, x);
        bottom = std::max(bottom, y);
    }
};

struct ArcEllipse final {
    double cx = 0.0;
    double cy = 0.0;
    double rx = 0.0;
    double ry = 0.0;
    double cos_phi = 1.0;
    double sin_phi = 0.0;
};

// The ellipse an SVG arc lies on, with radii scaled up to span the endpoints,
// following the endpoint-to-centre conversion of SVG 2 appendix B.2.4. Empty
// when the arc is drawn as a line or not at all.
std::optional<ArcEllipse> arc_ellipse(float from_x, float from_y, const float* arc) {
    const double x1 = from_x;
    const double y1 = from_y;
    const double x2 = arc[5];
    const double y2 = arc[6];
    double rx = std::abs(static_cast<double>(arc[0]));
    double ry = std::abs(static_cast<double>(arc[1]));
    if (rx == 0.0 || ry == 0.0 || (x1 == x2 && y1 == y2)) return std::nullopt;
    const double phi = static_cast<double>(arc[2]) * kPi / 180.0;
    const double cos_phi = std::cos(phi);
    const double sin_phi = std::sin(phi);
    const double dx = (x1 - x2) * 0.5;
    const double dy = (y1 - y2) * 0.5;
    const double x1p = cos_phi * dx + sin_phi * dy;
    const double y1p = -sin_phi * dx + cos_phi * dy;
    const double lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
    if (lambda > 1.0) {
        const double scale = std::sqrt(lambda);
        rx *= scale;
        ry *= scale;
    }
    const double numerator = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p;
    const double denominator = rx * rx * y1p * y1p + ry * ry * x1p * x1p;
    double coefficient =
        denominator > 0.0 ? std::sqrt(std::max(0.0, numerator / denominator)) : 0.0;
    if ((arc[3] != 0.0F) == (arc[4] != 0.0F)) coefficient = -coefficient;
    const double cxp = coefficient * rx * y1p / ry;
    const double cyp = -coefficient * ry * x1p / rx;
    const ArcEllipse ellipse{
        .cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) * 0.5,
        .cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) * 0.5,
        .rx = rx,
        .ry = ry,
        .cos_phi = cos_phi,
        .sin_phi = sin_phi,
    };
    if (!std::isfinite(ellipse.cx) || !std::isfinite(ellipse.cy) || !std::isfinite(rx) ||
        !std::isfinite(ry)) {
        return std::nullopt;
    }
    return ellipse;
}

void add_arc_ellipse(Box& box, float from_x, float from_y, const float* arc) {
    const auto ellipse = arc_ellipse(from_x, from_y, arc);
    if (!ellipse) return;
    const double c2 = ellipse->cos_phi * ellipse->cos_phi;
    const double s2 = ellipse->sin_phi * ellipse->sin_phi;
    const double half_x = std::sqrt(ellipse->rx * ellipse->rx * c2 + ellipse->ry * ellipse->ry * s2);
    const double half_y = std::sqrt(ellipse->rx * ellipse->rx * s2 + ellipse->ry * ellipse->ry * c2);
    box.add(static_cast<float>(ellipse->cx - half_x) - kArcBoundsSlack,
            static_cast<float>(ellipse->cy - half_y) - kArcBoundsSlack);
    box.add(static_cast<float>(ellipse->cx + half_x) + kArcBoundsSlack,
            static_cast<float>(ellipse->cy + half_y) + kArcBoundsSlack);
}

// Visits each drawing command with the point it starts from.
template <typename Visit>
void walk_commands(const PathGeometry& path, Visit visit) {
    float current_x = 0.0F;
    float current_y = 0.0F;
    float start_x = 0.0F;
    float start_y = 0.0F;
    std::size_t offset = 0;
    for (const auto verb : path.verbs) {
        const std::size_t arity = path_verb_arity(verb);
        if (!known_verb(verb) || path.values.size() - offset < arity) return;
        const float* v = path.values.data() + offset;
        offset += arity;
        visit(verb, current_x, current_y, start_x, start_y, v);
        if (verb == PathVerb::close) {
            current_x = start_x;
            current_y = start_y;
            continue;
        }
        current_x = v[arity - 2U];
        current_y = v[arity - 1U];
        if (verb == PathVerb::move) {
            start_x = current_x;
            start_y = current_y;
        }
    }
}

class PathDataParser final {
public:
    explicit PathDataParser(std::string_view data) : data_(data) {}

    bool parse(PathGeometry& output, std::string* reason) {
        if (data_.size() > kMaxPathDataBytes) {
            return fail(reason, "path data is longer than the " +
                                    std::to_string(kMaxPathDataBytes) + "-byte limit");
        }
        skip_whitespace();
        bool first = true;
        while (position_ < data_.size()) {
            const std::size_t at = position_;
            const char letter = data_[position_++];
            if (first && letter != 'M' && letter != 'm') {
                return fail(reason, "path data must start with a move command");
            }
            first = false;
            if (!command(letter, at, reason)) return false;
            skip_whitespace();
        }
        output.verbs = std::move(verbs_);
        output.values = std::move(values_);
        return true;
    }

private:
    enum class Previous { other, cubic, quadratic };

    static bool fail(std::string* reason, std::string message) {
        if (reason != nullptr) *reason = std::move(message);
        return false;
    }

    std::string at(std::size_t offset) const { return " at byte " + std::to_string(offset); }

    static bool whitespace(char c) noexcept {
        return c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == '\f';
    }

    static bool digit(char c) noexcept { return c >= '0' && c <= '9'; }

    void skip_whitespace() noexcept {
        while (position_ < data_.size() && whitespace(data_[position_])) ++position_;
    }

    void skip_separator() noexcept {
        skip_whitespace();
        if (position_ < data_.size() && data_[position_] == ',') {
            ++position_;
            skip_whitespace();
        }
    }

    bool number_follows() const noexcept {
        if (position_ >= data_.size()) return false;
        const char c = data_[position_];
        return digit(c) || c == '+' || c == '-' || c == '.';
    }

    bool number(float& output, std::string* reason) {
        const std::size_t start = position_;
        bool negative = false;
        if (position_ < data_.size() && (data_[position_] == '+' || data_[position_] == '-')) {
            negative = data_[position_] == '-';
            ++position_;
        }
        std::uint64_t mantissa = 0;
        int significant = 0;
        long exponent = 0;
        bool any_digit = false;
        const auto accumulate = [&](char c, bool fraction) {
            any_digit = true;
            const auto value = static_cast<std::uint64_t>(c - '0');
            if (significant < 19) {
                if (mantissa != 0U || value != 0U) ++significant;
                mantissa = mantissa * 10U + value;
                if (fraction) --exponent;
            } else if (!fraction) {
                ++exponent;
            }
        };
        while (position_ < data_.size() && digit(data_[position_])) {
            accumulate(data_[position_++], false);
        }
        if (position_ < data_.size() && data_[position_] == '.') {
            ++position_;
            while (position_ < data_.size() && digit(data_[position_])) {
                accumulate(data_[position_++], true);
            }
        }
        if (!any_digit) return fail(reason, "path data: expected a number" + at(start));
        if (position_ < data_.size() && (data_[position_] == 'e' || data_[position_] == 'E')) {
            ++position_;
            bool exponent_negative = false;
            if (position_ < data_.size() && (data_[position_] == '+' || data_[position_] == '-')) {
                exponent_negative = data_[position_] == '-';
                ++position_;
            }
            if (position_ >= data_.size() || !digit(data_[position_])) {
                return fail(reason, "path data: expected an exponent" + at(position_));
            }
            long written = 0;
            while (position_ < data_.size() && digit(data_[position_])) {
                written = std::min(written * 10 + (data_[position_++] - '0'), 100000L);
            }
            exponent += exponent_negative ? -written : written;
        }
        double value = static_cast<double>(mantissa);
        if (mantissa != 0U) {
            const double scale = std::pow(10.0, static_cast<double>(std::abs(exponent)));
            value = exponent < 0 ? value / scale : value * scale;
        }
        if (!std::isfinite(value) ||
            value > static_cast<double>(std::numeric_limits<float>::max())) {
            return fail(reason, "path data: a number is out of range" + at(start));
        }
        output = static_cast<float>(negative ? -value : value);
        return true;
    }

    bool flag(float& output, std::string* reason) {
        if (position_ < data_.size() && (data_[position_] == '0' || data_[position_] == '1')) {
            output = data_[position_++] == '1' ? 1.0F : 0.0F;
            return true;
        }
        return fail(reason, "path data: expected an arc flag of 0 or 1" + at(position_));
    }

    bool arguments(float* output, std::size_t count, bool arc, std::string* reason) {
        for (std::size_t index = 0; index < count; ++index) {
            if (index > 0U) skip_separator();
            const bool is_flag = arc && (index == 3U || index == 4U);
            if (!(is_flag ? flag(output[index], reason) : number(output[index], reason))) {
                return false;
            }
        }
        return true;
    }

    bool emit(PathVerb verb, std::initializer_list<float> values, std::string* reason) {
        if (verb != PathVerb::move && pending_move_) {
            pending_move_ = false;
            if (!emit(PathVerb::move, {start_x_, start_y_}, reason)) return false;
        }
        if (verb == PathVerb::move) pending_move_ = false;
        if (verbs_.size() >= kMaxPathCommands) {
            return fail(reason, "path data exceeds the " + std::to_string(kMaxPathCommands) +
                                    "-command budget");
        }
        if (values_.size() + values.size() > kMaxPathValues) {
            return fail(reason, "path data exceeds the " + std::to_string(kMaxPathValues) +
                                    "-value budget");
        }
        verbs_.push_back(verb);
        values_.insert(values_.end(), values);
        return true;
    }

    bool command(char letter, std::size_t offset, std::string* reason) {
        const bool relative = letter >= 'a' && letter <= 'z';
        const char upper = relative ? static_cast<char>(letter - 'a' + 'A') : letter;
        std::size_t arity = 0;
        switch (upper) {
        case 'Z':
            if (!emit(PathVerb::close, {}, reason)) return false;
            current_x_ = start_x_;
            current_y_ = start_y_;
            previous_ = Previous::other;
            pending_move_ = true;
            return true;
        case 'M':
        case 'L':
        case 'T': arity = 2U; break;
        case 'H':
        case 'V': arity = 1U; break;
        case 'S':
        case 'Q': arity = 4U; break;
        case 'C': arity = 6U; break;
        case 'A': arity = 7U; break;
        default:
            return fail(reason, std::string("path data: unknown command '") + letter + "'" +
                                    at(offset));
        }
        char repeat = upper;
        bool first_set = true;
        while (true) {
            skip_whitespace();
            if (!first_set && !number_follows()) break;
            float values[7]{};
            if (!arguments(values, arity, upper == 'A', reason)) return false;
            if (!apply(repeat, relative, values, reason)) return false;
            if (repeat == 'M') repeat = 'L';
            first_set = false;
            skip_whitespace();
            if (position_ < data_.size() && data_[position_] == ',') {
                ++position_;
                skip_whitespace();
                if (!number_follows()) {
                    return fail(reason, "path data: expected a number" + at(position_));
                }
            }
        }
        return true;
    }

    bool apply(char upper, bool relative, float* v, std::string* reason) {
        const float base_x = relative ? current_x_ : 0.0F;
        const float base_y = relative ? current_y_ : 0.0F;
        const auto finite = [&](std::initializer_list<float> values) {
            for (const float value : values) {
                if (!std::isfinite(value)) {
                    return fail(reason, "path data: a coordinate is out of range" + at(position_));
                }
            }
            return true;
        };
        switch (upper) {
        case 'M': {
            const float x = base_x + v[0];
            const float y = base_y + v[1];
            if (!finite({x, y}) || !emit(PathVerb::move, {x, y}, reason)) return false;
            start_x_ = current_x_ = x;
            start_y_ = current_y_ = y;
            previous_ = Previous::other;
            return true;
        }
        case 'L':
        case 'H':
        case 'V': {
            float x = current_x_;
            float y = current_y_;
            if (upper == 'L') {
                x = base_x + v[0];
                y = base_y + v[1];
            } else if (upper == 'H') {
                x = base_x + v[0];
            } else {
                y = base_y + v[0];
            }
            if (!finite({x, y}) || !emit(PathVerb::line, {x, y}, reason)) return false;
            current_x_ = x;
            current_y_ = y;
            previous_ = Previous::other;
            return true;
        }
        case 'C':
        case 'S': {
            float c1x = current_x_;
            float c1y = current_y_;
            const float* rest = v;
            if (upper == 'C') {
                c1x = base_x + v[0];
                c1y = base_y + v[1];
                rest = v + 2;
            } else if (previous_ == Previous::cubic) {
                c1x = 2.0F * current_x_ - control_x_;
                c1y = 2.0F * current_y_ - control_y_;
            }
            const float c2x = base_x + rest[0];
            const float c2y = base_y + rest[1];
            const float x = base_x + rest[2];
            const float y = base_y + rest[3];
            if (!finite({c1x, c1y, c2x, c2y, x, y}) ||
                !emit(PathVerb::cubic, {c1x, c1y, c2x, c2y, x, y}, reason)) {
                return false;
            }
            control_x_ = c2x;
            control_y_ = c2y;
            current_x_ = x;
            current_y_ = y;
            previous_ = Previous::cubic;
            return true;
        }
        case 'Q':
        case 'T': {
            float cx = current_x_;
            float cy = current_y_;
            const float* rest = v;
            if (upper == 'Q') {
                cx = base_x + v[0];
                cy = base_y + v[1];
                rest = v + 2;
            } else if (previous_ == Previous::quadratic) {
                cx = 2.0F * current_x_ - control_x_;
                cy = 2.0F * current_y_ - control_y_;
            }
            const float x = base_x + rest[0];
            const float y = base_y + rest[1];
            if (!finite({cx, cy, x, y}) || !emit(PathVerb::quadratic, {cx, cy, x, y}, reason)) {
                return false;
            }
            control_x_ = cx;
            control_y_ = cy;
            current_x_ = x;
            current_y_ = y;
            previous_ = Previous::quadratic;
            return true;
        }
        case 'A': {
            const float x = base_x + v[5];
            const float y = base_y + v[6];
            if (!finite({x, y}) ||
                !emit(PathVerb::arc,
                      {std::abs(v[0]), std::abs(v[1]), v[2], v[3], v[4], x, y}, reason)) {
                return false;
            }
            current_x_ = x;
            current_y_ = y;
            previous_ = Previous::other;
            return true;
        }
        default:
            return false;
        }
    }

    std::string_view data_;
    std::size_t position_ = 0;
    std::vector<PathVerb> verbs_;
    std::vector<float> values_;
    float current_x_ = 0.0F;
    float current_y_ = 0.0F;
    float start_x_ = 0.0F;
    float start_y_ = 0.0F;
    float control_x_ = 0.0F;
    float control_y_ = 0.0F;
    Previous previous_ = Previous::other;
    bool pending_move_ = false;
};

}  // namespace

bool path_is_valid(const PathGeometry& path, std::string* reason) {
    const auto fail = [reason](std::string message) {
        if (reason != nullptr) *reason = std::move(message);
        return false;
    };
    if (path.verbs.size() > kMaxPathCommands) {
        return fail("the path has more than " + std::to_string(kMaxPathCommands) + " commands");
    }
    if (path.values.size() > kMaxPathValues) {
        return fail("the path has more than " + std::to_string(kMaxPathValues) + " values");
    }
    if (!path.verbs.empty() && path.verbs.front() != PathVerb::move) {
        return fail("the path does not start with a move");
    }
    std::size_t offset = 0;
    for (const auto verb : path.verbs) {
        if (!known_verb(verb)) return fail("the path has an unknown command");
        const std::size_t arity = path_verb_arity(verb);
        if (path.values.size() - offset < arity) {
            return fail("a path command has fewer values than its arity");
        }
        if (verb == PathVerb::arc) {
            const float* arc = path.values.data() + offset;
            if (arc[0] < 0.0F || arc[1] < 0.0F) {
                return fail("an arc radius is negative");
            }
            if ((arc[3] != 0.0F && arc[3] != 1.0F) || (arc[4] != 0.0F && arc[4] != 1.0F)) {
                return fail("an arc flag is not 0 or 1");
            }
        }
        offset += arity;
    }
    if (offset != path.values.size()) {
        return fail("the path has values its commands do not consume");
    }
    for (const float value : path.values) {
        if (!std::isfinite(value)) return fail("a path coordinate is not finite");
    }
    if (!known_fill_rule(path.fill_rule)) return fail("the fill rule is unknown");
    const auto& stroke = path.stroke;
    if (!known_join(stroke.join)) return fail("the stroke join is unknown");
    if (!known_cap(stroke.cap)) return fail("the stroke cap is unknown");
    if (!finite_color(stroke.color)) return fail("the stroke colour is not finite");
    if (!std::isfinite(stroke.width) || stroke.width < 0.0F) {
        return fail("the stroke width is not a finite non-negative number");
    }
    if (!std::isfinite(stroke.miter_limit) || stroke.miter_limit < 1.0F) {
        return fail("the stroke miter limit is not a finite number of at least 1");
    }
    if (!std::isfinite(stroke.dash_offset)) return fail("the stroke dash offset is not finite");
    if (stroke.dash_array.size() > kMaxPathDashes) {
        return fail("the stroke dash array has more than " + std::to_string(kMaxPathDashes) +
                    " entries");
    }
    double dash_total = 0.0;
    for (const float dash : stroke.dash_array) {
        if (!std::isfinite(dash) || dash < 0.0F) {
            return fail("a stroke dash is not a finite non-negative number");
        }
        dash_total += dash;
    }
    if (!stroke.dash_array.empty() && !(dash_total > 0.0)) {
        return fail("the stroke dash array sums to zero");
    }
    if (!stroke.dash_array.empty()) {
        // An odd-length array repeats once, so a period then holds as many dashes
        // as the array has entries, over twice its sum.
        const bool odd = stroke.dash_array.size() % 2U != 0U;
        const double dashes_per_period =
            static_cast<double>(odd ? stroke.dash_array.size() : stroke.dash_array.size() / 2U);
        const double period = odd ? 2.0 * dash_total : dash_total;
        if (path_length_bound(path) * dashes_per_period / period > kMaxPathDashSegments) {
            return fail("the stroke dash pattern would draw more than " +
                        std::to_string(static_cast<long>(kMaxPathDashSegments)) + " dashes");
        }
    }
    return true;
}

std::optional<Rect> path_control_bounds(const PathGeometry& path) {
    Box box;
    walk_commands(path, [&box](PathVerb verb, float from_x, float from_y, float, float,
                               const float* v) {
        switch (verb) {
        case PathVerb::close: return;
        case PathVerb::arc:
            add_arc_ellipse(box, from_x, from_y, v);
            box.add(v[5], v[6]);
            return;
        case PathVerb::move:
        case PathVerb::line:
        case PathVerb::quadratic:
        case PathVerb::cubic:
            for (std::size_t index = 0; index + 1U < path_verb_arity(verb); index += 2U) {
                box.add(v[index], v[index + 1U]);
            }
            return;
        }
    });
    if (box.empty) return std::nullopt;
    return Rect{box.left, box.top, box.right - box.left, box.bottom - box.top};
}

double path_length_bound(const PathGeometry& path) {
    double length = 0.0;
    walk_commands(path, [&length](PathVerb verb, float from_x, float from_y, float start_x,
                                  float start_y, const float* v) {
        double x = from_x;
        double y = from_y;
        const auto to = [&](double next_x, double next_y) {
            length += std::hypot(next_x - x, next_y - y);
            x = next_x;
            y = next_y;
        };
        switch (verb) {
        case PathVerb::move: return;
        case PathVerb::close: to(start_x, start_y); return;
        case PathVerb::arc:
            if (const auto ellipse = arc_ellipse(from_x, from_y, v)) {
                length += 2.0 * kPi * std::max(ellipse->rx, ellipse->ry);
            } else {
                to(v[5], v[6]);
            }
            return;
        case PathVerb::line:
        case PathVerb::quadratic:
        case PathVerb::cubic:
            for (std::size_t index = 0; index + 1U < path_verb_arity(verb); index += 2U) {
                to(v[index], v[index + 1U]);
            }
            return;
        }
    });
    return length;
}

float path_stroke_outset(const PathStroke& stroke) noexcept {
    if (!std::isfinite(stroke.width) || !(stroke.width > 0.0F) || !(stroke.color.alpha > 0.0F)) {
        return 0.0F;
    }
    float factor = 1.0F;
    if (stroke.join == StrokeJoin::miter && std::isfinite(stroke.miter_limit)) {
        factor = std::max(factor, stroke.miter_limit);
    }
    if (stroke.cap != StrokeCap::butt) factor = std::max(factor, std::sqrt(2.0F));
    return 0.5F * stroke.width * factor;
}

bool parse_svg_path_data(std::string_view data, PathGeometry& output, std::string* reason) {
    return PathDataParser(data).parse(output, reason);
}

}  // namespace vellum::graphics
