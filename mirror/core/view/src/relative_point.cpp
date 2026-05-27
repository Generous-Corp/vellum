#include "pulp/view/relative_point.hpp"

#include <cctype>
#include <cstddef>
#include <cstdio>
#include <sstream>
#include <stdexcept>

namespace pulp::view {

// ── AST ────────────────────────────────────────────────────────────────────

struct RelativeExpression::Node {
    enum class Kind {
        Number,
        Identifier,   // unqualified (parent-relative built-in)
        Qualified,    // <ns>.<member>
        BinaryOp,     // + - * /
        UnaryMinus,
    };

    Kind kind = Kind::Number;
    float number = 0.0f;
    std::string ident;       // for Identifier
    std::string qual_ns;     // for Qualified — namespace ("parent", "prev", ...)
    std::string qual_member; // for Qualified — member ("right", "top", ...)
    char op = 0;             // for BinaryOp ('+','-','*','/')
    std::shared_ptr<Node> lhs;
    std::shared_ptr<Node> rhs;
};

namespace {

// ── Rect member access ─────────────────────────────────────────────────────
//
// Returns the named coordinate on a rectangle. Throws if the member is
// not one of the supported names.
float rect_member(const Rect& r, const std::string& name) {
    if (name == "left" || name == "x")          return r.x;
    if (name == "right")                         return r.right();
    if (name == "top" || name == "y")            return r.y;
    if (name == "bottom")                        return r.bottom();
    if (name == "width" || name == "w")          return r.width;
    if (name == "height" || name == "h")         return r.height;
    if (name == "centerx" || name == "centerX")  return r.center().x;
    if (name == "centery" || name == "centerY")  return r.center().y;
    throw std::runtime_error("RelativeExpression: unknown rect member '" + name + "'");
}

// ── Parser ─────────────────────────────────────────────────────────────────
//
// Recursive-descent over a tokenised view of the source string. Errors
// carry the byte position so callers can surface useful diagnostics.

struct Token {
    enum class Kind {
        End, Number, Ident, Plus, Minus, Star, Slash, Dot, LParen, RParen, Comma,
    };
    Kind kind = Kind::End;
    std::string text;
    float number = 0.0f;
    size_t pos = 0;
};

class Lexer {
public:
    explicit Lexer(const std::string& s) : s_(s) {}

    Token next() {
        skip_ws();
        Token t;
        t.pos = pos_;
        if (pos_ >= s_.size()) { t.kind = Token::Kind::End; return t; }
        char c = s_[pos_];
        if (c == '+') { ++pos_; t.kind = Token::Kind::Plus;   t.text = "+"; return t; }
        if (c == '-') { ++pos_; t.kind = Token::Kind::Minus;  t.text = "-"; return t; }
        if (c == '*') { ++pos_; t.kind = Token::Kind::Star;   t.text = "*"; return t; }
        if (c == '/') { ++pos_; t.kind = Token::Kind::Slash;  t.text = "/"; return t; }
        if (c == '.') {
            // Period followed by a non-digit is the qualified-name separator.
            // Period followed by a digit (e.g. ".5") is a leading-dot number.
            if (pos_ + 1 < s_.size() && std::isdigit(static_cast<unsigned char>(s_[pos_ + 1]))) {
                return read_number();
            }
            ++pos_; t.kind = Token::Kind::Dot; t.text = "."; return t;
        }
        if (c == '(') { ++pos_; t.kind = Token::Kind::LParen; t.text = "("; return t; }
        if (c == ')') { ++pos_; t.kind = Token::Kind::RParen; t.text = ")"; return t; }
        if (c == ',') { ++pos_; t.kind = Token::Kind::Comma;  t.text = ","; return t; }
        if (std::isdigit(static_cast<unsigned char>(c))) return read_number();
        if (std::isalpha(static_cast<unsigned char>(c)) || c == '_') return read_ident();
        throw std::invalid_argument(fmt_err(pos_, std::string("unexpected character '") + c + "'"));
    }

private:
    void skip_ws() {
        while (pos_ < s_.size() && std::isspace(static_cast<unsigned char>(s_[pos_]))) ++pos_;
    }

    Token read_number() {
        Token t; t.pos = pos_; t.kind = Token::Kind::Number;
        size_t start = pos_;
        bool seen_dot = false;
        while (pos_ < s_.size()) {
            char c = s_[pos_];
            if (std::isdigit(static_cast<unsigned char>(c))) { ++pos_; continue; }
            if (c == '.' && !seen_dot) { seen_dot = true; ++pos_; continue; }
            break;
        }
        t.text = s_.substr(start, pos_ - start);
        try {
            t.number = std::stof(t.text);
        } catch (...) {
            throw std::invalid_argument(fmt_err(start, "malformed number '" + t.text + "'"));
        }
        return t;
    }

