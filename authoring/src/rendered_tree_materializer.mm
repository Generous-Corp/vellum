#include "rendered_tree_materializer.hpp"

#import <Foundation/Foundation.h>
#include <xlocale.h>

#include <vellum/graphics/paint_command.hpp>

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/path_geometry.hpp>

#include <algorithm>
#include <array>
#include <cctype>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <initializer_list>
#include <limits>
#include <memory>
#include <optional>
#include <set>
#include <string_view>
#include <utility>

namespace vellum::authoring {
namespace {

constexpr std::size_t kMaximumJsonBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaximumNodes = 100000U;
constexpr std::size_t kMaximumDepth = 256U;
constexpr NSUInteger kMaximumGradientStops = 64U;
constexpr NSUInteger kMaximumTextInputBytes = 64U * 1024U;
constexpr NSUInteger kMaximumPlaceholderBytes = 4U * 1024U;
// Retained-tree consumers share these defaults across native and web. Controls
// retain the established 44-point density; zero keeps an unspecified generic
// child eligible for the parent stack's remaining-axis fill behavior.
constexpr float kDefaultButtonHeight = 44.0F;
constexpr float kDefaultTextInputHeight = 44.0F;
constexpr float kDefaultGenericHeight = 0.0F;
constexpr float kTextLineHeightMultiplier = 1.4F;
constexpr std::array<std::string_view, 10> kIntrinsicTypes{
    "view", "stack", "text", "text-run", "text-input", "button", "image", "canvas", "custom",
    "path",
};
constexpr std::array<std::string_view, 6> kTextStyleKeys{
    "color", "fontFamily", "fontSize", "fontWeight", "letterSpacing", "textDecoration",
};
constexpr std::array<std::string_view, 4> kCornerRadiusKeys{
    "borderTopLeftRadius", "borderTopRightRadius",
    "borderBottomRightRadius", "borderBottomLeftRadius",
};
constexpr std::size_t kMaximumTransformBytes = 1024U;
constexpr std::size_t kMaximumTransformFunctions = 32U;

using vellum::graphics::LoweringDiagnostic;

template <std::size_t Size>
bool contains(const std::array<std::string_view, Size>& values, std::string_view value) {
    return std::find(values.begin(), values.end(), value) != values.end();
}

struct StyleUse final {
    bool root = false;
    bool lays_out_children = false;
    bool paints_text_background = false;
    bool draws_image = false;
    /// `overflow: hidden` clips to the box with its border radii.
    bool clips_to_box = false;
};

bool style_key_consumed(std::string_view type, std::string_view key, const StyleUse& use) {
    const bool text = type == "text" || type == "text-run";
    if (key == "width" || key == "height") return true;
    if (key == "x" || key == "y") return !use.root;
    if (key == "opacity" || key == "transform") return true;
    if (key == "overflow" || key == "clipPath" || key == "isolation") return true;
    // The root has no siblings to mask.
    if (key == "maskMode") return !use.root;
    if (use.clips_to_box && type != "path" &&
        (key == "borderRadius" || contains(kCornerRadiusKeys, key))) {
        return true;
    }
    if (type == "path") return false;
    if (use.draws_image) {
        return key == "backgroundColor" || key == "borderRadius" ||
               contains(kCornerRadiusKeys, key);
    }
    if (key == "padding" || key == "direction") return use.lays_out_children;
    if (key == "gap") return type == "stack" && use.lays_out_children;
    if (key == "backgroundGradient" || key == "backgroundLinearGradient" || key == "boxShadow") return true;
    if (key == "backgroundColor") return !text;
    if (key == "borderRadius" || contains(kCornerRadiusKeys, key)) {
        return !text || use.paints_text_background;
    }
    if (text) return contains(kTextStyleKeys, key);
    if (type == "button") return key == "color";
    if (type == "text-input") return key == "color" || key == "fontSize";
    return false;
}

bool skipped_text_run(NSString* parent_type, id child) {
    return [child isKindOfClass:NSDictionary.class] &&
        [static_cast<NSDictionary*>(child)[@"type"] isEqual:@"text-run"] &&
        ([parent_type isEqualToString:@"text"] || [parent_type isEqualToString:@"button"]);
}

bool text_run_style_key_consumed(std::string_view parent_type, std::string_view key) {
    return parent_type == "text" && contains(kTextStyleKeys, key);
}

void set_error(std::string* destination, std::string value) {
    if (destination != nullptr) *destination = std::move(value);
}

std::string cpp_string(NSString* value) {
    if (value == nil) return {};
    const char* utf8 = value.UTF8String;
    if (utf8 == nullptr) return {};
    return std::string{
        utf8,
        static_cast<std::size_t>(
            [value lengthOfBytesUsingEncoding:NSUTF8StringEncoding])};
}

bool finite_number(id value, float& output) {
    if (![value isKindOfClass:NSNumber.class] ||
        CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) {
        return false;
    }
    const double number = [static_cast<NSNumber*>(value) doubleValue];
    if (!std::isfinite(number) ||
        number < -static_cast<double>(std::numeric_limits<float>::max()) ||
        number > static_cast<double>(std::numeric_limits<float>::max())) {
        return false;
    }
    output = static_cast<float>(number);
    return true;
}

float number_or(NSDictionary* style, NSString* key, float fallback) {
    float value = 0.0F;
    return finite_number(style[key], value) ? value : fallback;
}

bool number_field(
    NSDictionary* source, NSString* key, float fallback, float& output,
    std::string_view path, std::string* error) {
    id raw = source[key];
    if (raw == nil) {
        output = fallback;
        return true;
    }
    if (!finite_number(raw, output)) {
        set_error(error, std::string(path) + " must be a finite number");
        return false;
    }
    return true;
}

std::optional<vellum::graphics::Color> parse_color(id value) {
    if (![value isKindOfClass:NSString.class]) return std::nullopt;
    NSString* text = static_cast<NSString*>(value);
    if (![text hasPrefix:@"#"] || (text.length != 7 && text.length != 9)) {
        return std::nullopt;
    }
    unsigned long long encoded = 0;
    NSScanner* scanner = [NSScanner scannerWithString:[text substringFromIndex:1]];
    if (![scanner scanHexLongLong:&encoded] || !scanner.isAtEnd) return std::nullopt;
    if (text.length == 7) {
        return vellum::graphics::Color::hex(static_cast<std::uint32_t>(encoded));
    }
    return vellum::graphics::Color::hex(
        static_cast<std::uint32_t>(encoded >> 8U),
        static_cast<float>(encoded & 0xFFU) / 255.0F);
}

NSDictionary* dictionary_or_empty(id value);
NSArray* array_or_empty(id value);

vellum::graphics::TextStyle text_style(
    NSDictionary* style, vellum::graphics::TextStyle fallback = {}) {
    if ([style[@"fontFamily"] isKindOfClass:NSString.class]) {
        fallback.font_family = cpp_string(style[@"fontFamily"]);
    }
    fallback.font_size = std::max(
        1.0F, number_or(style, @"fontSize", fallback.font_size));
    fallback.letter_spacing = number_or(
        style, @"letterSpacing", fallback.letter_spacing);
    float weight = 0.0F;
    if (finite_number(style[@"fontWeight"], weight)) {
        fallback.font_weight = std::clamp(static_cast<int>(std::lround(weight)), 100, 900);
    }
    if (const auto color = parse_color(style[@"color"])) fallback.color = *color;
    if ([style[@"textDecoration"] isKindOfClass:NSString.class]) {
        NSString* decoration = style[@"textDecoration"];
        fallback.underline = [decoration containsString:@"underline"];
        fallback.strikethrough = [decoration containsString:@"line-through"];
    }
    return fallback;
}

bool parse_linear_gradient_value(
    id raw,
    const std::string& name,
    std::vector<vellum::graphics::LinearGradient>& output,
    std::string* error) {
    if (raw == nil) return true;
    if (![raw isKindOfClass:NSDictionary.class]) {
        set_error(error, name + " must be an object");
        return false;
    }
    NSDictionary* source = raw;
    vellum::graphics::LinearGradient gradient;
    if ([source[@"kind"] isKindOfClass:NSString.class]) {
        NSString* kind = source[@"kind"];
        if ([kind isEqualToString:@"radial"]) gradient.kind = vellum::graphics::GradientKind::radial;
        else if ([kind isEqualToString:@"two-circle-radial"]) gradient.kind = vellum::graphics::GradientKind::two_circle_radial;
        else if ([kind isEqualToString:@"conic"]) gradient.kind = vellum::graphics::GradientKind::conic;
        else if (![kind isEqualToString:@"linear"]) { set_error(error, name + ".kind is invalid"); return false; }
    }
    if (!number_field(source, @"angle", 180.0F, gradient.angle_degrees,
            name + ".angle", error) ||
        !number_field(source, @"repeatLength", 0.0F, gradient.repeat_length,
            name + ".repeatLength", error)) {
        return false;
    }
    if (source[@"interpolation"] != nil) {
        if (![source[@"interpolation"] isKindOfClass:NSString.class]) {
            set_error(error, name + ".interpolation must be srgb, linear-srgb or oklab");
            return false;
        }
        const std::string interpolation = cpp_string(source[@"interpolation"]);
        if (interpolation == "srgb") {
            gradient.interpolation = vellum::graphics::GradientInterpolation::srgb;
        } else if (interpolation == "linear-srgb" || interpolation == "srgb-linear") {
            gradient.interpolation = vellum::graphics::GradientInterpolation::linear_srgb;
        } else if (interpolation == "oklab") {
            gradient.interpolation = vellum::graphics::GradientInterpolation::oklab;
        } else {
            set_error(error, name + ".interpolation must be srgb, linear-srgb or oklab");
            return false;
        }
    }
    if (source[@"colorSpace"] != nil) {
        if (![source[@"colorSpace"] isKindOfClass:NSString.class] ||
            [source[@"colorSpace"] length] == 0U) {
            set_error(error, name + ".colorSpace must be a non-empty string");
            return false;
        }
        gradient.color_space = cpp_string(source[@"colorSpace"]);
    }
    if (source[@"repeating"] != nil &&
        CFGetTypeID((__bridge CFTypeRef)source[@"repeating"]) != CFBooleanGetTypeID()) {
        set_error(error, name + ".repeating must be boolean");
        return false;
    }
    gradient.repeating = [source[@"repeating"] boolValue];
    if (![source[@"spread"] isKindOfClass:NSString.class] || source[@"spread"] == nil) {
        gradient.spread = gradient.repeating ? vellum::graphics::GradientSpread::repeat : vellum::graphics::GradientSpread::pad;
    } else {
        NSString* spread = source[@"spread"];
        if ([spread isEqualToString:@"repeat"]) gradient.spread = vellum::graphics::GradientSpread::repeat;
        else if ([spread isEqualToString:@"reflect"]) gradient.spread = vellum::graphics::GradientSpread::reflect;
        else if ([spread isEqualToString:@"pad"]) gradient.spread = vellum::graphics::GradientSpread::pad;
        else { set_error(error, name + ".spread is invalid"); return false; }
    }
    if (!number_field(source, @"centerX", 0.5F, gradient.center_x, name + ".centerX", error) ||
        !number_field(source, @"centerY", 0.5F, gradient.center_y, name + ".centerY", error) ||
        !number_field(source, @"radiusX", 0.0F, gradient.radius_x, name + ".radiusX", error) ||
        !number_field(source, @"radiusY", 0.0F, gradient.radius_y, name + ".radiusY", error) ||
        !number_field(source, @"focalX", 0.5F, gradient.focal_x, name + ".focalX", error) ||
        !number_field(source, @"focalY", 0.5F, gradient.focal_y, name + ".focalY", error)) return false;
    if (gradient.repeat_length < 0.0F) {
        set_error(error, name + ".repeatLength must be non-negative");
        return false;
    }
    NSArray* stops = array_or_empty(source[@"stops"]);
    if (stops.count < 2U) {
        set_error(error, name + " requires at least two stops");
        return false;
    }
    if (stops.count > kMaximumGradientStops) {
        set_error(error, name + " exceeds the 64-stop limit");
        return false;
    }
    float previous = -1.0F;
    for (id value in stops) {
        if (![value isKindOfClass:NSDictionary.class]) {
            set_error(error, name + " stops must be objects");
            return false;
        }
        NSDictionary* stop = value;
        float position = 0.0F;
        const auto color = parse_color(stop[@"color"]);
        if (!finite_number(stop[@"position"], position) || position < 0.0F ||
            position > 1.0F || position <= previous || !color) {
            set_error(error, name + " stops require increasing positions and colors");
            return false;
        }
        gradient.stops.push_back({.position = position, .color = *color});
        previous = position;
    }
    output.push_back(std::move(gradient));
    return true;
}

bool parse_linear_gradient(
    NSDictionary* style,
    std::vector<vellum::graphics::LinearGradient>& output,
    std::string* error) {
    id raw = style[@"backgroundGradient"] ?: style[@"backgroundLinearGradient"];
    return parse_linear_gradient_value(raw, raw == style[@"backgroundGradient"] ?
        "backgroundGradient" : "backgroundLinearGradient", output, error);
}

bool parse_box_shadow(
    NSDictionary* style,
    std::vector<vellum::graphics::BoxShadow>& output,
    std::string* error) {
    id raw = style[@"boxShadow"];
    if (raw == nil) return true;
    if (![raw isKindOfClass:NSDictionary.class]) {
        set_error(error, "boxShadow must be an object");
        return false;
    }
    NSDictionary* source = raw;
    const auto color = parse_color(source[@"color"]);
    if (!color) {
        set_error(error, "boxShadow requires a color");
        return false;
    }
    vellum::graphics::BoxShadow shadow{.color = *color};
    if (!number_field(source, @"offsetX", 0.0F, shadow.offset_x,
            "boxShadow.offsetX", error) ||
        !number_field(source, @"offsetY", 0.0F, shadow.offset_y,
            "boxShadow.offsetY", error) ||
        !number_field(source, @"blurRadius", 0.0F, shadow.blur_radius,
            "boxShadow.blurRadius", error) ||
        !number_field(source, @"spreadRadius", 0.0F, shadow.spread_radius,
            "boxShadow.spreadRadius", error)) {
        return false;
    }
    if (shadow.blur_radius < 0.0F || shadow.spread_radius < 0.0F) {
        set_error(error, "boxShadow blurRadius and spreadRadius must be non-negative");
        return false;
    }
    output.push_back(shadow);
    return true;
}

bool parse_opacity(NSDictionary* style, float& output, std::string* error) {
    output = 1.0F;
    id raw = style[@"opacity"];
    if (raw == nil) return true;
    if (!finite_number(raw, output)) {
        set_error(error, "opacity must be a finite number");
        return false;
    }
    output = std::clamp(output, 0.0F, 1.0F);
    return true;
}

bool parse_corner_radii(
    NSDictionary* style, float uniform,
    std::optional<vellum::graphics::CornerRadii>& output, std::string* error) {
    std::array<float, 4> radii{uniform, uniform, uniform, uniform};
    bool present = false;
    for (std::size_t index = 0; index < kCornerRadiusKeys.size(); ++index) {
        NSString* key = [NSString stringWithUTF8String:std::string(kCornerRadiusKeys[index]).c_str()];
        id raw = style[key];
        if (raw == nil) continue;
        float value = 0.0F;
        if (!finite_number(raw, value)) {
            set_error(error, std::string(kCornerRadiusKeys[index]) + " must be a finite number");
            return false;
        }
        radii[index] = std::max(0.0F, value);
        present = true;
    }
    if (present) output = vellum::graphics::CornerRadii{radii[0], radii[1], radii[2], radii[3]};
    return true;
}

std::string_view trimmed(std::string_view value) {
    const auto space = [](char character) {
        return character == ' ' || character == '\t' || character == '\n' ||
               character == '\r' || character == '\f';
    };
    while (!value.empty() && space(value.front())) value.remove_prefix(1);
    while (!value.empty() && space(value.back())) value.remove_suffix(1);
    return value;
}

enum class TransformUnit { number, length, angle };

// Lengths are unitless, px, or a percentage of `reference`; angles are deg,
// rad, grad, or turn, or a bare zero. Angles are returned in radians.
std::optional<float> transform_argument(
    std::string_view text, TransformUnit unit, float reference) {
    text = trimmed(text);
    if (text.empty()) return std::nullopt;
    // strtod reads the process locale's decimal point; a fixed C locale keeps
    // "1.5" meaning one and a half under a comma-decimal locale.
    static const locale_t c_locale = newlocale(LC_ALL_MASK, "C", nullptr);
    const std::string copy(text);
    char* end = nullptr;
    const double number = strtod_l(copy.c_str(), &end, c_locale);
    if (c_locale == nullptr || end == copy.c_str() || !std::isfinite(number)) {
        return std::nullopt;
    }
    std::string suffix(end);
    std::transform(suffix.begin(), suffix.end(), suffix.begin(),
        [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
    double value = number;
    if (unit == TransformUnit::number) {
        if (!suffix.empty()) return std::nullopt;
    } else if (unit == TransformUnit::length) {
        if (suffix == "%") {
            value = number / 100.0 * reference;
        } else if (!suffix.empty() && suffix != "px") {
            return std::nullopt;
        }
    } else {
        constexpr double kPi = 3.14159265358979323846;
        if (suffix == "deg") {
            value = number * kPi / 180.0;
        } else if (suffix == "grad") {
            value = number * kPi / 200.0;
        } else if (suffix == "turn") {
            value = number * 2.0 * kPi;
        } else if (suffix != "rad" && !(suffix.empty() && number == 0.0)) {
            return std::nullopt;
        }
    }
    if (std::abs(value) > static_cast<double>(std::numeric_limits<float>::max())) {
        return std::nullopt;
    }
    return static_cast<float>(value);
}

std::optional<vellum::graphics::Affine2D> transform_function(
    std::string_view name, const std::vector<std::string_view>& arguments,
    float width, float height) {
    using vellum::graphics::Affine2D;
    const auto count = arguments.size();
    std::vector<float> values;
    const auto read = [&](TransformUnit unit, std::initializer_list<float> references) {
        values.clear();
        auto reference = references.begin();
        for (const auto argument : arguments) {
            const float basis = reference != references.end() ? *reference++ : 0.0F;
            const auto value = transform_argument(argument, unit, basis);
            if (!value) return false;
            values.push_back(*value);
        }
        return true;
    };
    const auto one_or_two = count == 1U || count == 2U;
    if (name == "matrix") {
        if (count != 6U || !read(TransformUnit::number, {})) return std::nullopt;
        return Affine2D{values[0], values[1], values[2], values[3], values[4], values[5]};
    }
    if (name == "translate") {
        if (!one_or_two || !read(TransformUnit::length, {width, height})) return std::nullopt;
        return vellum::graphics::translation(values[0], count == 2U ? values[1] : 0.0F);
    }
    if (name == "translatex" || name == "translatey") {
        const bool x = name == "translatex";
        if (count != 1U || !read(TransformUnit::length, {x ? width : height})) {
            return std::nullopt;
        }
        return x ? vellum::graphics::translation(values[0], 0.0F)
                 : vellum::graphics::translation(0.0F, values[0]);
    }
    if (name == "scale") {
        if (!one_or_two || !read(TransformUnit::number, {})) return std::nullopt;
        return Affine2D{.a = values[0], .d = count == 2U ? values[1] : values[0]};
    }
    if (name == "scalex" || name == "scaley") {
        if (count != 1U || !read(TransformUnit::number, {})) return std::nullopt;
        return name == "scalex" ? Affine2D{.a = values[0]} : Affine2D{.d = values[0]};
    }
    if (name == "rotate") {
        if (count != 1U || !read(TransformUnit::angle, {})) return std::nullopt;
        const float cosine = std::cos(values[0]);
        const float sine = std::sin(values[0]);
        return Affine2D{.a = cosine, .b = sine, .c = -sine, .d = cosine};
    }
    if (name == "skew") {
        if (!one_or_two || !read(TransformUnit::angle, {})) return std::nullopt;
        return Affine2D{
            .b = count == 2U ? std::tan(values[1]) : 0.0F,
            .c = std::tan(values[0]),
        };
    }
    if (name == "skewx" || name == "skewy") {
        if (count != 1U || !read(TransformUnit::angle, {})) return std::nullopt;
        return name == "skewx" ? Affine2D{.c = std::tan(values[0])}
                               : Affine2D{.b = std::tan(values[0])};
    }
    return std::nullopt;
}

enum class TransformStatus { parsed, dropped, failed };

// Parses a CSS transform list, composed left to right and pivoting about the
// box centre. An identity result leaves `output` empty. Text that does not
// parse is `dropped` with `reason`; only a non-finite composition `failed`.
TransformStatus parse_transform(
    NSDictionary* style, float width, float height,
    std::optional<vellum::graphics::Affine2D>& output, std::string& reason) {
    id raw = style[@"transform"];
    if (raw == nil) return TransformStatus::parsed;
    if (![raw isKindOfClass:NSString.class]) {
        reason = "transform is not a string of CSS transform functions";
        return TransformStatus::dropped;
    }
    const std::string source = cpp_string(raw);
    if (source.size() > kMaximumTransformBytes) {
        reason = "transform exceeds the 1024-byte limit";
        return TransformStatus::dropped;
    }
    vellum::graphics::Affine2D composed{};
    std::string_view rest = trimmed(source);
    std::size_t functions = 0;
    std::string lowered(rest);
    std::transform(lowered.begin(), lowered.end(), lowered.begin(),
        [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
    if (lowered == "none") rest = {};
    while (!rest.empty()) {
        const auto open = rest.find('(');
        const auto close = rest.find(')');
        if (open == std::string_view::npos || close == std::string_view::npos ||
            close < open) {
            reason = "transform has text that is not a function call: " + std::string(rest);
            return TransformStatus::dropped;
        }
        if (++functions > kMaximumTransformFunctions) {
            reason = "transform has more than 32 functions";
            return TransformStatus::dropped;
        }
        std::string name(trimmed(rest.substr(0, open)));
        std::transform(name.begin(), name.end(), name.begin(),
            [](unsigned char character) { return static_cast<char>(std::tolower(character)); });
        std::vector<std::string_view> arguments;
        const auto body = rest.substr(open + 1U, close - open - 1U);
        auto inside = body;
        while (true) {
            const auto comma = inside.find(',');
            arguments.push_back(inside.substr(0, comma));
            if (comma == std::string_view::npos) break;
            inside.remove_prefix(comma + 1U);
        }
        const auto function = transform_function(name, arguments, width, height);
        if (!function) {
            reason = "transform function " + std::string(trimmed(rest.substr(0, open))) + "(" +
                std::string(body) + ") is not supported";
            return TransformStatus::dropped;
        }
        composed = vellum::graphics::concat(composed, *function);
        rest = trimmed(rest.substr(close + 1U));
    }
    if (!vellum::graphics::is_finite(composed)) {
        reason = "transform does not compose to a finite matrix";
        return TransformStatus::failed;
    }
    if (vellum::graphics::is_identity(composed)) return TransformStatus::parsed;
    const float center_x = width * 0.5F;
    const float center_y = height * 0.5F;
    output = vellum::graphics::concat(
        vellum::graphics::translation(center_x, center_y),
        vellum::graphics::concat(composed,
            vellum::graphics::translation(-center_x, -center_y)));
    return TransformStatus::parsed;
}

NSDictionary* dictionary_or_empty(id value) {
    return [value isKindOfClass:NSDictionary.class]
        ? static_cast<NSDictionary*>(value)
        : @{};
}

NSArray* array_or_empty(id value) {
    return [value isKindOfClass:NSArray.class]
        ? static_cast<NSArray*>(value)
        : @[];
}

struct MaterializeContext final {
    std::set<std::string> identities;
    std::size_t nodes = 0;
    std::vector<Interaction>* interactions = nullptr;
    std::vector<TextInputControl>* text_inputs = nullptr;
    std::vector<AccessibilityNode>* accessibility_nodes = nullptr;
    std::vector<LoweringDiagnostic>* diagnostics = nullptr;
};

template <typename Consumed>
void report_dropped_style(
    NSDictionary* style, const std::string& node_id, std::string_view type,
    Consumed consumed, MaterializeContext& context) {
    NSArray* keys = [style.allKeys sortedArrayUsingSelector:@selector(compare:)];
    for (id key in keys) {
        if (![key isKindOfClass:NSString.class]) continue;
        const std::string name = cpp_string(key);
        if (consumed(name)) continue;
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(vellum::graphics::kStyleDroppedCode),
            .node_id = node_id,
            .property = name,
            .message = "style key " + name + " is not supported on " + std::string(type) +
                " nodes and was ignored",
        });
    }
}

std::string inferred_accessibility_role(NSString* type, NSDictionary* source) {
    if ([source[@"accessibilityRole"] isKindOfClass:NSString.class]) {
        return cpp_string(source[@"accessibilityRole"]);
    }
    if ([type isEqualToString:@"text-input"]) return "text-field";
    if ([type isEqualToString:@"button"]) return "button";
    if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) return "text";
    if ([type isEqualToString:@"image"]) return "image";
    return "group";
}

float default_height(NSString* type, NSDictionary* style, NSDictionary* source) {
    if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) {
        float font_size = number_or(style, @"fontSize", 14.0F);
        for (id child_value in array_or_empty(source[@"children"])) {
            if (![child_value isKindOfClass:NSDictionary.class]) continue;
            NSDictionary* child = static_cast<NSDictionary*>(child_value);
            if (![child[@"type"] isEqual:@"text-run"]) continue;
            font_size = std::max(
                font_size,
                number_or(dictionary_or_empty(child[@"style"]), @"fontSize", font_size));
        }
        return font_size * kTextLineHeightMultiplier;
    }
    if ([type isEqualToString:@"button"]) return kDefaultButtonHeight;
    if ([type isEqualToString:@"text-input"]) return kDefaultTextInputHeight;
    return kDefaultGenericHeight;
}

std::string direct_text(NSDictionary* source) {
    if ([source[@"text"] isKindOfClass:NSString.class]) {
        return cpp_string(source[@"text"]);
    }
    std::string result;
    for (id child in array_or_empty(source[@"children"])) {
        if (![child isKindOfClass:NSDictionary.class]) continue;
        NSDictionary* child_dictionary = static_cast<NSDictionary*>(child);
        if ([child_dictionary[@"type"] isEqual:@"text-run"] &&
            [child_dictionary[@"text"] isKindOfClass:NSString.class]) {
            result += cpp_string(child_dictionary[@"text"]);
        }
    }
    return result;
}

std::optional<std::string> json_object(id value) {
    if (![value isKindOfClass:NSDictionary.class] ||
        ![NSJSONSerialization isValidJSONObject:value]) {
        return std::nullopt;
    }
    NSError* error = nil;
    NSData* data = [NSJSONSerialization dataWithJSONObject:value
                                                   options:NSJSONWritingSortedKeys
                                                     error:&error];
    if (data == nil || error != nil || data.length > kMaximumJsonBytes) {
        return std::nullopt;
    }
    NSString* text = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding];
    return text == nil ? std::nullopt : std::optional<std::string>{cpp_string(text)};
}

bool reject_text_run(
    MaterializeContext& context, const std::string& node_id, std::string property,
    std::string message, std::string* error) {
    context.diagnostics->push_back({
        .severity = LoweringDiagnostic::Severity::error,
        .disposition = LoweringDiagnostic::Disposition::unsupported,
        .code = std::string(vellum::graphics::kInvalidTextRunCode),
        .node_id = node_id,
        .property = std::move(property),
        .message = message,
    });
    set_error(error, std::move(message));
    return false;
}

bool reject_path(
    MaterializeContext& context, const std::string& node_id, std::string property,
    std::string message, std::string* error) {
    context.diagnostics->push_back({
        .severity = LoweringDiagnostic::Severity::error,
        .disposition = LoweringDiagnostic::Disposition::unsupported,
        .code = std::string(vellum::graphics::kInvalidPathCode),
        .node_id = node_id,
        .property = std::move(property),
        .message = message,
    });
    set_error(error, std::move(message));
    return false;
}

template <std::size_t Size>
std::optional<std::size_t> keyword(id value, const std::array<std::string_view, Size>& names) {
    if (![value isKindOfClass:NSString.class]) return std::nullopt;
    const auto found = std::find(names.begin(), names.end(), cpp_string(value));
    if (found == names.end()) return std::nullopt;
    return static_cast<std::size_t>(found - names.begin());
}

constexpr std::array<std::string_view, 19> kPathAttributes{
    "type", "id", "children", "style", "events", "accessibilityLabel", "accessibilityValue",
    "accessibilityRole", "accessibilityState", "d", "fillRule", "fill", "stroke", "strokeWidth",
    "strokeLineJoin", "strokeLineCap", "strokeMiterLimit", "strokeDasharray", "strokeDashoffset",
};

void report_dropped_path_attributes(
    NSDictionary* source, const std::string& node_id, MaterializeContext& context) {
    NSArray* keys = [source.allKeys sortedArrayUsingSelector:@selector(compare:)];
    for (id key in keys) {
        if (![key isKindOfClass:NSString.class]) continue;
        const std::string name = cpp_string(key);
        if (contains(kPathAttributes, name)) continue;
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(vellum::graphics::kStyleDroppedCode),
            .node_id = node_id,
            .property = name,
            .message = "attribute " + name + " is not supported on path nodes and was ignored",
        });
    }
}

