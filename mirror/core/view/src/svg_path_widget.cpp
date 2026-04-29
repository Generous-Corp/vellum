#include <pulp/view/svg_path_widget.hpp>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstdlib>

namespace pulp::view {

namespace {

// SVG path-data tokenizer. Walks the input string, returning the next
// command letter or float in turn. Whitespace and commas are separators
// per the SVG 1.1 spec. Negative numbers don't need a separator from
// the previous token (e.g. "M0 0L1-1" is valid).
class PathScanner {
public:
    explicit PathScanner(const std::string& s) : s_(s), i_(0) {}

    bool at_end() const { return i_ >= s_.size(); }

    void skip_separators() {
        while (i_ < s_.size()) {
            char c = s_[i_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == ',') {
                ++i_;
            } else {
                break;
            }
        }
    }

    // Returns 0 (no command) when the next non-separator char is a
    // digit / sign / dot — meaning the caller should re-use the
    // previous command (SVG path implicit-command rule).
    char next_command() {
        skip_separators();
        if (i_ >= s_.size()) return 0;
        char c = s_[i_];
        if (std::isalpha(static_cast<unsigned char>(c))) {
            ++i_;
            return c;
        }
        return 0;
    }

    bool next_float(float& out) {
        skip_separators();
        if (i_ >= s_.size()) return false;
        const char* start = s_.data() + i_;
        char* end = nullptr;
        out = std::strtof(start, &end);
        if (end == start) return false;
        i_ += static_cast<size_t>(end - start);
        return true;
    }

    // SVG arcs encode the large-arc and sweep flags as a single '0' or
    // '1' digit, optionally without a separator from the next number.
    // Strtof would happily read '01' as 1.0 and consume both digits, so
    // arcs need a dedicated single-flag scan.
    bool next_flag(int& out) {
        skip_separators();
        if (i_ >= s_.size()) return false;
        char c = s_[i_];
        if (c == '0') { out = 0; ++i_; return true; }
        if (c == '1') { out = 1; ++i_; return true; }
        return false;
    }

private:
    const std::string& s_;
    size_t i_;
};

struct ParserState {
    float cx = 0, cy = 0;        // current point
    float sx = 0, sy = 0;        // last move-to (for Z)
    float prev_cubic_cpx = 0;    // last cubic control-point-2 (for S/s reflect)
    float prev_cubic_cpy = 0;
    float prev_quad_cpx = 0;     // last quadratic control point (for T/t reflect)
    float prev_quad_cpy = 0;
    bool has_prev_cubic = false;
    bool has_prev_quad = false;
};

// Approximate an SVG elliptical arc with a chain of cubic-bezier
// segments, following the W3C SVG implementation note:
// https://www.w3.org/TR/SVG/implnote.html#ArcImplementationNotes
// One bezier per <= pi/2 sweep angle. Returns the new current point.
void emit_arc_as_cubics(std::vector<SvgPathSegment>& out,
                        float x1, float y1,
                        float rx, float ry,
                        float phi_deg,
                        int large_arc, int sweep,
                        float x2, float y2) {
    if (x1 == x2 && y1 == y2) return;
    if (rx == 0 || ry == 0) {
        SvgPathSegment seg;
        seg.op = SvgPathSegment::Op::line_to;
        seg.p[0] = x2; seg.p[1] = y2;
        out.push_back(seg);
        return;
    }
    rx = std::abs(rx);
    ry = std::abs(ry);
    const float phi = phi_deg * 3.14159265358979323846f / 180.0f;
    const float cos_phi = std::cos(phi);
    const float sin_phi = std::sin(phi);

    // Step 1: transform to centred parametrisation (W3C eq. F.6.5.1).
    const float dx = (x1 - x2) * 0.5f;
    const float dy = (y1 - y2) * 0.5f;
    const float x1p =  cos_phi * dx + sin_phi * dy;
    const float y1p = -sin_phi * dx + cos_phi * dy;

    // Correct out-of-range radii (W3C eq. F.6.6.2).
    float lambda = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry);
    if (lambda > 1.0f) {
        const float s = std::sqrt(lambda);
        rx *= s;
        ry *= s;
    }

    const float rx2 = rx * rx;
    const float ry2 = ry * ry;
    const float x1p2 = x1p * x1p;
    const float y1p2 = y1p * y1p;
    float radicand = rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2;
    radicand /= (rx2 * y1p2 + ry2 * x1p2);
    if (radicand < 0) radicand = 0;
    float coef = std::sqrt(radicand);
    if (large_arc == sweep) coef = -coef;
    const float cxp =  coef * (rx * y1p) / ry;
    const float cyp = -coef * (ry * x1p) / rx;

