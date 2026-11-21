#include "rendered_tree_materializer.hpp"

#import <Foundation/Foundation.h>
#include <xlocale.h>

#include <vellum/graphics/animation.hpp>
#include <vellum/graphics/paint_command.hpp>

#include <vellum/graphics/image_asset.hpp>
#include <vellum/graphics/path_geometry.hpp>
#include <vellum/graphics/token_receipts.hpp>
#include <vellum/graphics/plot.hpp>
#include <vellum/graphics/vector_asset.hpp>

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

bool protocol_integer(double number, std::uint64_t limit, std::uint64_t& output) {
    if (!std::isfinite(number) || number < 0.0 || number != std::floor(number) ||
        number >= static_cast<double>(limit)) {
        return false;
    }
    output = static_cast<std::uint64_t>(number);
    return true;
}

namespace {

constexpr std::size_t kMaximumJsonBytes = 16U * 1024U * 1024U;
constexpr std::size_t kMaximumNodes = vellum::graphics::kMaxDesignNodes;
constexpr std::size_t kMaximumDepth = vellum::graphics::kMaxDesignDepth;
constexpr NSUInteger kMaximumGradientStops = vellum::graphics::kMaxGradientStops;
constexpr NSUInteger kMaximumTokens = vellum::graphics::kMaxDesignTokens;
constexpr NSUInteger kMaximumTextInputBytes = vellum::graphics::kMaxTextInputBytes;
constexpr NSUInteger kMaximumPlaceholderBytes = 4U * 1024U;
// Retained-tree consumers share these defaults across native and web. Controls
// retain the established 44-point density; zero keeps an unspecified generic
// child eligible for the parent stack's remaining-axis fill behavior.
constexpr float kDefaultButtonHeight = 44.0F;
constexpr float kDefaultTextInputHeight = 44.0F;
constexpr float kDefaultGenericHeight = 0.0F;
constexpr float kTextLineHeightMultiplier = 1.4F;
constexpr std::array<std::string_view, 12> kIntrinsicTypes{
    "view", "stack", "text", "text-run", "text-input", "button", "image", "canvas", "custom",
    "path", "vector", "plot",
};
constexpr std::array<std::string_view, 6> kTextStyleKeys{
    "color", "fontFamily", "fontSize", "fontWeight", "letterSpacing", "textDecoration",
};
constexpr std::array<std::string_view, 4> kCornerRadiusKeys{
    "borderTopLeftRadius", "borderTopRightRadius",
    "borderBottomRightRadius", "borderBottomLeftRadius",
};
const std::array<NSString*, 4> kBorderWidths{
    @"borderTopWidth", @"borderRightWidth", @"borderBottomWidth", @"borderLeftWidth",
};
const std::array<NSString*, 4> kBorderColors{
    @"borderTopColor", @"borderRightColor", @"borderBottomColor", @"borderLeftColor",
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
    if (key == "mixBlendMode") return true;
    // The root has no siblings to mask.
    if (key == "maskMode") return !use.root;
    if (use.clips_to_box && type != "path" && type != "vector" &&
        (key == "borderRadius" || contains(kCornerRadiusKeys, key))) {
        return true;
    }
    if (type == "path" || type == "vector") return false;
    if (type == "plot") return key == "backgroundColor";
    if (use.draws_image) {
        return key == "backgroundColor" || key == "borderRadius" ||
               contains(kCornerRadiusKeys, key);
    }
    if (key == "padding" || key == "direction") return use.lays_out_children;
    if (key == "gap") return type == "stack" && use.lays_out_children;
    if (key == "backgroundGradient" || key == "backgroundLinearGradient" ||
        key == "backgroundGradients" || key == "boxShadow") return true;
    if (!text) {
        for (std::size_t side = 0; side < kBorderWidths.size(); ++side) {
            if (key == kBorderWidths[side].UTF8String || key == kBorderColors[side].UTF8String) {
                return true;
            }
        }
    }
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

// A non-negative integer protocol field below `limit`, read from the JSON
// number's double before any integer conversion, so a value that is fractional,
// negative, not finite or out of range is refused instead of converted.
bool protocol_index(id value, std::uint64_t limit, std::uint64_t& output) {
    if (![value isKindOfClass:NSNumber.class] ||
        CFGetTypeID((__bridge CFTypeRef)value) == CFBooleanGetTypeID()) {
        return false;
    }
    return protocol_integer([static_cast<NSNumber*>(value) doubleValue], limit, output);
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

// `#rgb`, `#rgba`, `#rrggbb` or `#rrggbbaa`; a short form doubles each digit,
// as CSS does, so `#fff` and `#ffffff` are the same colour.
std::optional<vellum::graphics::Color> parse_color(id value) {
    if (![value isKindOfClass:NSString.class]) return std::nullopt;
    NSString* text = static_cast<NSString*>(value);
    if (![text hasPrefix:@"#"]) return std::nullopt;
    if (text.length == 4 || text.length == 5) {
        NSMutableString* expanded = [NSMutableString stringWithString:@"#"];
        for (NSUInteger index = 1; index < text.length; ++index) {
            const unichar digit = [text characterAtIndex:index];
            [expanded appendFormat:@"%C%C", digit, digit];
        }
        text = expanded;
    }
    if (text.length != 7 && text.length != 9) return std::nullopt;
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
        fallback.font_weight = static_cast<int>(std::lround(std::clamp(weight, 100.0F, 900.0F)));
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
    std::string* error,
    bool coincident_stops = false) {
    if (raw == nil) return true;
    if (![raw isKindOfClass:NSDictionary.class]) {
        set_error(error, name + " must be an object");
        return false;
    }
    NSDictionary* source = raw;
    vellum::graphics::LinearGradient gradient;
    if (id kind_value = source[@"kind"]; kind_value != nil) {
        if (![kind_value isKindOfClass:NSString.class]) {
            set_error(error, name + ".kind must be linear, radial, two-circle-radial or conic");
            return false;
        }
        NSString* kind = kind_value;
        if ([kind isEqualToString:@"radial"]) {
            gradient.kind = vellum::graphics::GradientKind::radial;
        } else if ([kind isEqualToString:@"two-circle-radial"]) {
            gradient.kind = vellum::graphics::GradientKind::two_circle_radial;
        } else if ([kind isEqualToString:@"conic"]) {
            gradient.kind = vellum::graphics::GradientKind::conic;
        } else if (![kind isEqualToString:@"linear"]) {
            set_error(error, name + ".kind must be linear, radial, two-circle-radial or conic");
            return false;
        }
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
    if (id spread_value = source[@"spread"]; spread_value == nil) {
        gradient.spread = gradient.repeating ? vellum::graphics::GradientSpread::repeat
                                             : vellum::graphics::GradientSpread::pad;
    } else {
        if (![spread_value isKindOfClass:NSString.class]) {
            set_error(error, name + ".spread must be pad, repeat or reflect");
            return false;
        }
        NSString* spread = spread_value;
        if ([spread isEqualToString:@"repeat"]) {
            gradient.spread = vellum::graphics::GradientSpread::repeat;
        } else if ([spread isEqualToString:@"reflect"]) {
            gradient.spread = vellum::graphics::GradientSpread::reflect;
        } else if ([spread isEqualToString:@"pad"]) {
            gradient.spread = vellum::graphics::GradientSpread::pad;
        } else {
            set_error(error, name + ".spread must be pad, repeat or reflect");
            return false;
        }
        if (source[@"repeating"] != nil &&
            gradient.repeating != (gradient.spread != vellum::graphics::GradientSpread::pad)) {
            set_error(error, name + ".repeating contradicts " + name +
                                 ".spread; repeating is true exactly when spread is repeat or "
                                 "reflect");
            return false;
        }
    }
    if (!number_field(source, @"centerX", 0.5F, gradient.center_x, name + ".centerX", error) ||
        !number_field(source, @"centerY", 0.5F, gradient.center_y, name + ".centerY", error) ||
        !number_field(source, @"radiusX", 0.0F, gradient.radius_x, name + ".radiusX", error) ||
        !number_field(source, @"radiusY", 0.0F, gradient.radius_y, name + ".radiusY", error) ||
        !number_field(source, @"focalX", 0.5F, gradient.focal_x, name + ".focalX", error) ||
        !number_field(source, @"focalY", 0.5F, gradient.focal_y, name + ".focalY", error) ||
        !number_field(source, @"startRadius", 0.0F, gradient.start_radius,
                      name + ".startRadius", error) ||
        !number_field(source, @"endRadius", 0.5F, gradient.end_radius, name + ".endRadius",
                      error)) {
        return false;
    }
    {
        using vellum::graphics::GradientKind;
        const auto kind = gradient.kind;
        const bool angled = kind == GradientKind::linear || kind == GradientKind::conic;
        const bool centred = kind != GradientKind::linear;
        const bool focal = kind == GradientKind::two_circle_radial;
        const std::pair<NSString*, bool> kind_fields[]{
            {@"angle", angled},   {@"centerX", centred}, {@"centerY", centred},
            {@"focalX", focal},   {@"focalY", focal},
        };
        for (const auto& [key, applies] : kind_fields) {
            if (!applies && source[key] != nil) {
                set_error(error, name + "." + cpp_string(key) + " does not apply to a " +
                                     (kind == GradientKind::linear       ? "linear"
                                      : kind == GradientKind::radial     ? "radial"
                                      : kind == GradientKind::conic      ? "conic"
                                                                         : "two-circle-radial") +
                                     " gradient");
                return false;
            }
        }
    }
    const std::pair<float, const char*> non_negative[]{
        {gradient.repeat_length, ".repeatLength"}, {gradient.radius_x, ".radiusX"},
        {gradient.radius_y, ".radiusY"},           {gradient.start_radius, ".startRadius"},
        {gradient.end_radius, ".endRadius"},
    };
    for (const auto& [value, field] : non_negative) {
        if (value < 0.0F) {
            set_error(error, name + field + " must be non-negative");
            return false;
        }
    }
    NSArray* stops = array_or_empty(source[@"stops"]);
    if (stops.count < 2U) {
        set_error(error, name + " requires at least two stops");
        return false;
    }
    if (stops.count > kMaximumGradientStops) {
        set_error(error, name + " exceeds the " + std::to_string(kMaximumGradientStops) + "-stop limit");
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
            position > 1.0F || (coincident_stops ? position < previous : position <= previous) ||
            !color) {
            set_error(error, name + (coincident_stops
                                         ? " stops require non-decreasing positions and colors"
                                         : " stops require increasing positions and colors"));
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
    if (id layers = style[@"backgroundGradients"]; layers != nil) {
        if (![layers isKindOfClass:NSArray.class] || [layers count] > 8U ||
            style[@"backgroundGradient"] != nil || style[@"backgroundLinearGradient"] != nil) {
            set_error(error, "backgroundGradients requires up to eight layers as the sole gradient style");
            return false;
        }
        for (id layer in layers) {
            if (!parse_linear_gradient_value(layer, "backgroundGradients", output, error, true)) {
                return false;
            }
        }
        return true;
    }
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
    const std::vector<vellum::graphics::TokenReceipt>* tokens = nullptr;
    std::vector<NodeRecord>* node_records = nullptr;
    std::optional<std::uint32_t> root_background_token;
};

constexpr std::size_t kMaximumSourceFileBytes = vellum::graphics::kMaxSourceFileBytes;
constexpr std::size_t kMaximumAnchorBytes = vellum::graphics::kMaxSourceAnchorBytes;

// Reads the optional `source` ({file, line, column}) and `anchor` fields of an
// authored node. Malformed values are ignored: source is diagnostic metadata
// and never changes what is drawn.
NodeSource read_node_source(NSDictionary* source) {
    NodeSource result;
    if ([source[@"anchor"] isKindOfClass:NSString.class]) {
        std::string anchor = cpp_string(source[@"anchor"]);
        if (anchor.size() <= kMaximumAnchorBytes) result.anchor = std::move(anchor);
    }
    if (![source[@"sourceLocation"] isKindOfClass:NSDictionary.class]) return result;
    NSDictionary* location = source[@"sourceLocation"];
    if (![location[@"file"] isKindOfClass:NSString.class]) return result;
    std::string file = cpp_string(location[@"file"]);
    if (file.empty() || file.size() > kMaximumSourceFileBytes) return result;
    result.file = std::move(file);
    const auto position = [&](NSString* key) {
        id raw = location[key];
        if (![raw isKindOfClass:NSNumber.class] ||
            CFGetTypeID((__bridge CFTypeRef)raw) == CFBooleanGetTypeID()) return 0;
        const double value = [raw doubleValue];
        return value >= 0.0 && value <= 1.0e9 ? static_cast<int>(value) : 0;
    };
    result.line = position(@"line");
    result.column = position(@"column");
    return result;
}

bool refuse_token(
    const MaterializeContext& context, const std::string& node_id, std::string property,
    std::string message, std::string* error) {
    context.diagnostics->push_back({
        .severity = LoweringDiagnostic::Severity::error,
        .disposition = LoweringDiagnostic::Disposition::unsupported,
        .code = std::string(vellum::graphics::kInvalidTokenReferenceCode),
        .node_id = node_id,
        .property = std::move(property),
        .message = message,
    });
    set_error(error, std::move(message));
    return false;
}

// The `tokenRefs` object of `source`, or nil when it has none; `ok` is false
// when it is present but not an object.
NSDictionary* token_references(
    NSDictionary* source, const MaterializeContext& context, const std::string& node_id,
    bool& ok, std::string* error) {
    ok = true;
    id references = source[@"tokenRefs"];
    if (references == nil) return nil;
    if (![references isKindOfClass:NSDictionary.class]) {
        ok = refuse_token(context, node_id, "tokenRefs",
            "tokenRefs of " + node_id + " must be an object", error);
        return nil;
    }
    return references;
}

// The table entry `references[key]` names, which must hold `color`.
bool token_entry(
    NSDictionary* references, NSString* key, const MaterializeContext& context,
    const std::string& node_id, const vellum::graphics::Color& color, std::uint32_t& entry,
    std::string* error) {
    const std::string property = "tokenRefs." + cpp_string(key);
    const std::size_t count = context.tokens != nullptr ? context.tokens->size() : 0U;
    std::uint64_t index = 0;
    if (!protocol_index(references[key], count, index)) {
        return refuse_token(context, node_id, property,
            property + " of " + node_id + " must name an entry of the token table", error);
    }
    entry = static_cast<std::uint32_t>(index);
    if (!vellum::graphics::same_token_color(color, (*context.tokens)[entry].value)) {
        return refuse_token(context, node_id, property,
            property + " of " + node_id + " disagrees with its resolved colour", error);
    }
    return true;
}

bool bind_token(
    NSDictionary* source, NSString* slot, const MaterializeContext& context,
    vellum::graphics::SceneNode& node, std::string* error) {
    bool ok = true;
    NSDictionary* references = token_references(source, context, node.id, ok, error);
    if (references == nil || references[slot] == nil) return ok;
    std::uint32_t entry = 0;
    if (!token_entry(references, slot, context, node.id, node.fill, entry, error)) return false;
    node.fill_token = entry;
    return true;
}

// The stop index of a `<prefix>.stops.<n>` slot name, or empty.
std::optional<std::uint32_t> stop_slot(std::string_view name, std::string_view prefix) {
    if (!name.starts_with(prefix) || !name.substr(prefix.size()).starts_with(".stops.")) {
        return std::nullopt;
    }
    const auto digits = name.substr(prefix.size() + 7U);
    if (digits.empty() || digits.size() > 6U || (digits.size() > 1U && digits[0] == '0') ||
        !std::all_of(digits.begin(), digits.end(),
                     [](char digit) { return digit >= '0' && digit <= '9'; })) {
        return std::nullopt;
    }
    return static_cast<std::uint32_t>(std::stoul(std::string(digits)));
}

// Binds every key of a node's `tokenRefs` to the colour it names: `fill_token`
// for `backgroundColor`, a text node's `color` and a path's `fill`, and a
// `TokenSlot` for `boxShadow`, `backgroundLinearGradient.stops.<n>`,
// `fill.stops.<n>` and a path's `stroke`. A key that names no slot, or a
// colour the node lacks, is refused; a colour the node's type does not draw
// is reported as dropped. A button or text input binds `color` on its label.
bool bind_token_refs(
    NSDictionary* source, NSDictionary* style, std::string_view type_name, bool labelled,
    bool root, MaterializeContext& context, vellum::graphics::SceneNode& output,
    std::string* error) {
    using vellum::graphics::SceneNode;
    using vellum::graphics::TokenSlot;
    bool ok = true;
    NSDictionary* references = token_references(source, context, output.id, ok, error);
    if (references == nil) return ok;
    const bool path = type_name == "path";
    const auto dropped = [&](const std::string& name) {
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::lowered,
            .code = std::string(vellum::graphics::kStyleDroppedCode),
            .node_id = output.id,
            .property = "tokenRefs." + name,
            .message = "a " + std::string(type_name) + " node does not draw the colour " + name +
                       " names, so its token reference was dropped",
        });
        return true;
    };
    const auto missing = [&](const std::string& name) {
        return refuse_token(context, output.id, "tokenRefs." + name,
            "tokenRefs." + name + " of " + output.id + " names a colour the node does not have",
            error);
    };
    const auto check_index = [&](NSString* key) {
        const std::size_t count = context.tokens != nullptr ? context.tokens->size() : 0U;
        std::uint64_t index = 0;
        if (protocol_index(references[key], count, index)) return true;
        const std::string property = "tokenRefs." + cpp_string(key);
        return refuse_token(context, output.id, property,
            property + " of " + output.id + " must name an entry of the token table", error);
    };
    const auto check_color = [&](NSString* key, const vellum::graphics::Color& color) {
        std::uint32_t entry = 0;
        return token_entry(references, key, context, output.id, color, entry, error);
    };
    const auto bind_slot = [&](NSString* key, TokenSlot slot) {
        const auto* color = vellum::graphics::token_slot_color(output, slot);
        if (color == nullptr) return missing(cpp_string(key));
        if (!token_entry(references, key, context, output.id, *color, slot.token, error)) {
            return false;
        }
        output.color_tokens.push_back(slot);
        return true;
    };
    const auto bind_fill = [&](NSString* key) {
        std::uint32_t entry = 0;
        if (!token_entry(references, key, context, output.id, output.fill, entry, error)) {
            return false;
        }
        output.fill_token = entry;
        return true;
    };
    NSArray* keys = [references.allKeys sortedArrayUsingSelector:@selector(compare:)];
    for (id raw_key in keys) {
        if (![raw_key isKindOfClass:NSString.class]) {
            return refuse_token(context, output.id, "tokenRefs",
                "tokenRefs of " + output.id + " must map slot names to entries", error);
        }
        NSString* key = raw_key;
        const std::string name = cpp_string(key);
        bool bound = true;
        if (name == "backgroundColor" && (root || !path)) {
            // The root's backgroundColor is also the scene's clear colour,
            // whatever the root's kind, so its receipt is kept for that too.
            const auto background = parse_color(style[@"backgroundColor"]);
            const bool fills = !path && (output.kind == SceneNode::Kind::rectangle ||
                                         output.kind == SceneNode::Kind::image ||
                                         output.kind == SceneNode::Kind::plot);
            if (!background) {
                bound = missing(name);
            } else {
                if (root) {
                    std::uint32_t entry = 0;
                    bound = token_entry(references, key, context, output.id, *background, entry,
                                        error);
                    if (bound) context.root_background_token = entry;
                }
                if (bound && fills) {
                    bound = bind_fill(key);
                } else if (bound && !root) {
                    bound = check_color(key, *background) && dropped(name);
                }
            }
        } else if (name == "color" && !path) {
            if (output.kind == SceneNode::Kind::text) {
                bound = bind_fill(key);
            } else {
                const auto color = parse_color(style[@"color"]);
                if (!labelled) {
                    bound = color ? check_color(key, *color) && dropped(name) : missing(name);
                } else {
                    bound = color ? check_color(key, *color) : check_index(key);
                }
            }
        } else if (name == "boxShadow" && !path) {
            if (style[@"boxShadow"] == nil) {
                bound = missing(name);
            } else if (output.box_shadows.empty()) {
                NSDictionary* shadow = dictionary_or_empty(style[@"boxShadow"]);
                const auto color = parse_color(shadow[@"color"]);
                bound = color ? check_color(key, *color) && dropped(name) : missing(name);
            } else {
                bound = bind_slot(key, {TokenSlot::Kind::box_shadow, 0U, 0U, 0U});
            }
        } else if (const auto stop = stop_slot(name, "backgroundLinearGradient"); stop && !path) {
            if (style[@"backgroundLinearGradient"] == nil) {
                bound = missing(name);
            } else if (output.fill_gradients.empty()) {
                NSArray* stops = array_or_empty(
                    dictionary_or_empty(style[@"backgroundLinearGradient"])[@"stops"]);
                const auto color = *stop < stops.count
                    ? parse_color(dictionary_or_empty(stops[*stop])[@"color"])
                    : std::nullopt;
                bound = color ? check_color(key, *color) && dropped(name) : missing(name);
            } else {
                bound = bind_slot(key, {TokenSlot::Kind::gradient_stop, 0U, *stop, 0U});
            }
        } else if (name == "fill" && path) {
            bound = parse_color(source[@"fill"]) ? bind_fill(key) : missing(name);
        } else if (name == "stroke" && path) {
            bound = parse_color(source[@"stroke"])
                ? bind_slot(key, {TokenSlot::Kind::path_stroke, 0U, 0U, 0U})
                : missing(name);
        } else if (const auto fill_stop = stop_slot(name, "fill"); fill_stop && path) {
            bound = bind_slot(key, {TokenSlot::Kind::gradient_stop, 0U, *fill_stop, 0U});
        } else {
            bound = refuse_token(context, output.id, "tokenRefs." + name,
                "tokenRefs." + name + " of " + output.id + " is not a token slot of a " +
                    std::string(type_name) + " node",
                error);
        }
        if (!bound) return false;
    }
    return true;
}

// Validates the `tokenRefs` of an attributed run that is flattened into its
// parent's generated label, so its colour is never bound: the reference must
// still name a table entry that holds the colour the run resolves to.
bool check_dropped_run_token_refs(
    NSDictionary* run, const std::optional<vellum::graphics::Color>& inherited,
    MaterializeContext& context, const std::string& fallback_id, std::string* error) {
    const std::string run_id =
        [run[@"id"] isKindOfClass:NSString.class] ? cpp_string(run[@"id"]) : fallback_id;
    bool ok = true;
    NSDictionary* references = token_references(run, context, run_id, ok, error);
    if (references == nil) return ok;
    for (id key in references) {
        if (![key isEqual:@"color"]) {
            return refuse_token(context, run_id, "tokenRefs",
                "tokenRefs of text-run " + run_id + " may only name color", error);
        }
    }
    if (references[@"color"] == nil) return true;
    const auto color = parse_color(dictionary_or_empty(run[@"style"])[@"color"]);
    const auto resolved = color ? color : inherited;
    if (!resolved) {
        return refuse_token(context, run_id, "tokenRefs.color",
            "tokenRefs.color of " + run_id + " names a colour the run does not have", error);
    }
    std::uint32_t entry = 0;
    if (!token_entry(references, @"color", context, run_id, *resolved, entry, error)) {
        return false;
    }
    context.diagnostics->push_back({
        .severity = LoweringDiagnostic::Severity::warning,
        .disposition = LoweringDiagnostic::Disposition::lowered,
        .code = std::string(vellum::graphics::kStyleDroppedCode),
        .node_id = run_id,
        .property = "tokenRefs.color",
        .message = "a button label does not keep the colour of its text-run " + run_id +
                   ", so its token reference was dropped",
    });
    return true;
}

// Binds an attributed run's `tokenRefs`; only `color` is a slot of a run.
bool bind_run_token_refs(
    NSDictionary* run, std::uint32_t run_index, MaterializeContext& context,
    vellum::graphics::SceneNode& output, std::string* error) {
    using vellum::graphics::TokenSlot;
    const std::string run_id =
        [run[@"id"] isKindOfClass:NSString.class] ? cpp_string(run[@"id"]) : output.id;
    bool ok = true;
    NSDictionary* references = token_references(run, context, run_id, ok, error);
    if (references == nil) return ok;
    for (id key in references) {
        if (![key isEqual:@"color"]) {
            return refuse_token(context, run_id, "tokenRefs",
                "tokenRefs of text-run " + run_id + " may only name color", error);
        }
    }
    if (references[@"color"] == nil) return true;
    TokenSlot slot{TokenSlot::Kind::text_run, run_index, 0U, 0U};
    const auto* color = vellum::graphics::token_slot_color(output, slot);
    if (color == nullptr ||
        !token_entry(references, @"color", context, run_id, *color, slot.token, error)) {
        return color == nullptr
            ? refuse_token(context, run_id, "tokenRefs.color",
                  "text-run " + run_id + " has no run to bind", error)
            : false;
    }
    output.color_tokens.push_back(slot);
    return true;
}

bool parse_token_table(
    NSDictionary* tree, std::vector<vellum::graphics::TokenReceipt>& output, std::string* error) {
    id raw = tree[@"tokens"];
    if (raw == nil) return true;
    if (![raw isKindOfClass:NSArray.class] || static_cast<NSArray*>(raw).count > kMaximumTokens) {
        set_error(error, "the token table must be an array of bounded size");
        return false;
    }
    std::set<std::string> paths;
    for (id item in static_cast<NSArray*>(raw)) {
        if (![item isKindOfClass:NSDictionary.class] ||
            ![item[@"path"] isKindOfClass:NSString.class] ||
            ![item[@"layer"] isKindOfClass:NSString.class] || [item[@"path"] length] == 0U ||
            [item[@"layer"] length] == 0U) {
            set_error(error, "each token receipt requires string path, layer and value fields");
            return false;
        }
        const auto value = parse_color(item[@"value"]);
        std::string path = cpp_string(item[@"path"]);
        if (!value || !paths.insert(path).second) {
            set_error(error, "token receipt " + path +
                                 " has no colour value or repeats a path");
            return false;
        }
        output.push_back({std::move(path), cpp_string(item[@"layer"]), *value});
    }
    return true;
}

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

constexpr std::array<std::string_view, 21> kPathAttributes{
    "type", "id", "children", "style", "events", "tracks", "accessibilityLabel",
    "accessibilityValue",
    "accessibilityRole", "accessibilityState", "d", "fillRule", "fill", "stroke", "strokeWidth",
    "strokeLineJoin", "strokeLineCap", "strokeMiterLimit", "strokeDasharray", "strokeDashoffset",
    "anchor",
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

bool reject_vector(
    MaterializeContext& context, const std::string& node_id, std::string property,
    std::string message, std::string* error) {
    context.diagnostics->push_back({
        .severity = LoweringDiagnostic::Severity::error,
        .disposition = LoweringDiagnostic::Disposition::unsupported,
        .code = std::string(vellum::graphics::kInvalidVectorAssetCode),
        .node_id = node_id,
        .property = std::move(property),
        .message = message,
    });
    set_error(error, std::move(message));
    return false;
}

constexpr std::array<std::string_view, 11> kVectorAttributes{
    "type", "id", "children", "style", "events", "tracks", "accessibilityLabel",
    "accessibilityValue", "accessibilityRole", "asset", "anchor",
};

bool parse_affine(id raw, vellum::graphics::Affine2D& output) {
    if (raw == nil) return true;
    if (![raw isKindOfClass:NSArray.class] || static_cast<NSArray*>(raw).count != 6U) return false;
    NSArray* values = raw;
    float numbers[6];
    for (NSUInteger index = 0; index < 6U; ++index) {
        if (!finite_number(values[index], numbers[index])) return false;
    }
    output = {numbers[0], numbers[1], numbers[2], numbers[3], numbers[4], numbers[5]};
    return true;
}

// The first key of `object` outside `allowed`, or empty when every key is
// known. A non-string key is reported as itself.
template <std::size_t Size>
std::string unknown_vector_key(NSDictionary* object, const std::array<std::string_view, Size>& allowed) {
    for (id key in object) {
        if (![key isKindOfClass:NSString.class]) return "a non-string key";
        const std::string name = cpp_string(key);
        if (!contains(allowed, name)) return name;
    }
    return {};
}

constexpr std::array<std::string_view, 3> kVectorAssetKeys{"contentHash", "viewBox", "ops"};
constexpr std::array<std::string_view, 2> kVectorClipKeys{"d", "rule"};
constexpr std::array<std::string_view, 2> kVectorPaintKeys{"gradient", "transform"};
constexpr std::array<std::string_view, 16> kVectorGradientKeys{
    "kind", "angle", "repeating", "repeatLength", "spread", "interpolation", "colorSpace",
    "centerX", "centerY", "radiusX", "radiusY", "focalX", "focalY", "startRadius",
    "endRadius", "stops"};
constexpr std::array<std::string_view, 2> kVectorStopKeys{"position", "color"};

bool parse_vector_path_data(id raw, id rule, vellum::graphics::PathGeometry& output,
                            std::string& reason) {
    if (![raw isKindOfClass:NSString.class]) {
        reason = "path data must be a string";
        return false;
    }
    if (!vellum::graphics::parse_svg_path_data(cpp_string(raw), output, &reason)) return false;
    if (rule != nil) {
        const auto value = keyword(rule, std::array<std::string_view, 2>{"nonzero", "evenodd"});
        if (!value) {
            reason = "fill rule must be nonzero or evenodd";
            return false;
        }
        output.fill_rule = static_cast<vellum::graphics::FillRule>(*value);
    }
    return true;
}

bool parse_vector_paint(id raw, vellum::graphics::VectorPaint& output, const std::string& name,
                        std::string& reason) {
    if (raw == nil) return true;
    if ([raw isEqual:@"none"]) {
        output = {};
        return true;
    }
    if ([raw isKindOfClass:NSString.class]) {
        const auto color = parse_color(raw);
        if (!color) {
            reason = name + " must be a hex colour, none or a gradient paint";
            return false;
        }
        output = {.color = *color};
        return true;
    }
    if (![raw isKindOfClass:NSDictionary.class]) {
        reason = name + " must be a hex colour, none or a gradient paint";
        return false;
    }
    NSDictionary* source = raw;
    if (const auto key = unknown_vector_key(source, kVectorPaintKeys); !key.empty()) {
        reason = name + " does not take " + key;
        return false;
    }
    if ([source[@"gradient"] isKindOfClass:NSDictionary.class]) {
        NSDictionary* gradient = source[@"gradient"];
        if (const auto key = unknown_vector_key(gradient, kVectorGradientKeys); !key.empty()) {
            reason = name + ".gradient does not take " + key;
            return false;
        }
        for (id stop in array_or_empty(gradient[@"stops"])) {
            if (![stop isKindOfClass:NSDictionary.class]) continue;
            if (const auto key = unknown_vector_key(static_cast<NSDictionary*>(stop), kVectorStopKeys);
                !key.empty()) {
                reason = name + ".gradient stops do not take " + key;
                return false;
            }
        }
    }
    std::vector<vellum::graphics::LinearGradient> gradients;
    std::string why;
    if (source[@"gradient"] == nil ||
        !parse_linear_gradient_value(source[@"gradient"], name + ".gradient", gradients, &why,
                                     true)) {
        reason = why.empty() ? name + " gradient paint requires a gradient" : why;
        return false;
    }
    output = {.gradient = gradients.front()};
    if (!parse_affine(source[@"transform"], output.gradient_transform)) {
        reason = name + ".transform must be six finite numbers";
        return false;
    }
    return true;
}

// A compiled vector asset: one display list drawn by a single node.
bool materialize_vector(
    NSDictionary* source, const std::string& identity, MaterializeContext& context,
    vellum::graphics::SceneNode& output, std::string* error) {
    using vellum::graphics::VectorOp;
    const auto reject = [&](std::string property, const std::string& problem) {
        return reject_vector(context, identity, std::move(property),
            "vector " + identity + " " + problem, error);
    };
    id children = source[@"children"];
    if (children != nil && (![children isKindOfClass:NSArray.class] ||
                            static_cast<NSArray*>(children).count != 0U)) {
        return reject("children", "does not accept children");
    }
    if (![source[@"asset"] isKindOfClass:NSDictionary.class]) {
        return reject("asset", "requires a compiled asset object");
    }
    NSDictionary* asset_source = source[@"asset"];
    if (const auto key = unknown_vector_key(asset_source, kVectorAssetKeys); !key.empty()) {
        return reject("asset", "asset does not take " + key);
    }
    auto asset = std::make_shared<vellum::graphics::VectorAsset>();
    if (![asset_source[@"contentHash"] isKindOfClass:NSString.class]) {
        return reject("asset", "requires a contentHash string");
    }
    asset->content_hash = cpp_string(asset_source[@"contentHash"]);
    id view_box = asset_source[@"viewBox"];
    if (![view_box isKindOfClass:NSArray.class] || static_cast<NSArray*>(view_box).count != 4U ||
        !finite_number(view_box[0], asset->view_box.x) ||
        !finite_number(view_box[1], asset->view_box.y) ||
        !finite_number(view_box[2], asset->view_box.width) ||
        !finite_number(view_box[3], asset->view_box.height)) {
        return reject("asset", "viewBox must be four finite numbers");
    }
    id ops = asset_source[@"ops"];
    if (![ops isKindOfClass:NSArray.class] ||
        static_cast<NSArray*>(ops).count > vellum::graphics::kMaxVectorOps) {
        return reject("asset", "ops must be an array of at most " +
                                   std::to_string(vellum::graphics::kMaxVectorOps) +
                                   " operations");
    }
    NSUInteger index = 0;
    for (id raw in static_cast<NSArray*>(ops)) {
        const std::string where = "operation " + std::to_string(index++);
        if (![raw isKindOfClass:NSDictionary.class] ||
            ![static_cast<NSDictionary*>(raw)[@"op"] isKindOfClass:NSString.class]) {
            return reject("asset", where + " must be an object with an op");
        }
        NSDictionary* op_source = raw;
        const std::string kind = cpp_string(op_source[@"op"]);
        static constexpr std::array<std::string_view, 4> kGroupKeys{
            "op", "transform", "opacity", "clip"};
        static constexpr std::array<std::string_view, 1> kEndKeys{"op"};
        static constexpr std::array<std::string_view, 12> kPathKeys{
            "op", "d", "fillRule", "transform", "fill", "stroke", "strokeWidth",
            "strokeLineJoin", "strokeLineCap", "strokeMiterLimit", "strokeDasharray",
            "strokeDashoffset"};
        for (id key in op_source) {
            if (![key isKindOfClass:NSString.class]) {
                return reject("asset", where + " has a key that is not a string");
            }
            const std::string name = cpp_string(key);
            const bool known = kind == "group" ? contains(kGroupKeys, name)
                             : kind == "end"   ? contains(kEndKeys, name)
                                               : contains(kPathKeys, name);
            if (!known && (kind == "group" || kind == "end" || kind == "path")) {
                return reject("asset", where + " " + kind + " does not take " + name);
            }
        }
        VectorOp op;
        if (!parse_affine(op_source[@"transform"], op.transform)) {
            return reject("asset", where + " transform must be six finite numbers");
        }
        std::string reason;
        if (kind == "group") {
            op.kind = VectorOp::Kind::push_group;
            if (op_source[@"opacity"] != nil && !finite_number(op_source[@"opacity"], op.opacity)) {
                return reject("asset", where + " opacity must be a finite number");
            }
            if (id clip = op_source[@"clip"]; clip != nil) {
                if (![clip isKindOfClass:NSDictionary.class]) {
                    return reject("asset", where + " clip must be an object");
                }
                if (const auto key = unknown_vector_key(static_cast<NSDictionary*>(clip), kVectorClipKeys);
                    !key.empty()) {
                    return reject("asset", where + " clip does not take " + key);
                }
                vellum::graphics::PathGeometry geometry;
                if (!parse_vector_path_data(static_cast<NSDictionary*>(clip)[@"d"],
                                            static_cast<NSDictionary*>(clip)[@"rule"], geometry,
                                            reason)) {
                    return reject("asset", where + " clip: " + reason);
                }
                op.clip = std::make_shared<const vellum::graphics::PathGeometry>(
                    std::move(geometry));
            }
        } else if (kind == "end") {
            op.kind = VectorOp::Kind::pop_group;
        } else if (kind == "path") {
            op.kind = VectorOp::Kind::path;
            vellum::graphics::PathGeometry geometry;
            if (!parse_vector_path_data(op_source[@"d"], op_source[@"fillRule"], geometry,
                                        reason)) {
                return reject("asset", where + " " + reason);
            }
            op.fill = {.color = vellum::graphics::Color::hex(0x000000)};
            if (!parse_vector_paint(op_source[@"fill"], op.fill, where + " fill", reason) ||
                !parse_vector_paint(op_source[@"stroke"], op.stroke, where + " stroke", reason)) {
                return reject("asset", reason);
            }
            auto& stroke = geometry.stroke;
            stroke.width = 1.0F;
            const auto number = [&](NSString* key, float& value) {
                id number_value = op_source[key];
                return number_value == nil || finite_number(number_value, value);
            };
            if (!number(@"strokeWidth", stroke.width) ||
                !number(@"strokeMiterLimit", stroke.miter_limit) ||
                !number(@"strokeDashoffset", stroke.dash_offset)) {
                return reject("asset", where + " stroke numbers must be finite");
            }
            if (op_source[@"strokeLineJoin"] != nil) {
                const auto join = keyword(op_source[@"strokeLineJoin"],
                    std::array<std::string_view, 3>{"miter", "round", "bevel"});
                if (!join) return reject("asset", where + " strokeLineJoin is unknown");
                stroke.join = static_cast<vellum::graphics::StrokeJoin>(*join);
            }
            if (op_source[@"strokeLineCap"] != nil) {
                const auto cap = keyword(op_source[@"strokeLineCap"],
                    std::array<std::string_view, 3>{"butt", "round", "square"});
                if (!cap) return reject("asset", where + " strokeLineCap is unknown");
                stroke.cap = static_cast<vellum::graphics::StrokeCap>(*cap);
            }
            if (id dashes = op_source[@"strokeDasharray"]; dashes != nil) {
                if (![dashes isKindOfClass:NSArray.class] ||
                    static_cast<NSArray*>(dashes).count > vellum::graphics::kMaxPathDashes) {
                    return reject("asset", where + " strokeDasharray must be an array of at most " +
                                               std::to_string(vellum::graphics::kMaxPathDashes) +
                                               " numbers");
                }
                for (id dash in static_cast<NSArray*>(dashes)) {
                    float length = 0.0F;
                    if (!finite_number(dash, length)) {
                        return reject("asset", where + " dashes must be finite numbers");
                    }
                    stroke.dash_array.push_back(length);
                }
            }
            op.path = std::make_shared<const vellum::graphics::PathGeometry>(std::move(geometry));
        } else {
            return reject("asset", where + " op must be group, end or path");
        }
        asset->ops.push_back(std::move(op));
    }
    std::string reason;
    if (!vellum::graphics::vector_asset_is_valid(*asset, &reason)) {
        return reject("asset", "cannot be drawn: " + reason);
    }
    NSArray* keys = [source.allKeys sortedArrayUsingSelector:@selector(compare:)];
    for (id key in keys) {
        if (![key isKindOfClass:NSString.class]) continue;
        const std::string name = cpp_string(key);
        if (contains(kVectorAttributes, name)) continue;
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(vellum::graphics::kStyleDroppedCode),
            .node_id = identity,
            .property = name,
            .message = "attribute " + name + " is not supported on vector nodes and was ignored",
        });
    }
    output.kind = vellum::graphics::SceneNode::Kind::vector;
    output.fill = vellum::graphics::Color::rgba(0.0F, 0.0F, 0.0F, 0.0F);
    output.fill_gradients.clear();
    output.box_shadows.clear();
    output.corner_radius = 0.0F;
    output.corner_radii.reset();
    output.vector = std::move(asset);
    return true;
}

constexpr std::array<std::string_view, 25> kPlotAttributes{
    "type", "id", "children", "style", "events", "accessibilityLabel", "accessibilityValue",
    "accessibilityRole", "accessibilityState", "plot", "channel", "source", "slots", "history",
    "orientation", "audioChannel", "min", "max", "baseline", "lineWidth", "barGap", "peakWidth",
    "lineColor", "fillColor", "peakColor",
};

// A plot names a value-feed channel and how to draw it; the samples arrive at
// run time through `PlotFeedSampler`, so a materialized plot starts empty.
bool materialize_plot(
    NSDictionary* source, const std::string& identity, MaterializeContext& context,
    vellum::graphics::SceneNode& output, std::string* error) {
    using vellum::graphics::PlotChannelShape;
    using vellum::graphics::PlotStyle;
    const auto reject = [&](std::string property, const std::string& problem) {
        std::string message = "plot " + identity + " " + problem;
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::error,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(vellum::graphics::kInvalidPlotCode),
            .node_id = identity,
            .property = std::move(property),
            .message = message,
        });
        set_error(error, std::move(message));
        return false;
    };
    id children = source[@"children"];
    if (children != nil && (![children isKindOfClass:NSArray.class] ||
                            static_cast<NSArray*>(children).count != 0U)) {
        return reject("children", "does not accept children");
    }
    vellum::graphics::PlotSpec spec;
    const auto style = keyword(source[@"plot"],
        std::array<std::string_view, 4>{"line", "area", "bar", "meter"});
    if (!style) return reject("plot", "plot must be line, area, bar or meter");
    spec.style = static_cast<PlotStyle>(*style);
    if (![source[@"channel"] isKindOfClass:NSString.class] ||
        static_cast<NSString*>(source[@"channel"]).length == 0U) {
        return reject("channel", "requires the name of a value-feed channel in channel");
    }
    spec.channel = cpp_string(source[@"channel"]);
    spec.shape = spec.style == PlotStyle::level_meter ? PlotChannelShape::meter
                                                      : PlotChannelShape::vector;
    if (source[@"source"] != nil) {
        const auto shape = keyword(source[@"source"],
            std::array<std::string_view, 3>{"scalar", "meter", "vector"});
        if (!shape) return reject("source", "source must be scalar, meter or vector");
        spec.shape = static_cast<PlotChannelShape>(*shape);
    }
    if (source[@"history"] != nil) {
        const auto history = keyword(source[@"history"],
            std::array<std::string_view, 2>{"scroll", "sweep"});
        if (!history) return reject("history", "history must be scroll or sweep");
        spec.history = static_cast<vellum::graphics::PlotHistory>(*history);
    }
    if (source[@"orientation"] != nil) {
        const auto orientation = keyword(source[@"orientation"],
            std::array<std::string_view, 2>{"vertical", "horizontal"});
        if (!orientation) return reject("orientation", "orientation must be vertical or horizontal");
        spec.orientation = static_cast<vellum::graphics::PlotOrientation>(*orientation);
    }
    // Whole numbers are checked as written, before any rounding to float, so
    // a value just past a whole number or the limit is refused as the
    // JavaScript validators refuse it.
    const auto count = [&](NSString* key, std::uint32_t& value, std::uint32_t limit) {
        id raw = source[key];
        if (raw == nil) return true;
        if (![raw isKindOfClass:NSNumber.class] ||
            CFGetTypeID((__bridge CFTypeRef)raw) == CFBooleanGetTypeID()) {
            return false;
        }
        const double number = [static_cast<NSNumber*>(raw) doubleValue];
        if (!std::isfinite(number) || number < 0.0 || number != std::floor(number) ||
            number > static_cast<double>(limit)) {
            return false;
        }
        value = static_cast<std::uint32_t>(number);
        return true;
    };
    if (!count(@"slots", spec.slot_count, vellum::graphics::kMaxPlotSlots)) {
        return reject("slots", "slots must be a whole number of at most " +
            std::to_string(vellum::graphics::kMaxPlotSlots));
    }
    if (!count(@"audioChannel", spec.audio_channel, vellum::graphics::kMaxPlotAudioChannel)) {
        return reject("audioChannel", "audioChannel must be a whole number of at most " +
                                          std::to_string(vellum::graphics::kMaxPlotAudioChannel));
    }
    const auto number = [&](NSString* key, float& value) {
        id raw = source[key];
        return raw == nil || finite_number(raw, value);
    };
    for (const auto& [key, field] : std::array<std::pair<NSString*, float*>, 6>{{
             {@"min", &spec.value_min}, {@"max", &spec.value_max},
             {@"baseline", &spec.baseline}, {@"lineWidth", &spec.line_width},
             {@"barGap", &spec.bar_gap}, {@"peakWidth", &spec.peak_width}}}) {
        if (!number(key, *field)) return reject(cpp_string(key), cpp_string(key) + " must be a finite number");
    }
    for (const auto& [key, field] : std::array<std::pair<NSString*, vellum::graphics::Color*>, 3>{{
             {@"lineColor", &spec.line_color}, {@"fillColor", &spec.fill_color},
             {@"peakColor", &spec.peak_color}}}) {
        if (source[key] == nil) continue;
        // Exactly # and six or eight hex digits, as the JavaScript validators
        // require; the shared parser also takes forms such as #0x12ab.
        const auto strict = [](id value) {
            if (![value isKindOfClass:NSString.class]) return false;
            const std::string text = cpp_string(value);
            if ((text.size() != 7U && text.size() != 9U) || text[0] != '#') return false;
            return std::all_of(text.begin() + 1, text.end(),
                               [](char c) { return std::isxdigit(static_cast<unsigned char>(c)) != 0; });
        };
        const auto color = strict(source[key]) ? parse_color(source[key]) : std::nullopt;
        if (!color) return reject(cpp_string(key), cpp_string(key) + " must be a colour");
        *field = *color;
    }
    std::string reason;
    if (!vellum::graphics::plot_is_valid(spec, &reason)) {
        return reject("plot", "cannot be drawn: " + reason);
    }
    NSArray* keys = [source.allKeys sortedArrayUsingSelector:@selector(compare:)];
    for (id key in keys) {
        if (![key isKindOfClass:NSString.class]) continue;
        const std::string name = cpp_string(key);
        if (contains(kPlotAttributes, name)) continue;
        context.diagnostics->push_back({
            .severity = LoweringDiagnostic::Severity::warning,
            .disposition = LoweringDiagnostic::Disposition::unsupported,
            .code = std::string(vellum::graphics::kStyleDroppedCode),
            .node_id = identity,
            .property = name,
            .message = "attribute " + name + " is not supported on plot nodes and was ignored",
        });
    }
    output.kind = vellum::graphics::SceneNode::Kind::plot;
    output.fill_gradients.clear();
    output.box_shadows.clear();
    output.corner_radius = 0.0F;
    output.corner_radii.reset();
    output.plot = std::make_shared<const vellum::graphics::PlotSpec>(std::move(spec));
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
// mask for its siblings, and `isolation: isolate` forces a group. `mixBlendMode`
// blends the node's group with what lies beneath it, in any spelling
// `parse_blend_mode` knows. A mask is coverage, not paint, so a mask node takes
// no blend mode other than `normal`.
// Animation tracks: `tracks` on a node is an array of
// { property, index?, stop?, keyframes: [{ time, value, easing? }] }. A colour
// property takes a "#rrggbb" or "#rrggbbaa" value; every other property takes
// a number. Easing is "linear", a CSS keyword ("ease", "ease-in", "ease-out",
// "ease-in-out"), or an object with `type` "cubicBezier" (x1, y1, x2, y2),
// "steps" (steps, position "start", "end", "none" or "both") or "spring"
// (stiffness, damping, mass, velocity).
constexpr std::size_t kMaximumTracksPerNode = vellum::graphics::kMaxTracksPerNode;
constexpr std::size_t kMaximumKeyframesPerTrack = vellum::graphics::kMaxKeyframesPerTrack;

std::optional<vellum::graphics::AnimatedProperty> animated_property(id value) {
    using vellum::graphics::AnimatedProperty;
    if (![value isKindOfClass:NSString.class]) return std::nullopt;
    static const std::array<std::pair<std::string_view, AnimatedProperty>, 11> names{{
        {"opacity", AnimatedProperty::opacity},
        {"translateX", AnimatedProperty::translate_x},
        {"translateY", AnimatedProperty::translate_y},
        {"scaleX", AnimatedProperty::scale_x},
        {"scaleY", AnimatedProperty::scale_y},
        {"rotation", AnimatedProperty::rotation_degrees},
        {"fill", AnimatedProperty::fill},
        {"strokeColor", AnimatedProperty::stroke_color},
        {"shadowColor", AnimatedProperty::shadow_color},
        {"gradientStopColor", AnimatedProperty::gradient_stop_color},
        {"textRunColor", AnimatedProperty::text_run_color},
    }};
    const std::string name = cpp_string(value);
    for (const auto& [candidate, property] : names) {
        if (candidate == name) return property;
    }
    return std::nullopt;
}

// The first key of `source` outside `allowed`, or empty.
template <std::size_t Size>
std::string unknown_key(NSDictionary* source, const std::array<std::string_view, Size>& allowed) {
    NSArray* keys = [source.allKeys sortedArrayUsingSelector:@selector(compare:)];
    for (id key in keys) {
        const std::string name = [key isKindOfClass:NSString.class] ? cpp_string(key) : "?";
        if (!contains(allowed, name)) return name;
    }
    return {};
}

bool index_field(NSDictionary* source, NSString* key, std::uint32_t& output) {
    id raw = source[key];
    output = 0U;
    if (raw == nil) return true;
    float value = 0.0F;
    if (!finite_number(raw, value) || value < 0.0F || value > 65535.0F ||
        value != std::floor(value)) {
        return false;
    }
    output = static_cast<std::uint32_t>(value);
    return true;
}

bool parse_easing(id raw, vellum::graphics::Easing& easing, std::string& reason) {
    using vellum::graphics::EasingKind;
    easing = {};
    if (raw == nil) return true;
    if ([raw isKindOfClass:NSString.class]) {
        const std::string name = cpp_string(raw);
        const auto bezier = [&](float x1, float y1, float x2, float y2) {
            easing = {.kind = EasingKind::cubic_bezier, .x1 = x1, .y1 = y1, .x2 = x2, .y2 = y2};
            return true;
        };
        if (name == "linear") return true;
        if (name == "ease") return bezier(0.25F, 0.1F, 0.25F, 1.0F);
        if (name == "ease-in") return bezier(0.42F, 0.0F, 1.0F, 1.0F);
        if (name == "ease-out") return bezier(0.0F, 0.0F, 0.58F, 1.0F);
        if (name == "ease-in-out") return bezier(0.42F, 0.0F, 0.58F, 1.0F);
        reason = "easing " + name + " is not linear, ease, ease-in, ease-out or ease-in-out";
        return false;
    }
    if (![raw isKindOfClass:NSDictionary.class]) {
        reason = "easing must be a keyword or an object";
        return false;
    }
    NSDictionary* source = raw;
    const std::string type =
        [source[@"type"] isKindOfClass:NSString.class] ? cpp_string(source[@"type"]) : "";
    const std::string extra = type == "cubicBezier"
        ? unknown_key(source, std::array<std::string_view, 5>{"type", "x1", "y1", "x2", "y2"})
        : type == "steps"
        ? unknown_key(source, std::array<std::string_view, 3>{"type", "steps", "position"})
        : unknown_key(source, std::array<std::string_view, 5>{
              "type", "stiffness", "damping", "mass", "velocity"});
    if (!extra.empty() &&
        (type == "cubicBezier" || type == "steps" || type == "spring")) {
        reason = type + " easing does not take " + extra;
        return false;
    }
    std::string* none = nullptr;
    if (type == "cubicBezier") {
        easing.kind = EasingKind::cubic_bezier;
        if (!finite_number(source[@"x1"], easing.x1) || !finite_number(source[@"y1"], easing.y1) ||
            !finite_number(source[@"x2"], easing.x2) || !finite_number(source[@"y2"], easing.y2)) {
            reason = "cubicBezier easing needs finite x1, y1, x2 and y2";
            return false;
        }
        return true;
    }
    if (type == "steps") {
        easing.kind = EasingKind::steps;
        float steps = 0.0F;
        if (!finite_number(source[@"steps"], steps) || steps < 1.0F || steps > 65535.0F ||
            steps != std::floor(steps)) {
            reason = "steps easing needs a whole number of steps from 1 to 65535";
            return false;
        }
        easing.steps = static_cast<std::uint32_t>(steps);
        const auto position = source[@"position"] == nil
            ? std::optional<std::size_t>{1U}
            : keyword(source[@"position"],
                      std::array<std::string_view, 4>{"start", "end", "none", "both"});
        if (!position) {
            reason = "steps position must be start, end, none or both";
            return false;
        }
        easing.step_position = static_cast<vellum::graphics::StepPosition>(*position);
        return true;
    }
    if (type == "spring") {
        easing.kind = EasingKind::spring;
        if (!number_field(source, @"stiffness", 100.0F, easing.stiffness, "spring.stiffness",
                          none) ||
            !number_field(source, @"damping", 10.0F, easing.damping, "spring.damping", none) ||
            !number_field(source, @"mass", 1.0F, easing.mass, "spring.mass", none) ||
            !number_field(source, @"velocity", 0.0F, easing.initial_velocity,
                          "spring.velocity", none)) {
            reason = "spring easing constants must be finite numbers";
            return false;
        }
        return true;
    }
    reason = "easing type must be cubicBezier, steps or spring";
    return false;
}

bool parse_tracks(id raw, const std::string& identity, vellum::graphics::SceneNode& output,
                  std::string* error) {
    if (raw == nil) return true;
    const auto fail = [&](const std::string& message) {
        set_error(error, "node " + identity + " tracks: " + message);
        return false;
    };
    if (![raw isKindOfClass:NSArray.class]) return fail("must be an array");
    NSArray* tracks = raw;
    if (tracks.count > kMaximumTracksPerNode) return fail("a node has at most " + std::to_string(kMaximumTracksPerNode) + " tracks");
    for (id track_value in tracks) {
        if (![track_value isKindOfClass:NSDictionary.class]) return fail("a track must be an object");
        NSDictionary* source = track_value;
        vellum::graphics::AnimationTrack track;
        const auto property = animated_property(source[@"property"]);
        if (!property) return fail("a track has an unknown property");
        track.property = *property;
        using vellum::graphics::AnimatedProperty;
        const bool indexed = track.property == AnimatedProperty::shadow_color ||
                             track.property == AnimatedProperty::gradient_stop_color ||
                             track.property == AnimatedProperty::text_run_color;
        const bool stopped = track.property == AnimatedProperty::gradient_stop_color;
        if (const auto extra = unknown_key(source, std::array<std::string_view, 4>{
                "property", "index", "stop", "keyframes"});
            !extra.empty() || (!indexed && source[@"index"] != nil) ||
            (!stopped && source[@"stop"] != nil)) {
            return fail("the " + cpp_string(source[@"property"]) + " track does not take " +
                        (!extra.empty() ? extra : source[@"index"] != nil && !indexed
                                                      ? std::string("index")
                                                      : std::string("stop")));
        }
        if (!index_field(source, @"index", track.index) ||
            !index_field(source, @"stop", track.stop)) {
            return fail("index and stop must be whole numbers from 0 to 65535");
        }
        if (![source[@"keyframes"] isKindOfClass:NSArray.class]) {
            return fail("a track needs a keyframes array");
        }
        NSArray* keyframes = source[@"keyframes"];
        if (keyframes.count > kMaximumKeyframesPerTrack) {
            return fail("a track has at most " + std::to_string(kMaximumKeyframesPerTrack) +
                        " keyframes");
        }
        const bool color = vellum::graphics::is_color_property(track.property);
        for (id key_value in keyframes) {
            if (![key_value isKindOfClass:NSDictionary.class]) {
                return fail("a keyframe must be an object");
            }
            NSDictionary* key = key_value;
            if (const auto extra = unknown_key(
                    key, std::array<std::string_view, 3>{"time", "value", "easing"});
                !extra.empty()) {
                return fail("a keyframe does not take " + extra);
            }
            vellum::graphics::AnimationKeyframe keyframe;
            float time = 0.0F;
            if (!finite_number(key[@"time"], time)) return fail("a keyframe needs a finite time");
            keyframe.time = [static_cast<NSNumber*>(key[@"time"]) doubleValue];
            if (color) {
                const auto parsed = parse_color(key[@"value"]);
                if (!parsed) return fail("a colour keyframe value must be #rrggbb or #rrggbbaa");
                keyframe.color = *parsed;
            } else if (!finite_number(key[@"value"], keyframe.value)) {
                return fail("a keyframe value must be a finite number");
            }
            std::string reason;
            if (!parse_easing(key[@"easing"], keyframe.easing, reason)) return fail(reason);
            track.keyframes.push_back(keyframe);
        }
        output.tracks.push_back(std::move(track));
    }
    vellum::graphics::SceneNode own = output;
    own.children.clear();
    std::string reason;
    if (vellum::graphics::find_invalid_tracks(own, reason) != nullptr) return fail(reason);
    return true;
}

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
            output.kind == Kind::image || output.kind == Kind::vector ||
            output.kind == Kind::plot;
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
    if (id blend = style[@"mixBlendMode"]; blend != nil) {
        const auto mode = [blend isKindOfClass:NSString.class]
            ? vellum::graphics::parse_blend_mode(cpp_string(blend))
            : std::nullopt;
        if (!mode) return reject("mixBlendMode", "must be a blend mode name");
        if (output.mask && *mode != vellum::graphics::BlendMode::normal) {
            return reject("mixBlendMode", "cannot apply to a mask");
        }
        output.blend_mode = *mode;
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

// A border occupies the area between the outer rounded rectangle and its
// inset. A solid uniform border uses one even-odd path, so translucent
// corners never blend twice. Individual sides share mitered edges.
bool append_box_border(
    NSDictionary* style, MaterializeContext& context,
    vellum::graphics::SceneNode& output, std::string* error) {
    using namespace vellum::graphics;
    const float width = output.bounds.width;
    const float height = output.bounds.height;
    if (width <= 0.0F || height <= 0.0F) return true;

    std::array<float, 4> widths{};
    std::array<Color, 4> colors{};
    for (std::size_t side = 0; side < widths.size(); ++side) {
        if (style[kBorderWidths[side]] == nil && style[kBorderColors[side]] == nil) continue;
        const auto color = parse_color(style[kBorderColors[side]]);
        if (!finite_number(style[kBorderWidths[side]], widths[side]) ||
            widths[side] < 0.0F || !color) {
            set_error(error, "box border sides require a nonnegative width and hex color");
            return false;
        }
        colors[side] = *color;
    }
    if (std::ranges::none_of(widths, [](float value) { return value > 0.0F; })) return true;
    auto radii = resolved_corner_radii(output);
    const auto scale_for = [](float available, float sum) {
        return sum > available ? available / sum : 1.0F;
    };
    const float scale = std::min({
        scale_for(width, radii.top_left + radii.top_right),
        scale_for(width, radii.bottom_left + radii.bottom_right),
        scale_for(height, radii.top_left + radii.bottom_left),
        scale_for(height, radii.top_right + radii.bottom_right),
    });
    radii.top_left *= scale;
    radii.top_right *= scale;
    radii.bottom_right *= scale;
    radii.bottom_left *= scale;
    const auto append = [&](std::size_t side, PathGeometry path, bool clipped) {
        SceneNode border;
        border.id = output.id + "/border-" + std::to_string(side);
        if (++context.nodes > kMaximumNodes || !context.identities.insert(border.id).second) {
            set_error(error, "generated box border exceeds node limit or duplicates a node id");
            return false;
        }
        border.kind = SceneNode::Kind::path;
        border.bounds = {0.0F, 0.0F, width, height};
        border.fill = colors[side];
        border.path = std::make_shared<const PathGeometry>(std::move(path));
        if (clipped && is_rounded(radii)) {
            border.clip = NodeClip{.rect = border.bounds, .radii = radii, .clips_own_paint = true};
        }
        output.children.push_back(std::move(border));
        return true;
    };

    auto [top, right, bottom, left] = widths;
    const auto same_color = [&](Color color) {
        const auto first = colors[0];
        return color.red == first.red && color.green == first.green &&
            color.blue == first.blue && color.alpha == first.alpha;
    };
    if (top == right && top == bottom && top == left &&
        std::ranges::all_of(colors, same_color)) {
        if (colors[0].alpha == 0.0F) return true;
        PathGeometry ring;
        ring.fill_rule = FillRule::evenodd;
        const auto rounded_box = [&](float x, float y, float w, float h, float inset) {
            const auto inner_radius = [inset](float radius) { return std::max(0.0F, radius - inset); };
            const float tl = inner_radius(radii.top_left), tr = inner_radius(radii.top_right);
            const float bl = inner_radius(radii.bottom_left), br = inner_radius(radii.bottom_right);
            const auto add = [&](PathVerb verb, std::initializer_list<float> values) {
                ring.verbs.push_back(verb);
                ring.values.insert(ring.values.end(), values);
            };
            const auto arc = [&](float r, float end_x, float end_y) {
                if (r > 0.0F) add(PathVerb::arc, {r, r, 0, 0, 1, end_x, end_y});
            };
            add(PathVerb::move, {x + tl, y});
            add(PathVerb::line, {x + w - tr, y}); arc(tr, x + w, y + tr);
            add(PathVerb::line, {x + w, y + h - br}); arc(br, x + w - br, y + h);
            add(PathVerb::line, {x + bl, y + h}); arc(bl, x, y + h - bl);
            add(PathVerb::line, {x, y + tl}); arc(tl, x + tl, y);
            add(PathVerb::close, {});
        };
        rounded_box(0, 0, width, height, 0);
        if (2 * top < width && 2 * top < height) {
            rounded_box(top, top, width - 2 * top, height - 2 * top, top);
        }
        return append(0, std::move(ring), false);
    }

    const auto inset = [&](float available, float first, float second) {
        const float factor = scale_for(available, first + second);
        return std::pair{first * factor, available - second * factor};
    };
    const auto [x0, x1] = inset(width, left, right);
    const auto [y0, y1] = inset(height, top, bottom);
    // Partition the rounded border ring by side.  A straight-sided polygon
    // clipped to the outer rounded box leaves the curved corner sections
    // outside the polygon (and therefore unpainted) when side colours differ.
    // Give each side the corresponding outer and inner contour, splitting
    // every corner at the border-width-weighted bisector so the four paths
    // meet without gaps or area overlap.
    if (x1 > x0 && y1 > y0) {
        const float inner_width = x1 - x0;
        const float inner_height = y1 - y0;
        // CSS keeps each inner corner's centre aligned with the outer corner;
        // horizontal and vertical insets therefore produce an elliptical arc.
        std::array<float, 4> inner_rx{
            std::max(0.0F, radii.top_left - left),
            std::max(0.0F, radii.top_right - right),
            std::max(0.0F, radii.bottom_right - right),
            std::max(0.0F, radii.bottom_left - left),
        };
        std::array<float, 4> inner_ry{
            std::max(0.0F, radii.top_left - top),
            std::max(0.0F, radii.top_right - top),
            std::max(0.0F, radii.bottom_right - bottom),
            std::max(0.0F, radii.bottom_left - bottom),
        };
        const float inner_scale_x = std::min({
            scale_for(inner_width, inner_rx[0] + inner_rx[1]),
            scale_for(inner_width, inner_rx[3] + inner_rx[2]),
        });
        const float inner_scale_y = std::min({
            scale_for(inner_height, inner_ry[0] + inner_ry[3]),
            scale_for(inner_height, inner_ry[1] + inner_ry[2]),
        });
        for (float& radius : inner_rx) radius *= inner_scale_x;
        for (float& radius : inner_ry) radius *= inner_scale_y;

        const auto append_arc = [](PathGeometry& path, float radius_x, float radius_y,
                                   float end_x, float end_y, bool clockwise) {
            if (radius_x > 0.0F && radius_y > 0.0F) {
                path.verbs.push_back(PathVerb::arc);
                path.values.insert(path.values.end(), {radius_x, radius_y, 0, 0,
                                                       clockwise ? 1.0F : 0.0F, end_x, end_y});
            } else {
                path.verbs.push_back(PathVerb::line);
                path.values.insert(path.values.end(), {end_x, end_y});
            }
        };
        const auto make_side = [&](std::size_t side) {
            PathGeometry path;
            const auto unit = [](float first, float second) {
                const float length = std::hypot(first, second);
                return length > 0.0F ? std::pair{first / length, second / length}
                                    : std::pair{0.70710678118F, 0.70710678118F};
            };
            const auto [utlx, utly] = unit(top, left);
            const auto [utrx, utry] = unit(top, right);
            const auto [ubrx, ubry] = unit(bottom, right);
            const auto [ublx, ubly] = unit(bottom, left);
            const float otlx = radii.top_left * utlx, otly = radii.top_left * utly;
            const float otrx = radii.top_right * utrx, otry = radii.top_right * utry;
            const float obrx = radii.bottom_right * ubrx, obry = radii.bottom_right * ubry;
            const float oblx = radii.bottom_left * ublx, obly = radii.bottom_left * ubly;
            const float itlx = inner_rx[0] * utlx, itly = inner_ry[0] * utly;
            const float itrx = inner_rx[1] * utrx, itry = inner_ry[1] * utry;
            const float ibrx = inner_rx[2] * ubrx, ibry = inner_ry[2] * ubry;
            const float iblx = inner_rx[3] * ublx, ibly = inner_ry[3] * ubly;
            const float inner_tl_split_x = x0 + inner_rx[0] - itlx;
            const float inner_tl_split_y = y0 + inner_ry[0] - itly;
            const float inner_tr_split_x = x1 - inner_rx[1] + itrx;
            const float inner_tr_split_y = y0 + inner_ry[1] - itry;
            const float inner_br_split_x = x1 - inner_rx[2] + ibrx;
            const float inner_br_split_y = y1 - inner_ry[2] + ibry;
            const float inner_bl_split_x = x0 + inner_rx[3] - iblx;
            const float inner_bl_split_y = y1 - inner_ry[3] + ibly;
            const auto move = [&](float x, float y) {
                path.verbs.push_back(PathVerb::move);
                path.values.insert(path.values.end(), {x, y});
            };
            const auto line = [&](float x, float y) {
                path.verbs.push_back(PathVerb::line);
                path.values.insert(path.values.end(), {x, y});
            };
            const auto close = [&] { path.verbs.push_back(PathVerb::close); };
            switch (side) {
            case 0: // top: top sectors of the top corners
                move(radii.top_left - otlx, radii.top_left - otly);
                append_arc(path, radii.top_left, radii.top_left, radii.top_left, 0.0F, true);
                line(width - radii.top_right, 0.0F);
                append_arc(path, radii.top_right, radii.top_right, width - radii.top_right + otrx,
                           radii.top_right - otry, true);
                line(inner_tr_split_x, inner_tr_split_y);
                append_arc(path, inner_rx[1], inner_ry[1], x1 - inner_rx[1], y0, false);
                line(x0 + inner_rx[0], y0);
                append_arc(path, inner_rx[0], inner_ry[0], inner_tl_split_x,
                           inner_tl_split_y, false);
                line(radii.top_left - otlx, radii.top_left - otly);
                close();
                break;
            case 1: // right: right sectors of the right corners
                move(width - radii.top_right + otrx, radii.top_right - otry);
                append_arc(path, radii.top_right, radii.top_right, width, radii.top_right, true);
                line(width, height - radii.bottom_right);
                append_arc(path, radii.bottom_right, radii.bottom_right,
                           width - radii.bottom_right + obrx,
                           height - radii.bottom_right + obry, true);
                line(inner_br_split_x, inner_br_split_y);
                append_arc(path, inner_rx[2], inner_ry[2], x1, y1 - inner_ry[2], false);
                line(x1, y0 + inner_ry[1]);
                append_arc(path, inner_rx[1], inner_ry[1], inner_tr_split_x,
                           inner_tr_split_y, false);
                line(width - radii.top_right + otrx, radii.top_right - otry);
                close();
                break;
            case 2: // bottom: bottom sectors of the bottom corners
                move(width - radii.bottom_right + obrx, height - radii.bottom_right + obry);
                append_arc(path, radii.bottom_right, radii.bottom_right,
                           width - radii.bottom_right, height, true);
                line(radii.bottom_left, height);
                append_arc(path, radii.bottom_left, radii.bottom_left,
                           radii.bottom_left - oblx,
                           height - radii.bottom_left + obly, true);
                line(inner_bl_split_x, inner_bl_split_y);
                append_arc(path, inner_rx[3], inner_ry[3], x0 + inner_rx[3], y1, false);
                line(x1 - inner_rx[2], y1);
                append_arc(path, inner_rx[2], inner_ry[2], inner_br_split_x,
                           inner_br_split_y, false);
                line(width - radii.bottom_right + obrx, height - radii.bottom_right + obry);
                close();
                break;
            case 3: // left: left sectors of the left corners
                move(radii.bottom_left - oblx, height - radii.bottom_left + obly);
                append_arc(path, radii.bottom_left, radii.bottom_left,
                           0.0F, height - radii.bottom_left, true);
                line(0.0F, radii.top_left);
                append_arc(path, radii.top_left, radii.top_left, radii.top_left - otlx,
                           radii.top_left - otly, true);
                line(inner_tl_split_x, inner_tl_split_y);
                append_arc(path, inner_rx[0], inner_ry[0], x0, y0 + inner_ry[0], false);
                line(x0, y1 - inner_ry[3]);
                append_arc(path, inner_rx[3], inner_ry[3], inner_bl_split_x,
                           inner_bl_split_y, false);
                line(radii.bottom_left - oblx, height - radii.bottom_left + obly);
                close();
                break;
            }
            return path;
        };
        for (std::size_t side = 0; side < widths.size(); ++side) {
            if (widths[side] == 0.0F || colors[side].alpha == 0.0F) continue;
            if (!append(side, make_side(side), true)) return false;
        }
        return true;
    }

    const std::array<std::array<float, 8>, 4> sides{{
        {0, 0, width, 0, x1, y0, x0, y0},
        {width, 0, width, height, x1, y1, x1, y0},
        {width, height, 0, height, x0, y1, x1, y1},
        {0, height, 0, 0, x0, y0, x0, y1},
    }};
    for (std::size_t side = 0; side < sides.size(); ++side) {
        if (widths[side] == 0.0F || colors[side].alpha == 0.0F) continue;
        PathGeometry path;
        path.verbs = {PathVerb::move, PathVerb::line, PathVerb::line, PathVerb::line, PathVerb::close};
        path.values.assign(sides[side].begin(), sides[side].end());
        if (!append(side, std::move(path), true)) return false;
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
        if (primitive_version == nil ||
            CFGetTypeID((__bridge CFTypeRef)primitive_version) == CFBooleanGetTypeID() ||
            primitive_version.doubleValue != 1.0 ||
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
            std::uint64_t first = 0;
            std::uint64_t last = 0;
            const std::uint64_t limit = static_cast<std::uint64_t>(input_value.length) + 1U;
            if (!protocol_index(start, limit, first) || !protocol_index(end, limit, last) ||
                first > last) {
                set_error(error,
                    "text-input selection must be an ordered UTF-16 range within value");
                return false;
            }
            selection_start = static_cast<NSUInteger>(first);
            selection_end = static_cast<NSUInteger>(last);
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
    context.node_records->push_back(
        {identity, type_name, absolute_box, read_node_source(source)});
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
    } else if (type_name == "vector") {
        if (!materialize_vector(source, identity, context, output, error)) return false;
    } else if (type_name == "plot") {
        if (!materialize_plot(source, identity, context, output, error)) return false;
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
                const auto run_index = static_cast<std::uint32_t>(output.text_runs.size());
                const auto run_style = dictionary_or_empty(child[@"style"]);
                output.text_runs.push_back({
                    .text = cpp_string(child[@"text"]),
                    .style = text_style(run_style, base_style),
                    .inherits_color = !parse_color(run_style[@"color"]).has_value(),
                });
                if (!bind_run_token_refs(child, run_index, context, output, error)) return false;
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

    const bool generates_label =
        ([type isEqualToString:@"button"] && !direct_text(source).empty()) ||
        (is_text_input && (input_value.length > 0U || input_placeholder.length > 0U));
    if (!bind_token_refs(source, style, type_name, generates_label, style_use.root, context,
            output, error)) {
        return false;
    }

    if (!parse_grouping(style, identity, style_use, context, output, error)) return false;
    if ((output.kind == vellum::graphics::SceneNode::Kind::rectangle ||
         output.kind == vellum::graphics::SceneNode::Kind::group) &&
        !append_box_border(style, context, output, error)) {
        return false;
    }

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
            if (!bind_token(source, @"color", context, label, error)) return false;
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
            if (!bind_token(source, @"color", context, label, error)) {
                return false;
            }
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
            if ([type isEqualToString:@"button"] &&
                !check_dropped_run_token_refs(
                    child,
                    parse_color(style[@"color"]).value_or(vellum::graphics::Color::hex(0x041412)),
                    context, output.id, error)) {
                return false;
            }
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
    return parse_tracks(source[@"tracks"], identity, output, error);
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
    // The envelope may name the scene version its tree was written for; a tree
    // that names none is written for the current version. The scene is built at
    // that version and refused when it carries a field the version lacks.
    if (id raw_version = envelope[@"sceneVersion"]; raw_version != nil) {
        std::uint64_t version = 0;
        if (!protocol_index(raw_version,
                            static_cast<std::uint64_t>(vellum::graphics::kSceneVersion) + 1U,
                            version) ||
            !vellum::graphics::scene_version_is_supported(static_cast<std::uint32_t>(version))) {
            const std::string message =
                "sceneVersion must name a scene version this renderer supports, from " +
                std::to_string(vellum::graphics::kMinSupportedSceneVersion) + " to " +
                std::to_string(vellum::graphics::kSceneVersion);
            if (failure_diagnostics != nullptr) {
                failure_diagnostics->push_back({
                    .severity = LoweringDiagnostic::Severity::error,
                    .disposition = LoweringDiagnostic::Disposition::unsupported,
                    .code = std::string(vellum::graphics::kSceneVersionMismatchCode),
                    .node_id = [tree[@"id"] isKindOfClass:NSString.class] ? cpp_string(tree[@"id"])
                                                                         : std::string{},
                    .property = "sceneVersion",
                    .message = message,
                });
            }
            set_error(error, message);
            return false;
        }
        candidate.scene.scene_version = static_cast<std::uint32_t>(version);
    }
    candidate.scene.width = width;
    candidate.scene.height = height;
    candidate.scene.background = parse_color(style[@"backgroundColor"])
        .value_or(vellum::graphics::Color::hex(0xF8FAFC));
    if (!parse_token_table(tree, candidate.scene.tokens, error)) return false;
    MaterializeContext context{
        .interactions = &candidate.interactions,
        .text_inputs = &candidate.text_inputs,
        .accessibility_nodes = &candidate.accessibility_nodes,
        .diagnostics = &candidate.diagnostics,
        .tokens = &candidate.scene.tokens,
        .node_records = &candidate.nodes,
    };
    if (!materialize_node(
            tree, {0.0F, 0.0F, width, height}, 0.0F, 0.0F, vellum::graphics::Affine2D{}, 0U,
            context, candidate.scene.root, error)) {
        if (failure_diagnostics != nullptr) {
            *failure_diagnostics = std::move(candidate.diagnostics);
        }
        return false;
    }
    candidate.scene.background_token = context.root_background_token;
    if (vellum::graphics::find_scene_version_mismatch(candidate.scene)) {
        auto loss = vellum::graphics::scene_loss_diagnostics(candidate.scene);
        loss.resize(std::min<std::size_t>(loss.size(), 1U));
        set_error(error, loss.empty() ? "the tree carries a field its scene version lacks"
                                      : loss.front().message);
        if (failure_diagnostics != nullptr) *failure_diagnostics = std::move(loss);
        return false;
    }
    output = std::move(candidate);
    return true;
}

}  // namespace vellum::authoring