// Path paint follows SVG: the fill defaults to black, the stroke to none, and
// the stroke width to 1.
bool materialize_path(
    NSDictionary* source, const std::string& identity, MaterializeContext& context,
    vellum::graphics::SceneNode& output, std::string* error) {
    using vellum::graphics::Color;
    const auto reject = [&](std::string property, const std::string& problem) {
        return reject_path(context, identity, std::move(property),
            "path " + identity + " " + problem, error);
    };
    id children = source[@"children"];
    if (children != nil && (![children isKindOfClass:NSArray.class] ||
                            static_cast<NSArray*>(children).count != 0U)) {
        return reject("children", "does not accept children");
    }
    if (![source[@"d"] isKindOfClass:NSString.class]) {
        return reject("d", "requires string path data in d");
    }
    vellum::graphics::PathGeometry path;
    std::string reason;
    if (!vellum::graphics::parse_svg_path_data(cpp_string(source[@"d"]), path, &reason)) {
        return reject("d", "has invalid path data: " + reason);
    }
    if (source[@"fillRule"] != nil) {
        const auto rule = keyword(source[@"fillRule"],
            std::array<std::string_view, 2>{"nonzero", "evenodd"});
        if (!rule) return reject("fillRule", "fillRule must be nonzero or evenodd");
        path.fill_rule = static_cast<vellum::graphics::FillRule>(*rule);
    }
    const Color none = Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
    output.fill = Color::hex(0x000000);
    output.fill_gradients.clear();
    output.box_shadows.clear();
    output.corner_radius = 0.0F;
    output.corner_radii.reset();
    if (id fill = source[@"fill"]; fill != nil) {
        if ([fill isEqual:@"none"]) {
            output.fill = none;
        } else if ([fill isKindOfClass:NSDictionary.class]) {
            output.fill = none;
            std::string why;
            if (!parse_linear_gradient_value(fill, "fill", output.fill_gradients, &why)) {
                return reject("fill", why);
            }
        } else if (const auto color = parse_color(fill)) {
            output.fill = *color;
        } else {
            return reject("fill", "fill must be a colour, none or a linear gradient");
        }
    }
    auto& stroke = path.stroke;
    if (id paint = source[@"stroke"]; paint != nil && ![paint isEqual:@"none"]) {
        if ([paint isKindOfClass:NSDictionary.class]) {
            std::vector<vellum::graphics::LinearGradient> gradients;
            std::string why;
            if (!parse_linear_gradient_value(paint, "stroke", gradients, &why)) return reject("stroke", why);
            if (gradients.size() != 1U) return reject("stroke", "requires one gradient");
            stroke.gradient = gradients.front();
            stroke.color = none;
        } else {
            const auto color = parse_color(paint);
            if (!color) return reject("stroke", "stroke must be a colour or none");
            stroke.color = *color;
        }
    }
    stroke.width = 1.0F;
    const auto number = [&](NSString* key, float& value) {
        id raw = source[key];
        return raw == nil || finite_number(raw, value);
    };
    if (!number(@"strokeWidth", stroke.width) || stroke.width < 0.0F) {
        return reject("strokeWidth", "strokeWidth must be a finite non-negative number");
    }
    if (!number(@"strokeMiterLimit", stroke.miter_limit) || stroke.miter_limit < 1.0F) {
        return reject("strokeMiterLimit", "strokeMiterLimit must be a finite number of at least 1");
    }
    if (!number(@"strokeDashoffset", stroke.dash_offset)) {
        return reject("strokeDashoffset", "strokeDashoffset must be a finite number");
    }
    if (source[@"strokeLineJoin"] != nil) {
        const auto join = keyword(source[@"strokeLineJoin"],
            std::array<std::string_view, 3>{"miter", "round", "bevel"});
        if (!join) return reject("strokeLineJoin", "strokeLineJoin must be miter, round or bevel");
        stroke.join = static_cast<vellum::graphics::StrokeJoin>(*join);
    }
    if (source[@"strokeLineCap"] != nil) {
        const auto cap = keyword(source[@"strokeLineCap"],
            std::array<std::string_view, 3>{"butt", "round", "square"});
        if (!cap) return reject("strokeLineCap", "strokeLineCap must be butt, round or square");
        stroke.cap = static_cast<vellum::graphics::StrokeCap>(*cap);
    }
    if (id dashes = source[@"strokeDasharray"]; dashes != nil) {
        if (![dashes isKindOfClass:NSArray.class] ||
            static_cast<NSArray*>(dashes).count > vellum::graphics::kMaxPathDashes) {
            return reject("strokeDasharray", "strokeDasharray must be an array of at most " +
                std::to_string(vellum::graphics::kMaxPathDashes) + " numbers");
        }
        for (id dash in static_cast<NSArray*>(dashes)) {
            float length = 0.0F;
            if (!finite_number(dash, length) || length < 0.0F) {
                return reject("strokeDasharray",
                    "strokeDasharray entries must be finite non-negative numbers");
            }
            stroke.dash_array.push_back(length);
        }
    }
    vellum::graphics::PathGeometry unstroked = path;
    unstroked.stroke = {};
    if (!vellum::graphics::path_is_valid(unstroked, &reason)) {
        return reject("d", "cannot be drawn: " + reason);
    }
    if (!vellum::graphics::path_is_valid(path, &reason)) {
        return reject("strokeDasharray", "cannot be drawn: " + reason);
    }
    report_dropped_path_attributes(source, identity, context);
    output.kind = vellum::graphics::SceneNode::Kind::path;
    output.path = std::make_shared<const vellum::graphics::PathGeometry>(std::move(path));
    return true;
}