    // Centre in original coords.
    const float cx = cos_phi * cxp - sin_phi * cyp + (x1 + x2) * 0.5f;
    const float cy = sin_phi * cxp + cos_phi * cyp + (y1 + y2) * 0.5f;

    // Start and sweep angles.
    auto angle_between = [](float ux, float uy, float vx, float vy) {
        const float dot = ux * vx + uy * vy;
        const float len = std::sqrt((ux*ux + uy*uy) * (vx*vx + vy*vy));
        float c = len > 0 ? dot / len : 1.0f;
        c = std::clamp(c, -1.0f, 1.0f);
        float ang = std::acos(c);
        if (ux * vy - uy * vx < 0) ang = -ang;
        return ang;
    };
    const float theta1 = angle_between(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry);
    float dtheta = angle_between((x1p - cxp) / rx, (y1p - cyp) / ry,
                                  (-x1p - cxp) / rx, (-y1p - cyp) / ry);
    constexpr float kTwoPi = 2.0f * 3.14159265358979323846f;
    if (!sweep && dtheta > 0) dtheta -= kTwoPi;
    else if (sweep && dtheta < 0) dtheta += kTwoPi;

    // Number of cubic segments — one per <= pi/2 sweep.
    const int n_segs = std::max(1, static_cast<int>(std::ceil(std::abs(dtheta) / (3.14159265f * 0.5f))));
    const float seg_angle = dtheta / static_cast<float>(n_segs);
    // Bezier control-handle length factor for an arc of seg_angle radians.
    const float t = (4.0f / 3.0f) * std::tan(seg_angle * 0.25f);