    Token read_ident() {
        Token t; t.pos = pos_; t.kind = Token::Kind::Ident;
        size_t start = pos_;
        while (pos_ < s_.size()) {
            char c = s_[pos_];
            if (std::isalnum(static_cast<unsigned char>(c)) || c == '_') { ++pos_; continue; }
            break;
        }
        t.text = s_.substr(start, pos_ - start);
        return t;
    }

    static std::string fmt_err(size_t pos, const std::string& msg) {
        std::ostringstream oss;
        oss << "RelativeExpression parse error at column " << pos << ": " << msg;
        return oss.str();
    }

    const std::string& s_;
    size_t pos_ = 0;
};

class Parser {
public:
    explicit Parser(const std::string& s) : lex_(s), src_(s) { advance(); }

    // expr = term (('+'|'-') term)*
    std::shared_ptr<RelativeExpression::Node> parse_expr() {
        auto lhs = parse_term();
        while (cur_.kind == Token::Kind::Plus || cur_.kind == Token::Kind::Minus) {
            char op = cur_.text[0];
            advance();
            auto rhs = parse_term();
            auto n = std::make_shared<RelativeExpression::Node>();
            n->kind = RelativeExpression::Node::Kind::BinaryOp;
            n->op = op;
            n->lhs = lhs;
            n->rhs = rhs;
            lhs = n;
        }
        return lhs;
    }

    // term = factor (('*'|'/') factor)*
    std::shared_ptr<RelativeExpression::Node> parse_term() {
        auto lhs = parse_factor();
        while (cur_.kind == Token::Kind::Star || cur_.kind == Token::Kind::Slash) {
            char op = cur_.text[0];
            advance();
            auto rhs = parse_factor();
            auto n = std::make_shared<RelativeExpression::Node>();
            n->kind = RelativeExpression::Node::Kind::BinaryOp;
            n->op = op;
            n->lhs = lhs;
            n->rhs = rhs;
            lhs = n;
        }
        return lhs;
    }

    // factor = number | identifier | qualified | '(' expr ')' | '-' factor
    std::shared_ptr<RelativeExpression::Node> parse_factor() {
        if (cur_.kind == Token::Kind::Minus) {
            advance();
            auto inner = parse_factor();
            auto n = std::make_shared<RelativeExpression::Node>();
            n->kind = RelativeExpression::Node::Kind::UnaryMinus;
            n->lhs = inner;
            return n;
        }
        if (cur_.kind == Token::Kind::LParen) {
            advance();
            auto inner = parse_expr();
            expect(Token::Kind::RParen, "expected ')'");
            advance();
            return inner;
        }
        if (cur_.kind == Token::Kind::Number) {
            auto n = std::make_shared<RelativeExpression::Node>();
            n->kind = RelativeExpression::Node::Kind::Number;
            n->number = cur_.number;
            advance();
            return n;
        }
        if (cur_.kind == Token::Kind::Ident) {
            std::string ident = cur_.text;
            advance();
            if (cur_.kind == Token::Kind::Dot) {
                advance();
                if (cur_.kind != Token::Kind::Ident)
                    throw std::invalid_argument(err_at(cur_.pos, "expected identifier after '.'"));
                auto n = std::make_shared<RelativeExpression::Node>();
                n->kind = RelativeExpression::Node::Kind::Qualified;
                n->qual_ns = ident;
                n->qual_member = cur_.text;
                advance();
                return n;
            }
            auto n = std::make_shared<RelativeExpression::Node>();
            n->kind = RelativeExpression::Node::Kind::Identifier;
            n->ident = ident;
            return n;
        }
        throw std::invalid_argument(err_at(cur_.pos, "expected expression"));
    }

    void expect_end() {
        if (cur_.kind != Token::Kind::End)
            throw std::invalid_argument(err_at(cur_.pos, "unexpected trailing token '" + cur_.text + "'"));
    }

    Token& cur() { return cur_; }
    void advance() { cur_ = lex_.next(); }

private:
    void expect(Token::Kind k, const std::string& msg) {
        if (cur_.kind != k) throw std::invalid_argument(err_at(cur_.pos, msg));
    }

    std::string err_at(size_t pos, const std::string& msg) const {
        std::ostringstream oss;
        oss << "RelativeExpression parse error at column " << pos << " in \"" << src_ << "\": " << msg;
        return oss.str();
    }