bool reject_image(
    MaterializeContext& context, const std::string& node_id, std::string property,
    std::string message, std::string* error) {
    context.diagnostics->push_back({
        .severity = LoweringDiagnostic::Severity::error,
        .disposition = LoweringDiagnostic::Disposition::unsupported,
        .code = std::string(vellum::graphics::kInvalidImageCode),
        .node_id = node_id,
        .property = std::move(property),
        .message = message,
    });
    set_error(error, std::move(message));
    return false;
}

// An image whose source is a SHA-256 content hash. The asset itself is
// supplied by the host in `Scene::images`; the renderer reports a missing one.
bool materialize_image(
    NSDictionary* source, const std::string& identity, std::string content_hash,
    MaterializeContext& context, vellum::graphics::SceneNode& output, std::string* error) {
    const auto reject = [&](std::string property, const std::string& problem) {
        return reject_image(context, identity, std::move(property),
            "image " + identity + " " + problem, error);
    };
    id children = source[@"children"];
    if (children != nil && (![children isKindOfClass:NSArray.class] ||
                            static_cast<NSArray*>(children).count != 0U)) {
        return reject("children", "does not accept children");
    }
    vellum::graphics::ImageReference reference{.content_hash = std::move(content_hash)};
    if (source[@"fit"] != nil) {
        const auto fit = keyword(source[@"fit"],
            std::array<std::string_view, 4>{"fill", "contain", "cover", "none"});
        if (!fit) return reject("fit", "fit must be fill, contain, cover or none");
        reference.fit = static_cast<vellum::graphics::ImageFit>(*fit);
    }
    if (source[@"sampling"] != nil) {
        const auto sampling = keyword(source[@"sampling"],
            std::array<std::string_view, 2>{"nearest", "linear"});
        if (!sampling) return reject("sampling", "sampling must be nearest or linear");
        reference.sampling = static_cast<vellum::graphics::ImageSampling>(*sampling);
    }
    if (id raw = source[@"sourceRect"]; raw != nil) {
        if (![raw isKindOfClass:NSDictionary.class]) {
            return reject("sourceRect", "sourceRect must be an object");
        }
        NSDictionary* rect = raw;
        vellum::graphics::Rect value;
        if (!finite_number(rect[@"x"], value.x) || !finite_number(rect[@"y"], value.y) ||
            !finite_number(rect[@"width"], value.width) ||
            !finite_number(rect[@"height"], value.height) || value.width < 0.0F ||
            value.height < 0.0F) {
            return reject("sourceRect",
                "sourceRect needs finite x, y and non-negative width and height");
        }
        reference.source_rect = value;
    }
    output.kind = vellum::graphics::SceneNode::Kind::image;
    output.fill_gradients.clear();
    output.box_shadows.clear();
    output.image = std::move(reference);
    return true;
}