    float prev_x = x1, prev_y = y1;
    for (int i = 1; i <= n_segs; ++i) {
        const float a0 = theta1 + seg_angle * static_cast<float>(i - 1);
        const float a1 = theta1 + seg_angle * static_cast<float>(i);
        const float cos_a0 = std::cos(a0), sin_a0 = std::sin(a0);
        const float cos_a1 = std::cos(a1), sin_a1 = std::sin(a1);

        const float ex = cos_phi * rx * cos_a1 - sin_phi * ry * sin_a1 + cx;
        const float ey = sin_phi * rx * cos_a1 + cos_phi * ry * sin_a1 + cy;

        const float c1x = prev_x + (-cos_phi * rx * sin_a0 - sin_phi * ry * cos_a0) * t;
        const float c1y = prev_y + (-sin_phi * rx * sin_a0 + cos_phi * ry * cos_a0) * t;
        const float c2x = ex + (cos_phi * rx * sin_a1 + sin_phi * ry * cos_a1) * t;
        const float c2y = ey + (sin_phi * rx * sin_a1 - cos_phi * ry * cos_a1) * t;

        SvgPathSegment seg;
        seg.op = SvgPathSegment::Op::cubic_to;
        seg.p[0] = c1x; seg.p[1] = c1y;
        seg.p[2] = c2x; seg.p[3] = c2y;
        seg.p[4] = ex;  seg.p[5] = ey;
        out.push_back(seg);
        prev_x = ex; prev_y = ey;
    }
}

void parse_path(const std::string& data, std::vector<SvgPathSegment>& out) {
    out.clear();
    PathScanner sc(data);
    ParserState st;
    char prev = 0;

    auto rel = [](char c) { return std::islower(static_cast<unsigned char>(c)) != 0; };

    while (!sc.at_end()) {
        char cmd = sc.next_command();
        if (cmd == 0) {
            // Implicit command — re-use the previous one. M repeats as L,
            // m repeats as l (W3C path-data grammar). All others repeat as
            // themselves.
            if (prev == 'M') cmd = 'L';
            else if (prev == 'm') cmd = 'l';
            else cmd = prev;
            if (cmd == 0) break;  // garbage at start
        }

        const bool relative = rel(cmd);
        const char up = static_cast<char>(std::toupper(static_cast<unsigned char>(cmd)));

        switch (up) {
        case 'M': {
            float x, y;
            if (!sc.next_float(x) || !sc.next_float(y)) return;
            if (relative) { x += st.cx; y += st.cy; }
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::move_to;
            seg.p[0] = x; seg.p[1] = y;
            out.push_back(seg);
            st.cx = x; st.cy = y;
            st.sx = x; st.sy = y;
            st.has_prev_cubic = false;
            st.has_prev_quad = false;
            break;
        }
        case 'L': {
            float x, y;
            if (!sc.next_float(x) || !sc.next_float(y)) return;
            if (relative) { x += st.cx; y += st.cy; }
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::line_to;
            seg.p[0] = x; seg.p[1] = y;
            out.push_back(seg);
            st.cx = x; st.cy = y;
            st.has_prev_cubic = false;
            st.has_prev_quad = false;
            break;
        }
        case 'H': {
            float x;
            if (!sc.next_float(x)) return;
            if (relative) x += st.cx;
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::line_to;
            seg.p[0] = x; seg.p[1] = st.cy;
            out.push_back(seg);
            st.cx = x;
            st.has_prev_cubic = false;
            st.has_prev_quad = false;
            break;
        }
        case 'V': {
            float y;
            if (!sc.next_float(y)) return;
            if (relative) y += st.cy;
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::line_to;
            seg.p[0] = st.cx; seg.p[1] = y;
            out.push_back(seg);
            st.cy = y;
            st.has_prev_cubic = false;
            st.has_prev_quad = false;
            break;
        }
        case 'C': {
            float x1, y1, x2, y2, x, y;
            if (!sc.next_float(x1) || !sc.next_float(y1) ||
                !sc.next_float(x2) || !sc.next_float(y2) ||
                !sc.next_float(x)  || !sc.next_float(y)) return;
            if (relative) {
                x1 += st.cx; y1 += st.cy;
                x2 += st.cx; y2 += st.cy;
                x  += st.cx; y  += st.cy;
            }
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::cubic_to;
            seg.p[0] = x1; seg.p[1] = y1;
            seg.p[2] = x2; seg.p[3] = y2;
            seg.p[4] = x;  seg.p[5] = y;
            out.push_back(seg);
            st.cx = x; st.cy = y;
            st.prev_cubic_cpx = x2;
            st.prev_cubic_cpy = y2;
            st.has_prev_cubic = true;
            st.has_prev_quad = false;
            break;
        }
        case 'S': {
            float x2, y2, x, y;
            if (!sc.next_float(x2) || !sc.next_float(y2) ||
                !sc.next_float(x)  || !sc.next_float(y)) return;
            if (relative) {
                x2 += st.cx; y2 += st.cy;
                x  += st.cx; y  += st.cy;
            }
            // First control point is the reflection of the previous
            // cubic's second control point through the current point.
            // If the previous segment wasn't a cubic, use the current
            // point itself (W3C spec).
            float x1, y1;
            if (st.has_prev_cubic) {
                x1 = 2.0f * st.cx - st.prev_cubic_cpx;
                y1 = 2.0f * st.cy - st.prev_cubic_cpy;
            } else {
                x1 = st.cx; y1 = st.cy;
            }
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::cubic_to;
            seg.p[0] = x1; seg.p[1] = y1;
            seg.p[2] = x2; seg.p[3] = y2;
            seg.p[4] = x;  seg.p[5] = y;
            out.push_back(seg);
            st.cx = x; st.cy = y;
            st.prev_cubic_cpx = x2;
            st.prev_cubic_cpy = y2;
            st.has_prev_cubic = true;
            st.has_prev_quad = false;
            break;
        }
        case 'Q': {
            float x1, y1, x, y;
            if (!sc.next_float(x1) || !sc.next_float(y1) ||
                !sc.next_float(x)  || !sc.next_float(y)) return;
            if (relative) {
                x1 += st.cx; y1 += st.cy;
                x  += st.cx; y  += st.cy;
            }
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::quad_to;
            seg.p[0] = x1; seg.p[1] = y1;
            seg.p[2] = x;  seg.p[3] = y;
            out.push_back(seg);
            st.cx = x; st.cy = y;
            st.prev_quad_cpx = x1;
            st.prev_quad_cpy = y1;
            st.has_prev_quad = true;
            st.has_prev_cubic = false;
            break;
        }
        case 'T': {
            float x, y;
            if (!sc.next_float(x) || !sc.next_float(y)) return;
            if (relative) { x += st.cx; y += st.cy; }
            float x1, y1;
            if (st.has_prev_quad) {
                x1 = 2.0f * st.cx - st.prev_quad_cpx;
                y1 = 2.0f * st.cy - st.prev_quad_cpy;
            } else {
                x1 = st.cx; y1 = st.cy;
            }
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::quad_to;
            seg.p[0] = x1; seg.p[1] = y1;
            seg.p[2] = x;  seg.p[3] = y;
            out.push_back(seg);
            st.cx = x; st.cy = y;
            st.prev_quad_cpx = x1;
            st.prev_quad_cpy = y1;
            st.has_prev_quad = true;
            st.has_prev_cubic = false;
            break;
        }
        case 'A': {
            float rx, ry, phi, x, y;
            int large, sweep;
            if (!sc.next_float(rx) || !sc.next_float(ry) ||
                !sc.next_float(phi) ||
                !sc.next_flag(large) || !sc.next_flag(sweep) ||
                !sc.next_float(x)   || !sc.next_float(y)) return;
            if (relative) { x += st.cx; y += st.cy; }
            emit_arc_as_cubics(out, st.cx, st.cy, rx, ry, phi,
                               large, sweep, x, y);
            st.cx = x; st.cy = y;
            st.has_prev_cubic = false;
            st.has_prev_quad = false;
            break;
        }
        case 'Z': {
            SvgPathSegment seg;
            seg.op = SvgPathSegment::Op::close_path;
            out.push_back(seg);
            st.cx = st.sx; st.cy = st.sy;
            st.has_prev_cubic = false;
            st.has_prev_quad = false;
            break;
        }
        default:
            // Unknown command — skip a token to make progress and avoid
            // infinite loops on malformed input.
            float dummy;
            if (!sc.next_float(dummy)) return;
            break;
        }
        prev = cmd;
    }
}

}  // namespace