    Lexer lex_;
    const std::string& src_;
    Token cur_;
};

// ── Evaluator ──────────────────────────────────────────────────────────────

float eval_node(const RelativeExpression::Node& n,
                const Rect& parent,
                const std::map<std::string, Rect>& named) {
    using Kind = RelativeExpression::Node::Kind;
    switch (n.kind) {
        case Kind::Number:
            return n.number;
        case Kind::Identifier:
            // Unqualified identifiers resolve against the parent rect.
            return rect_member(parent, n.ident);
        case Kind::Qualified: {
            // `parent.foo` is the same as the unqualified `foo` for ergonomics.
            if (n.qual_ns == "parent") return rect_member(parent, n.qual_member);
            auto it = named.find(n.qual_ns);
            if (it == named.end())
                throw std::runtime_error("RelativeExpression: undefined named rect '" + n.qual_ns + "'");
            return rect_member(it->second, n.qual_member);
        }
        case Kind::UnaryMinus:
            return -eval_node(*n.lhs, parent, named);
        case Kind::BinaryOp: {
            float l = eval_node(*n.lhs, parent, named);
            float r = eval_node(*n.rhs, parent, named);
            switch (n.op) {
                case '+': return l + r;
                case '-': return l - r;
                case '*': return l * r;
                case '/':
                    if (r == 0.0f)
                        throw std::runtime_error("RelativeExpression: division by zero");
                    return l / r;
            }
        }
    }
    return 0.0f;  // unreachable
}

// ── Serialiser ─────────────────────────────────────────────────────────────

int op_prec(char op) {
    switch (op) {
        case '+': case '-': return 1;
        case '*': case '/': return 2;
    }
    return 0;
}

void emit(const RelativeExpression::Node& n, std::ostringstream& out, int parent_prec) {
    using Kind = RelativeExpression::Node::Kind;
    switch (n.kind) {
        case Kind::Number: {
            // Compact representation — strip trailing zeros / unnecessary dot.
            char buf[32];
            std::snprintf(buf, sizeof(buf), "%g", n.number);
            out << buf;
            return;
        }
        case Kind::Identifier:
            out << n.ident;
            return;
        case Kind::Qualified:
            out << n.qual_ns << '.' << n.qual_member;
            return;
        case Kind::UnaryMinus:
            out << '-';
            emit(*n.lhs, out, 3);  // bind tighter than any binary op
            return;
        case Kind::BinaryOp: {
            int p = op_prec(n.op);
            bool paren = p < parent_prec;
            if (paren) out << '(';
            emit(*n.lhs, out, p);
            out << ' ' << n.op << ' ';
            emit(*n.rhs, out, p + 1);
            if (paren) out << ')';
            return;
        }
    }
}

}  // namespace

// ── Public API ─────────────────────────────────────────────────────────────

RelativeExpression RelativeExpression::parse(const std::string& source) {
    Parser p(source);
    auto root = p.parse_expr();
    p.expect_end();
    return RelativeExpression(std::move(root));
}

float RelativeExpression::evaluate(const Rect& parent,
                                   const std::map<std::string, Rect>& named) const {
    if (!root_) throw std::runtime_error("RelativeExpression: evaluate on empty expression");
    return eval_node(*root_, parent, named);
}

float RelativeExpression::evaluate(const std::string& source,
                                   const Rect& parent,
                                   const std::map<std::string, Rect>& named) {
    return parse(source).evaluate(parent, named);
}

std::string RelativeExpression::to_string() const {
    if (!root_) return {};
    std::ostringstream out;
    emit(*root_, out, 0);
    return out.str();
}

RelativePoint RelativePoint::parse(const std::string& source) {
    // Split on the top-level comma. We can't just split on the first comma
    // because parenthesised sub-expressions don't contain commas (the
    // grammar above forbids commas inside expr), so a single scan suffices.
    int depth = 0;
    std::size_t comma_pos = std::string::npos;
    for (std::size_t i = 0; i < source.size(); ++i) {
        char c = source[i];
        if (c == '(') ++depth;
        else if (c == ')') --depth;
        else if (c == ',' && depth == 0) { comma_pos = i; break; }
    }
    if (comma_pos == std::string::npos)
        throw std::invalid_argument("RelativePoint: expected 'x, y' — comma missing in \"" + source + "\"");

    auto x = RelativeExpression::parse(source.substr(0, comma_pos));
    auto y = RelativeExpression::parse(source.substr(comma_pos + 1));
    return RelativePoint(std::move(x), std::move(y));
}

Point RelativePoint::evaluate(const Rect& parent,
                              const std::map<std::string, Rect>& named) const {
    return Point{ x_.evaluate(parent, named), y_.evaluate(parent, named) };
}

}  // namespace pulp::view