// Clip, mask and isolation follow CSS: `overflow: hidden` (or `clip`, or
// `auto` and `scroll` without scrolling) clips the subtree to the border box
// with its radii, leaving the node's own fill and shadows whole; text, path
// and image nodes draw their content as their own paint, so theirs is clipped.
// `clipPath` is SVG path data in the box's coordinates that clips the node
// and its subtree and wins over `overflow`, `maskMode` marks the node as a
// mask for its siblings, and `isolation: isolate` forces a group.
bool parse_grouping(
    NSDictionary* style, const std::string& identity, const StyleUse& use,
    MaterializeContext& context, vellum::graphics::SceneNode& output, std::string* error) {
    const auto reject = [&](std::string property, const std::string& problem) {
        set_error(error, "node " + identity + " " + property + " " + problem);
        return false;
    };
    bool clip_box = false;
    if (id overflow = style[@"overflow"]; overflow != nil) {
        const auto value = keyword(overflow,
            std::array<std::string_view, 5>{"visible", "hidden", "clip", "auto", "scroll"});
        if (!value) return reject("overflow", "must be visible, hidden, clip, auto or scroll");
        clip_box = *value != 0U;
        if (*value >= 3U) {
            context.diagnostics->push_back({
                .severity = LoweringDiagnostic::Severity::warning,
                .disposition = LoweringDiagnostic::Disposition::lowered,
                .code = std::string(vellum::graphics::kStyleDroppedCode),
                .node_id = identity,
                .property = "overflow",
                .message = "overflow " + cpp_string(overflow) +
                    " clips like hidden; the node does not scroll",
            });
        }
    }
    if (id clip_path = style[@"clipPath"]; clip_path != nil) {
        if (![clip_path isKindOfClass:NSString.class]) {
            return reject_path(context, identity, "clipPath",
                "node " + identity + " clipPath must be SVG path data", error);
        }
        vellum::graphics::PathGeometry path;
        std::string reason;
        if (!vellum::graphics::parse_svg_path_data(cpp_string(clip_path), path, &reason) ||
            !vellum::graphics::path_is_valid(path, &reason)) {
            return reject_path(context, identity, "clipPath",
                "node " + identity + " has an invalid clipPath: " + reason, error);
        }
        output.clip = vellum::graphics::NodeClip{
            .path = std::make_shared<const vellum::graphics::PathGeometry>(std::move(path))};
        if (clip_box) {
            context.diagnostics->push_back({
                .severity = LoweringDiagnostic::Severity::warning,
                .disposition = LoweringDiagnostic::Disposition::lowered,
                .code = std::string(vellum::graphics::kStyleDroppedCode),
                .node_id = identity,
                .property = "overflow",
                .message = "overflow hidden was ignored because clipPath clips the node",
            });
        }
    } else if (clip_box) {
        using Kind = vellum::graphics::SceneNode::Kind;
        const bool content_is_own_paint =
            output.kind == Kind::text || output.kind == Kind::path ||
            output.kind == Kind::image;
        output.clip = vellum::graphics::NodeClip{
            .rect = {0.0F, 0.0F, output.bounds.width, output.bounds.height},
            .radii = vellum::graphics::resolved_corner_radii(output),
            .clips_own_paint = content_is_own_paint,
        };
        if (content_is_own_paint && !output.box_shadows.empty()) {
            context.diagnostics->push_back({
                .severity = LoweringDiagnostic::Severity::warning,
                .disposition = LoweringDiagnostic::Disposition::lowered,
                .code = std::string(vellum::graphics::kStyleDroppedCode),
                .node_id = identity,
                .property = "boxShadow",
                .message = "the overflow clip of a node whose content is its own paint also "
                    "clips its box shadow",
            });
        }
    }
    if (id mode = style[@"maskMode"]; mode != nil && !use.root) {
        const auto value = keyword(mode, std::array<std::string_view, 2>{"alpha", "luminance"});
        if (!value) return reject("maskMode", "must be alpha or luminance");
        output.mask = static_cast<vellum::graphics::MaskMode>(*value);
    }
    if (id isolation = style[@"isolation"]; isolation != nil) {
        const auto value = keyword(isolation, std::array<std::string_view, 2>{"auto", "isolate"});
        if (!value) return reject("isolation", "must be auto or isolate");
        output.isolate = *value == 1U;
    }
    return true;
}

