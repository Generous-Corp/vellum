// SPDX-License-Identifier: MIT
//
// Scoring a snapshot against the detection manifest, and the new-format
// report.

#include <vellum/authoring/import_detect_report.hpp>

#include <algorithm>
#include <cctype>
#include <iomanip>
#include <regex>
#include <sstream>
#include <string_view>

namespace vellum::authoring::import_detect {

namespace {

std::string json_escape(std::string_view input) {
    std::string out;
    out.reserve(input.size() + 8);
    for (char ch : input) {
        switch (ch) {
            case '\\': out += "\\\\"; break;
            case '"': out += "\\\""; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (static_cast<unsigned char>(ch) < 0x20) {
                    std::ostringstream ss;
                    ss << "\\u" << std::hex << std::setw(4)
                       << std::setfill('0')
                       << static_cast<int>(static_cast<unsigned char>(ch));
                    out += ss.str();
                } else {
                    out += ch;
                }
                break;
        }
    }
    return out;
}

void add_distinct(std::vector<std::string>& list, const std::string& value) {
    if (std::find(list.begin(), list.end(), value) == list.end()) list.push_back(value);
}

bool contains(const std::vector<std::string>& list, const std::string& value) {
    return std::find(list.begin(), list.end(), value) != list.end();
}

std::string lower(std::string text) {
    for (auto& c : text) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return text;
}

// ECMAScript search, the html-script-src clause's semantics; an invalid
// pattern finds nothing.
bool regex_finds(const std::string& pattern, const std::string& text) {
    try {
        return std::regex_search(text, std::regex(pattern, std::regex::ECMAScript));
    } catch (const std::regex_error&) {
        return false;
    }
}

// A regex matching exactly `literal`, for a script src new to the manifest.
std::string literal_regex(const std::string& literal) {
    static const std::string special = R"(\^$.|?*+()[]{}/)";
    std::string out;
    for (char c : literal) {
        if (special.find(c) != std::string::npos) out += '\\';
        out += c;
    }
    return out;
}

// The fingerprint clauses for one side of the report: Tailwind tokens as one
// any-of clause, then one clause per script src regex and script type.
// Additions name new srcs, so they are escaped into literal regexes.
void write_clauses(std::ostringstream& o, const std::vector<std::string>& tokens,
                   const std::vector<std::string>& srcs, const std::vector<std::string>& types,
                   bool srcs_are_literal) {
    std::vector<std::string> clauses;
    if (!tokens.empty()) {
        std::string clause = "{\"kind\": \"tailwind-config-token\", \"any-of\": [";
        for (size_t i = 0; i < tokens.size(); ++i)
            clause += (i ? ", \"" : "\"") + json_escape(tokens[i]) + "\"";
        clauses.push_back(clause + "]}");
    }
    for (const auto& src : srcs) {
        clauses.push_back("{\"kind\": \"html-script-src\", \"regex\": \"" +
                          json_escape(srcs_are_literal ? literal_regex(src) : src) + "\"}");
    }
    for (const auto& type : types)
        clauses.push_back("{\"kind\": \"html-script-type\", \"value\": \"" + json_escape(type) + "\"}");
    if (clauses.empty()) return;
    o << "\n";
    for (size_t i = 0; i < clauses.size(); ++i)
        o << "    " << clauses[i] << (i + 1 < clauses.size() ? ",\n" : "\n");
    o << "  ";
}

}  // namespace

DetectionResult detect(const ImportsManifest& manifest, const InputSnapshot& snap) {
    DetectionResult best;
    best.ok = true;

    int best_matched = -1;
    for (const auto& src : manifest.sources) {
        for (const auto& fmt : src.formats) {
            int matched = 0;
            std::vector<std::string> matched_kinds;
            std::vector<std::string> unmatched_kinds;
            for (const auto& cl : fmt.fingerprint) {
                if (match_clause(cl, snap)) {
                    ++matched;
                    add_distinct(matched_kinds, cl.raw_kind);
                } else {
                    add_distinct(unmatched_kinds, cl.raw_kind);
                }
            }
            const int total = static_cast<int>(fmt.fingerprint.size());
            const int confidence = total > 0 ? (matched * 100 / total) : 0;
            // Strict modes (DESIGN.md): reject this format unless every clause
            // matches (match="all-of") or the confidence clears the floor
            // (min_confidence_pct). This prevents generic Markdown files
            // with `---fence---` from masquerading as DESIGN.md.
            if (fmt.match == "all-of" && matched < total) continue;
            if (fmt.min_confidence_pct > 0 && confidence < fmt.min_confidence_pct) continue;
            // Prefer the highest confidence ratio first. A format with a
            // smaller fingerprint can be a much stronger match than one with
            // more raw matches in a larger fingerprint. Use matched count only
            // as the deterministic tie-breaker, preserving manifest order for
            // exact ties.
            const auto lhs = static_cast<long long>(matched) * best.total_clauses;
            const auto rhs = static_cast<long long>(best.matched_clauses) * total;
            const bool better = best_matched < 0 || lhs > rhs ||
                                (lhs == rhs && matched > best_matched);
            if (matched > 0 && better) {
                best.source = src.source;
                best.format_version = fmt.format_version;
                best.parser_version = fmt.parser_version;
                best.matched_clauses = matched;
                best.total_clauses = total;
                best.confidence_pct = total > 0 ? (matched * 100 / total) : 0;
                best.matched_kinds = std::move(matched_kinds);
                best.unmatched_kinds = std::move(unmatched_kinds);
                best_matched = matched;
            }
        }
    }
    return best;
}

NewFormatReport build_new_format_report(const ImportsManifest& manifest,
                                        const InputSnapshot& snap,
                                        const DetectionResult& closest) {
    NewFormatReport r;
    r.candidate_source = closest.source;
    r.based_on_source = closest.source;
    r.based_on_format_version = closest.format_version;
    if (!closest.format_version.empty())
        r.candidate_format_version = closest.format_version + "+next";
    else
        r.candidate_format_version.clear();

    // The closest format's fingerprint, in manifest order.
    std::vector<std::string> known_tokens;
    std::vector<std::string> src_regexes;
    std::vector<std::string> known_types;
    for (const auto& src : manifest.sources) {
        if (src.source != closest.source) continue;
        for (const auto& fmt : src.formats) {
            if (fmt.format_version != closest.format_version) continue;
            for (const auto& cl : fmt.fingerprint) {
                if (cl.kind == FingerprintClause::Kind::tailwind_config_token)
                    for (const auto& tok : cl.any_of) add_distinct(known_tokens, tok);
                if (cl.kind == FingerprintClause::Kind::html_script_src && !cl.regex.empty())
                    add_distinct(src_regexes, cl.regex);
                if (cl.kind == FingerprintClause::Kind::html_script_type && !cl.value.empty())
                    add_distinct(known_types, lower(cl.value));
            }
        }
    }

    // With no closest match there is no baseline to diff against, and the
    // report stays empty rather than drafting an entry from guessed data.
    if (closest.source.empty()) return r;

    // Tailwind tokens, both ways. A category the closest format does not use
    // at all still reports every input value as an addition.
    for (const auto& tok : snap.tailwind_tokens) {
        if (!contains(known_tokens, tok)) add_distinct(r.additions, tok);
    }
    for (const auto& tok : known_tokens) {
        if (!contains(snap.tailwind_tokens, tok)) add_distinct(r.removals, tok);
    }

    // Script srcs against the html-script-src regexes, both ways.
    for (const auto& pattern : src_regexes) {
        bool any = false;
        for (const auto& src : snap.script_srcs) any = any || regex_finds(pattern, src);
        if (!any) add_distinct(r.script_removals, pattern);
    }
    for (const auto& src : snap.script_srcs) {
        bool known = false;
        for (const auto& pattern : src_regexes) known = known || regex_finds(pattern, src);
        if (!known) add_distinct(r.script_additions, src);
    }

    // Script types against the html-script-type values, both ways.
    std::vector<std::string> types;
    for (const auto& type : snap.script_types) add_distinct(types, lower(type));
    for (const auto& type : known_types) {
        if (!contains(types, type)) add_distinct(r.type_removals, type);
    }
    for (const auto& type : types) {
        if (!contains(known_types, type)) add_distinct(r.type_additions, type);
    }

    // Cap suggestions so the output stays human-readable.
    for (auto* list : {&r.additions, &r.removals, &r.script_additions, &r.script_removals,
                       &r.type_additions, &r.type_removals}) {
        if (list->size() > 20) list->resize(20);
    }
    return r;
}

std::string render_new_format_json(const NewFormatReport& r) {
    std::ostringstream o;
    o << "{\n";
    o << "  \"candidate-source\": \"" << json_escape(r.candidate_source) << "\",\n";
    o << "  \"candidate-format-version\": \"" << json_escape(r.candidate_format_version) << "\",\n";
    o << "  \"fingerprint-additions\": [";
    write_clauses(o, r.additions, r.script_additions, r.type_additions, true);
    o << "],\n";
    o << "  \"fingerprint-removals\": [";
    write_clauses(o, r.removals, r.script_removals, r.type_removals, false);
    o << "],\n";
    o << "  \"based-on\": {\"source\": \"" << json_escape(r.based_on_source)
      << "\", \"format-version\": \"" << json_escape(r.based_on_format_version) << "\"}\n";
    o << "}\n";
    return o.str();
}

}  // namespace vellum::authoring::import_detect