void SvgPathWidget::set_path(std::string data) {
    path_data_ = std::move(data);
    reparse();
}

void SvgPathWidget::set_viewbox(float w, float h) {
    viewbox_w_ = w;
    viewbox_h_ = h;
}

void SvgPathWidget::set_fill_color(canvas::Color c) {
    fill_color_ = c;
    has_fill_ = true;
}

void SvgPathWidget::clear_fill() {
    has_fill_ = false;
}

void SvgPathWidget::set_stroke_color(canvas::Color c) {
    stroke_color_ = c;
    has_stroke_ = true;
}

void SvgPathWidget::clear_stroke() {
    has_stroke_ = false;
}

void SvgPathWidget::set_stroke_width(float w) {
    stroke_width_ = std::max(0.0f, w);
}

void SvgPathWidget::reparse() {
    parse_path(path_data_, segments_);
}

void SvgPathWidget::paint(canvas::Canvas& canvas) {
    if (segments_.empty()) return;
    if (!has_fill_ && !has_stroke_) return;

    const auto b = local_bounds();
    if (b.width <= 0 || b.height <= 0) return;

    const float vw = viewbox_w_ > 0 ? viewbox_w_ : b.width;
    const float vh = viewbox_h_ > 0 ? viewbox_h_ : b.height;

    canvas.save();
    // xMidYMid meet — preserve aspect, centre, scale to fit.
    const float sx = b.width / vw;
    const float sy = b.height / vh;
    const float scale = std::min(sx, sy);
    const float ox = (b.width  - vw * scale) * 0.5f;
    const float oy = (b.height - vh * scale) * 0.5f;
    canvas.translate(ox, oy);
    canvas.scale(scale, scale);

    canvas.begin_path();
    for (const auto& seg : segments_) {
        switch (seg.op) {
        case SvgPathSegment::Op::move_to:
            canvas.move_to(seg.p[0], seg.p[1]);
            break;
        case SvgPathSegment::Op::line_to:
            canvas.line_to(seg.p[0], seg.p[1]);
            break;
        case SvgPathSegment::Op::quad_to:
            canvas.quad_to(seg.p[0], seg.p[1], seg.p[2], seg.p[3]);
            break;
        case SvgPathSegment::Op::cubic_to:
            canvas.cubic_to(seg.p[0], seg.p[1], seg.p[2], seg.p[3],
                            seg.p[4], seg.p[5]);
            break;
        case SvgPathSegment::Op::close_path:
            canvas.close_path();
            break;
        }
    }

    if (has_fill_) {
        canvas.set_fill_color(fill_color_);
        canvas.fill_current_path();
    }
    if (has_stroke_ && stroke_width_ > 0) {
        canvas.set_stroke_color(stroke_color_);
        canvas.set_line_width(stroke_width_);
        canvas.stroke_current_path();
    }

    canvas.restore();
}

}  // namespace pulp::view