bool validate_attributed_run(
    NSDictionary* run, const std::string& parent_id, std::size_t depth,
    MaterializeContext& context, std::string* error) {
    if (depth + 1U > kMaximumDepth || ++context.nodes > kMaximumNodes) {
        set_error(error, "authoring tree exceeds the node or depth limit");
        return false;
    }
    const std::string identity =
        [run[@"id"] isKindOfClass:NSString.class] ? cpp_string(run[@"id"]) : std::string{};
    if (identity.empty()) {
        return reject_text_run(context, parent_id, "id",
            "text-run under " + parent_id + " requires a non-empty string id", error);
    }
    if (!context.identities.insert(identity).second) {
        return reject_text_run(context, identity, "id",
            "authoring tree contains an empty or duplicate node id: " + identity, error);
    }
    if (![run[@"text"] isKindOfClass:NSString.class]) {
        return reject_text_run(context, identity, "text",
            "text-run " + identity + " requires string text", error);
    }
    if (run[@"children"] != nil && (![run[@"children"] isKindOfClass:NSArray.class] ||
                                    static_cast<NSArray*>(run[@"children"]).count != 0U)) {
        return reject_text_run(context, identity, "children",
            "text-run " + identity + " does not accept children", error);
    }
    if (run[@"style"] != nil && ![run[@"style"] isKindOfClass:NSDictionary.class]) {
        return reject_text_run(context, identity, "style",
            "text-run " + identity + " style must be an object", error);
    }
    return true;
}

