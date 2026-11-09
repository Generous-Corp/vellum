#pragma once

#include <vellum/graphics/scene.hpp>

#include <cstddef>
#include <optional>
#include <string>
#include <string_view>

namespace vellum::graphics {

/// The most commands one `PathGeometry` may hold.
inline constexpr std::size_t kMaxPathCommands = 4096U;
/// The most values one `PathGeometry` may hold: 64 KiB of `float` data.
inline constexpr std::size_t kMaxPathValues = 16384U;
/// The longest SVG path data string `parse_svg_path_data` accepts, in bytes.
inline constexpr std::size_t kMaxPathDataBytes = 64U * 1024U;
/// The most entries a stroke dash array may hold.
inline constexpr std::size_t kMaxPathDashes = 64U;

/// Whether a path can be drawn as given: it fits the command and value
/// budgets, every verb is known and has exactly its arity of values, the
/// first verb is `move`, every value is finite, arc radii are non-negative,
/// arc flags are 0 or 1, the fill rule, join and cap are known, and the stroke
/// has a finite colour, a finite non-negative width, a finite miter limit of
/// at least 1, a finite dash offset, and at most `kMaxPathDashes` finite
/// non-negative dashes whose sum is positive. On failure `reason`, when
/// non-null, says which rule failed.
[[nodiscard]] bool path_is_valid(const PathGeometry& path, std::string* reason = nullptr);

/// The box containing every point and control point of a valid path, with
/// each arc's whole ellipse, so it contains the filled area. It ignores the
/// stroke. Empty when the path has no commands.
[[nodiscard]] std::optional<Rect> path_control_bounds(const PathGeometry& path);

/// How far a visible stroke can reach beyond `path_control_bounds`: half the
/// width times the miter limit for miter joins, or times sqrt(2) for round
/// or square caps, whichever is larger. Zero when the stroke draws nothing.
[[nodiscard]] float path_stroke_outset(const PathStroke& stroke) noexcept;

/// Parses SVG path data (the `d` attribute grammar of SVG 2) into absolute
/// commands, replacing `output`'s verbs and values and keeping its fill rule,
/// stroke and anti-aliasing. Relative commands are resolved, `H` and `V` become lines, `S`
/// and `T` become cubics and quadratics with reflected control points, and a
/// command after `Z` that is not a move starts with an explicit move to the
/// closed subpath's start. Empty or whitespace-only data yields no commands.
/// Malformed data, data longer than `kMaxPathDataBytes`, or a result over
/// the path budgets fails, leaving `output` untouched and setting `reason`,
/// when non-null, to a message naming the byte offset.
[[nodiscard]] bool parse_svg_path_data(
    std::string_view data, PathGeometry& output, std::string* reason = nullptr);

}  // namespace vellum::graphics