bool materialize_node(
    NSDictionary* source,
    const vellum::graphics::Rect& proposed,
    float absolute_x,
    float absolute_y,
    const vellum::graphics::Affine2D& parent_transform,
    std::size_t depth,
    MaterializeContext& context,
    vellum::graphics::SceneNode& output,
    std::string* error) {
    if (depth > kMaximumDepth || ++context.nodes > kMaximumNodes) {
        set_error(error, "authoring tree exceeds the node or depth limit");
        return false;
    }
    if (![source[@"type"] isKindOfClass:NSString.class] ||
        ![source[@"id"] isKindOfClass:NSString.class]) {
        set_error(error, "each authoring node requires string type and id fields");
        return false;
    }
    NSString* type = source[@"type"];
    const std::string identity = cpp_string(source[@"id"]);
    if (identity.empty() || !context.identities.insert(identity).second) {
        set_error(error, "authoring tree contains an empty or duplicate node id: " + identity);
        return false;
    }
    const std::string type_name = cpp_string(type);
    if (!contains(kIntrinsicTypes, type_name)) {
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::error,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(vellum::graphics::kUnknownNodeTypeCode),
            .node_id = identity,
            .property = "type",
            .message = "authoring node " + identity + " has unsupported type " + type_name,
        });
        set_error(error, context.diagnostics->back().message);
        return false;
    }
    NSDictionary* style = dictionary_or_empty(source[@"style"]);
    NSDictionary* events = dictionary_or_empty(source[@"events"]);
    const bool is_text_input = [type isEqualToString:@"text-input"];
    NSString* input_value = nil;
    NSString* input_placeholder = nil;
    NSUInteger selection_start = 0U;
    NSUInteger selection_end = 0U;
    if (is_text_input) {
        NSNumber* primitive_version = [source[@"primitiveVersion"]
            isKindOfClass:NSNumber.class] ? source[@"primitiveVersion"] : nil;
        input_value = [source[@"value"] isKindOfClass:NSString.class]
            ? source[@"value"] : nil;
        input_placeholder = [source[@"placeholder"] isKindOfClass:NSString.class]
            ? source[@"placeholder"] : nil;
        if (primitive_version == nil || primitive_version.integerValue != 1 ||
            input_value == nil ||
            [input_value lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >
                kMaximumTextInputBytes ||
            (source[@"placeholder"] != nil && input_placeholder == nil) ||
            [input_placeholder lengthOfBytesUsingEncoding:NSUTF8StringEncoding] >
                kMaximumPlaceholderBytes ||
            ![events[@"change"] isKindOfClass:NSString.class]) {
            set_error(error,
                "text-input requires primitiveVersion 1, bounded string value/placeholder, "
                "and a change action");
            return false;
        }
        if (array_or_empty(source[@"children"]).count != 0U) {
            set_error(error, "text-input v1 does not accept retained-tree children");
            return false;
        }
        selection_start = input_value.length;
        selection_end = input_value.length;
        if (source[@"selection"] != nil) {
            if (![source[@"selection"] isKindOfClass:NSDictionary.class]) {
                set_error(error, "text-input selection must be an object");
                return false;
            }
            NSDictionary* selection = source[@"selection"];
            NSNumber* start = [selection[@"start"] isKindOfClass:NSNumber.class]
                ? selection[@"start"] : nil;
            NSNumber* end = [selection[@"end"] isKindOfClass:NSNumber.class]
                ? selection[@"end"] : nil;
            if (start == nil || end == nil || start.doubleValue != start.unsignedLongLongValue ||
                end.doubleValue != end.unsignedLongLongValue ||
                start.unsignedLongLongValue > end.unsignedLongLongValue ||
                end.unsignedLongLongValue > input_value.length) {
                set_error(error,
                    "text-input selection must be an ordered UTF-16 range within value");
                return false;
            }
            selection_start = start.unsignedIntegerValue;
            selection_end = end.unsignedIntegerValue;
        }
    }
    output.id = identity;
    output.bounds = proposed;
    output.corner_radius = std::max(0.0F, number_or(style, @"borderRadius", 0.0F));
    output.fill = parse_color(style[@"backgroundColor"])
        .value_or(vellum::graphics::Color::rgba(0.0F, 0.0F, 0.0F, 0.0F));
    if (!parse_linear_gradient(style, output.fill_gradients, error) ||
        !parse_box_shadow(style, output.box_shadows, error) ||
        !parse_corner_radii(style, output.corner_radius, output.corner_radii, error) ||
        !parse_opacity(style, output.opacity, error)) {
        return false;
    }
    std::string transform_reason;
    const auto transform_status = parse_transform(
        style, proposed.width, proposed.height, output.transform, transform_reason);
    if (transform_status == TransformStatus::failed) {
        set_error(error, transform_reason);
        return false;
    }
    if (transform_status == TransformStatus::dropped) {
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(vellum::graphics::kStyleDroppedCode),
            .node_id = identity,
            .property = "transform",
            .message = transform_reason + "; the node was drawn untransformed",
        });
    }
    const vellum::graphics::Rect absolute_box{
        absolute_x + proposed.x, absolute_y + proposed.y, proposed.width, proposed.height};
    vellum::graphics::Affine2D node_transform = parent_transform;
    if (output.transform) {
        node_transform = vellum::graphics::concat(parent_transform, vellum::graphics::concat(
            vellum::graphics::translation(absolute_box.x, absolute_box.y),
            vellum::graphics::concat(*output.transform,
                vellum::graphics::translation(-absolute_box.x, -absolute_box.y))));
    }
    const auto frame_box = vellum::graphics::transformed_bounds(node_transform, absolute_box);
    const bool hit_testable =
        node_transform.a * node_transform.d - node_transform.b * node_transform.c != 0.0F;
    std::optional<std::string> image_hash;
    if (type_name == "image" && [source[@"source"] isKindOfClass:NSString.class]) {
        image_hash = vellum::graphics::normalize_content_hash(cpp_string(source[@"source"]));
    }
    StyleUse style_use{
        .root = depth == 0U,
        .paints_text_background =
            !output.fill_gradients.empty() || !output.box_shadows.empty(),
        .draws_image = image_hash.has_value(),
        .clips_to_box = [style[@"overflow"] isKindOfClass:NSString.class] &&
            ![style[@"overflow"] isEqual:@"visible"] && style[@"clipPath"] == nil,
    };
    for (id child in array_or_empty(source[@"children"])) {
        if (!skipped_text_run(type, child)) style_use.lays_out_children = true;
    }
    report_dropped_style(style, identity, type_name,
        [&](std::string_view key) { return style_key_consumed(type_name, key, style_use); },
        context);
    if (type_name == "image" && !image_hash && source[@"source"] != nil) {
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::lowered,
            .code = std::string(vellum::graphics::kNodeContentDroppedCode),
            .node_id = identity,
            .property = "source",
            .message = "image source is not a SHA-256 content hash, so it names no asset; "
                       "only its box was kept",
        });
    } else if ((type_name == "image" && !image_hash) || type_name == "canvas") {
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::lowered,
            .code = std::string(vellum::graphics::kNodeContentDroppedCode),
            .node_id = identity,
            .property = "type",
            .message = type_name + " content has no scene representation; only its box was kept",
        });
    }

    if (type_name == "path") {
        if (!materialize_path(source, identity, context, output, error)) return false;
    } else if (image_hash) {
        if (!materialize_image(source, identity, *image_hash, context, output, error)) {
            return false;
        }
    } else if ([type isEqualToString:@"custom"]) {
        if (![source[@"component"] isKindOfClass:NSString.class]) {
            set_error(error, "custom authoring node requires a component identifier");
            return false;
        }
        const auto properties = json_object(source[@"properties"]);
        if (!properties) {
            set_error(error, "custom authoring node properties must be a bounded JSON object");
            return false;
        }
        output.kind = vellum::graphics::SceneNode::Kind::custom;
        output.custom_component = cpp_string(source[@"component"]);
        output.custom_properties_json = *properties;
    } else if ([type isEqualToString:@"text"] || [type isEqualToString:@"text-run"]) {
        if ([type isEqualToString:@"text"] &&
            [source[@"text"] isKindOfClass:NSString.class] &&
            array_or_empty(source[@"children"]).count > 0U) {
            set_error(error, "text authoring nodes accept text or attributed children, not both");
            return false;
        }
        output.kind = vellum::graphics::SceneNode::Kind::text;
        output.text = direct_text(source);
        const auto base_style = text_style(style);
        output.font_family = base_style.font_family;
        output.font_weight = base_style.font_weight;
        output.font_size = base_style.font_size;
        output.letter_spacing = base_style.letter_spacing;
        output.fill = base_style.color;
        output.underline = base_style.underline;
        output.strikethrough = base_style.strikethrough;
        if ([type isEqualToString:@"text"]) {
            for (id child_value in array_or_empty(source[@"children"])) {
                if (![child_value isKindOfClass:NSDictionary.class]) continue;
                NSDictionary* child = child_value;
                if (![child[@"type"] isEqual:@"text-run"] ||
                    ![child[@"text"] isKindOfClass:NSString.class]) {
                    continue;
                }
                output.text_runs.push_back({
                    .text = cpp_string(child[@"text"]),
                    .style = text_style(dictionary_or_empty(child[@"style"]), base_style),
                });
            }
        }
    } else if (is_text_input) {
        output.kind = vellum::graphics::SceneNode::Kind::rectangle;
        output.fill = parse_color(style[@"backgroundColor"])
            .value_or(vellum::graphics::Color::hex(0xFFFFFF));
        output.corner_radius = std::max(output.corner_radius, 6.0F);
    } else if ([type isEqualToString:@"button"] ||
               parse_color(style[@"backgroundColor"]).has_value() ||
               !output.fill_gradients.empty() || !output.box_shadows.empty()) {
        output.kind = vellum::graphics::SceneNode::Kind::rectangle;
        if ([type isEqualToString:@"button"] &&
            !parse_color(style[@"backgroundColor"]).has_value()) {
            output.fill = vellum::graphics::Color::hex(0x14B8A6);
            output.corner_radius = std::max(output.corner_radius, 10.0F);
        }
    } else {
        output.kind = vellum::graphics::SceneNode::Kind::group;
    }

    if (!parse_grouping(style, identity, style_use, context, output, error)) return false;

    for (NSString* event in events) {
        if (![event isKindOfClass:NSString.class] ||
            ![events[event] isKindOfClass:NSString.class]) {
            set_error(error, "authoring node events must map strings to action strings");
            return false;
        }
        if (!hit_testable) continue;
        context.interactions->push_back({
            .node_id = identity,
            .event = cpp_string(event),
            .action = cpp_string(events[event]),
            .bounds = frame_box,
        });
    }

    if (is_text_input && hit_testable) {
        const auto action = [&](NSString* name) -> std::string {
            return [events[name] isKindOfClass:NSString.class]
                ? cpp_string(events[name]) : std::string{};
        };
        context.text_inputs->push_back({
            .node_id = identity,
            .value = cpp_string(input_value),
            .placeholder = cpp_string(input_placeholder),
            .change_action = action(@"change"),
            .submit_action = action(@"submit"),
            .key_down_action = action(@"keyDown"),
            .selection_change_action = action(@"selectionChange"),
            .composition_start_action = action(@"compositionStart"),
            .composition_update_action = action(@"compositionUpdate"),
            .composition_end_action = action(@"compositionEnd"),
            .selection_start = selection_start,
            .selection_end = selection_end,
            .bounds = frame_box,
        });
    }

    const bool semantic = is_text_input || [type isEqualToString:@"button"] ||
        [source[@"accessibilityLabel"] isKindOfClass:NSString.class] ||
        [source[@"accessibilityValue"] isKindOfClass:NSString.class] ||
        source[@"accessibilityRole"] != nil || source[@"accessibilityState"] != nil;
    if (semantic) {
        if ((source[@"accessibilityLabel"] != nil &&
             ![source[@"accessibilityLabel"] isKindOfClass:NSString.class]) ||
            (source[@"accessibilityValue"] != nil &&
             ![source[@"accessibilityValue"] isKindOfClass:NSString.class]) ||
            (source[@"accessibilityRole"] != nil &&
             ![source[@"accessibilityRole"] isKindOfClass:NSString.class]) ||
            (source[@"accessibilityState"] != nil &&
             ![source[@"accessibilityState"] isKindOfClass:NSDictionary.class])) {
            set_error(error, "accessibility label/value/role/state has an invalid type");
            return false;
        }
        NSDictionary* semantic_state = dictionary_or_empty(source[@"accessibilityState"]);
        AccessibilityNode node{
            .node_id = identity,
            .role = inferred_accessibility_role(type, source),
            .label = [source[@"accessibilityLabel"] isKindOfClass:NSString.class]
                ? cpp_string(source[@"accessibilityLabel"]) : direct_text(source),
            .value = [source[@"accessibilityValue"] isKindOfClass:NSString.class]
                ? cpp_string(source[@"accessibilityValue"])
                : (is_text_input ? cpp_string(input_value) : std::string{}),
            .bounds = frame_box,
        };
        const auto boolean = [&](NSString* name, bool& destination, bool* present = nullptr) {
            id raw = semantic_state[name];
            if (raw == nil) return true;
            if (![raw isKindOfClass:NSNumber.class]) return false;
            destination = [raw boolValue];
            if (present != nullptr) *present = true;
            return true;
        };
        if (!boolean(@"disabled", node.state.disabled) ||
            !boolean(@"selected", node.state.selected) ||
            !boolean(@"expanded", node.state.expanded, &node.state.has_expanded)) {
            set_error(error, "accessibility state values must be booleans");
            return false;
        }
        if (semantic_state[@"checked"] != nil) {
            node.state.has_checked = true;
            if ([semantic_state[@"checked"] isKindOfClass:NSString.class] &&
                [semantic_state[@"checked"] isEqualToString:@"mixed"]) {
                node.state.mixed = true;
            } else if ([semantic_state[@"checked"] isKindOfClass:NSNumber.class]) {
                node.state.checked = [semantic_state[@"checked"] boolValue];
            } else {
                set_error(error, "accessibility checked state must be boolean or mixed");
                return false;
            }
        }
        if ([events[@"press"] isKindOfClass:NSString.class]) {
            node.actions.push_back("press");
        }
        if (is_text_input) {
            node.actions.push_back("focus");
            node.actions.push_back("set-value");
        }
        context.accessibility_nodes->push_back(std::move(node));
    }

    NSArray* children = array_or_empty(source[@"children"]);
    const bool is_stack = [type isEqualToString:@"stack"];
    const bool horizontal = [style[@"direction"] isEqual:@"horizontal"];
    const float padding = std::max(0.0F, number_or(style, @"padding", 0.0F));
    const float gap = std::max(0.0F, number_or(style, @"gap", 0.0F));
    if ([type isEqualToString:@"button"]) {
        const std::string label_text = direct_text(source);
        if (!label_text.empty()) {
            vellum::graphics::SceneNode label;
            const float font_size = 14.0F;
            label.id = identity + "/label";
            if (!context.identities.insert(label.id).second) {
                set_error(error, "generated button label id collides: " + label.id);
                return false;
            }
            if (++context.nodes > kMaximumNodes) {
                set_error(error, "authoring tree exceeds the node limit");
                return false;
            }
            label.kind = vellum::graphics::SceneNode::Kind::text;
            label.bounds = {16.0F, (proposed.height - font_size * 1.4F) * 0.5F,
                            std::max(0.0F, proposed.width - 32.0F), font_size * 1.4F};
            label.fill = parse_color(style[@"color"])
                .value_or(vellum::graphics::Color::hex(0x041412));
            label.font_size = font_size;
            label.text = label_text;
            output.children.push_back(std::move(label));
        }
    }
    if (is_text_input) {
        const bool showing_placeholder = input_value.length == 0U;
        NSString* display = showing_placeholder ? input_placeholder : input_value;
        if (display.length > 0U) {
            vellum::graphics::SceneNode label;
            const float font_size = std::max(1.0F, number_or(style, @"fontSize", 14.0F));
            label.id = identity + "/value";
            if (!context.identities.insert(label.id).second) {
                set_error(error, "generated text-input value id collides: " + label.id);
                return false;
            }
            if (++context.nodes > kMaximumNodes) {
                set_error(error, "authoring tree exceeds the node limit");
                return false;
            }
            label.kind = vellum::graphics::SceneNode::Kind::text;
            label.bounds = {12.0F, (proposed.height - font_size * 1.4F) * 0.5F,
                            std::max(0.0F, proposed.width - 24.0F), font_size * 1.4F};
            label.fill = parse_color(style[@"color"]).value_or(
                vellum::graphics::Color::hex(showing_placeholder ? 0x94A3B8 : 0x111827));
            label.font_size = font_size;
            label.text = cpp_string(display);
            output.children.push_back(std::move(label));
        }
    }
    float cursor = padding;
    for (id child_value in children) {
        if (![child_value isKindOfClass:NSDictionary.class]) {
            set_error(error, "authoring node children must be retained-tree objects");
            return false;
        }
        NSDictionary* child = static_cast<NSDictionary*>(child_value);
        if (skipped_text_run(type, child)) {
            if (!validate_attributed_run(child, identity, depth, context, error)) return false;
            report_dropped_style(dictionary_or_empty(child[@"style"]), cpp_string(child[@"id"]),
                "text-run",
                [&](std::string_view key) { return text_run_style_key_consumed(type_name, key); },
                context);
            continue;
        }
        NSDictionary* child_style = dictionary_or_empty(child[@"style"]);
        NSString* child_type = [child[@"type"] isKindOfClass:NSString.class]
            ? child[@"type"] : @"view";
        float width = number_or(
            child_style, @"width",
            horizontal ? 0.0F : std::max(0.0F, proposed.width - padding * 2.0F));
        float height = number_or(
            child_style, @"height", default_height(child_type, child_style, child));
        float x = number_or(child_style, @"x", is_stack && horizontal ? cursor : padding);
        float y = number_or(child_style, @"y", is_stack && !horizontal ? cursor : padding);
        if (width <= 0.0F && horizontal) width = std::max(0.0F, proposed.width - cursor - padding);
        if (height <= 0.0F && !horizontal) height = std::max(0.0F, proposed.height - cursor - padding);
        vellum::graphics::SceneNode child_output;
        if (!materialize_node(
                child, {x, y, width, height},
                absolute_x + proposed.x, absolute_y + proposed.y, node_transform,
                depth + 1U, context, child_output, error)) {
            return false;
        }
        output.children.push_back(std::move(child_output));
        if (is_stack) cursor += (horizontal ? width : height) + gap;
    }
    return true;
}

}  // namespace

bool materialize_rendered_tree(
    std::string_view rendered_json,
    RenderedApplication& output,
    std::string* error,
    std::vector<LoweringDiagnostic>* failure_diagnostics) {
    if (error != nullptr) error->clear();
    if (failure_diagnostics != nullptr) failure_diagnostics->clear();
    if (rendered_json.size() > kMaximumJsonBytes) {
        set_error(error, "JavaScript authoring bridge JSON is invalid or too large");
        return false;
    }
    NSData* data = [NSData dataWithBytes:rendered_json.data()
                                  length:rendered_json.size()];
    NSError* json_error = nil;
    id value = [NSJSONSerialization JSONObjectWithData:data options:0 error:&json_error];
    if (![value isKindOfClass:NSDictionary.class]) {
        set_error(error, "JavaScript authoring bridge returned an invalid envelope: " +
                         cpp_string(json_error.localizedDescription));
        return false;
    }
    NSDictionary* envelope = static_cast<NSDictionary*>(value);
    const bool legacy = [envelope[@"protocol"] isEqual:@"vellum.authoring-host.v1"];
    const bool asynchronous =
        [envelope[@"protocol"] isEqual:@"vellum.authoring-host.v2"] &&
        [envelope[@"kind"] isEqual:@"render-result"];
    if ((!legacy && !asynchronous) ||
        ![envelope[@"tree"] isKindOfClass:NSDictionary.class]) {
        set_error(error, "JavaScript authoring bridge protocol mismatch");
        return false;
    }
    NSDictionary* tree = envelope[@"tree"];
    NSDictionary* style = dictionary_or_empty(tree[@"style"]);
    const float width = number_or(style, @"width", 0.0F);
    const float height = number_or(style, @"height", 0.0F);
    if (width <= 0.0F || height <= 0.0F) {
        set_error(error, "root authoring node requires positive numeric width and height");
        return false;
    }
    RenderedApplication candidate;
    candidate.scene.width = width;
    candidate.scene.height = height;
    candidate.scene.background = parse_color(style[@"backgroundColor"])
        .value_or(vellum::graphics::Color::hex(0xF8FAFC));
    MaterializeContext context{
        .interactions = &candidate.interactions,
        .text_inputs = &candidate.text_inputs,
        .accessibility_nodes = &candidate.accessibility_nodes,
        .diagnostics = &candidate.diagnostics,
    };
    if (!materialize_node(
            tree, {0.0F, 0.0F, width, height}, 0.0F, 0.0F, vellum::graphics::Affine2D{}, 0U,
            context, candidate.scene.root, error)) {
        if (failure_diagnostics != nullptr) {
            *failure_diagnostics = std::move(candidate.diagnostics);
        }
        return false;
    }
    output = std::move(candidate);
    return true;
}

}  // namespace vellum::authoring
