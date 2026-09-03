// SPDX-License-Identifier: LGPL-2.1-or-later
#include "json.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace fcxr {
namespace json {

const std::string Value::kEmptyString;

const Value& Value::null() {
    static const Value kNull;
    return kNull;
}

int Value::asInt(int def) const {
    if (type_ != Type::Number) return def;
    if (!(num_ >= -2147483648.0 && num_ <= 2147483647.0)) return def;
    return static_cast<int>(num_ < 0 ? num_ - 0.5 : num_ + 0.5);
}

int64_t Value::asInt64(int64_t def) const {
    if (type_ != Type::Number) return def;
    if (!(num_ >= -9.2233720368547758e18 && num_ <= 9.2233720368547758e18)) return def;
    return static_cast<int64_t>(num_ < 0 ? num_ - 0.5 : num_ + 0.5);
}

const Value& Value::find(const std::string& key) const {
    if (type_ != Type::Object) return null();
    for (const Member& m : obj_) {
        if (m.first == key) return m.second;
    }
    return null();
}

void Value::set(const std::string& key, Value v) {
    if (type_ != Type::Object) { type_ = Type::Object; obj_.clear(); }
    for (Member& m : obj_) {
        if (m.first == key) { m.second = std::move(v); return; }
    }
    obj_.emplace_back(key, std::move(v));
}

// ---------------------------------------------------------------- utf8 bits

// Appends the UTF-8 encoding of `cp` (already validated as a scalar value).
static void appendUtf8(uint32_t cp, std::string& out) {
    if (cp < 0x80) {
        out.push_back(static_cast<char>(cp));
    } else if (cp < 0x800) {
        out.push_back(static_cast<char>(0xC0 | (cp >> 6)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else if (cp < 0x10000) {
        out.push_back(static_cast<char>(0xE0 | (cp >> 12)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    } else {
        out.push_back(static_cast<char>(0xF0 | (cp >> 18)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 12) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3F)));
        out.push_back(static_cast<char>(0x80 | (cp & 0x3F)));
    }
}

// Validates one UTF-8 sequence starting at p (p < end). Returns its length in
// bytes, or 0 if the sequence is malformed. Rejects overlong encodings,
// surrogates and anything above U+10FFFF.
static size_t utf8SequenceLength(const unsigned char* p, const unsigned char* end) {
    unsigned char c = *p;
    if (c < 0x80) return 1;
    auto cont = [&](size_t i) { return p + i < end && (p[i] & 0xC0) == 0x80; };
    if ((c & 0xE0) == 0xC0) {
        if (c < 0xC2) return 0;  // overlong
        return cont(1) ? 2 : 0;
    }
    if ((c & 0xF0) == 0xE0) {
        if (!cont(1) || !cont(2)) return 0;
        uint32_t cp = ((c & 0x0Fu) << 12) | ((p[1] & 0x3Fu) << 6) | (p[2] & 0x3Fu);
        if (cp < 0x800) return 0;                      // overlong
        if (cp >= 0xD800 && cp <= 0xDFFF) return 0;    // surrogate
        return 3;
    }
    if ((c & 0xF8) == 0xF0) {
        if (!cont(1) || !cont(2) || !cont(3)) return 0;
        uint32_t cp = ((c & 0x07u) << 18) | ((p[1] & 0x3Fu) << 12) |
                      ((p[2] & 0x3Fu) << 6) | (p[3] & 0x3Fu);
        if (cp < 0x10000 || cp > 0x10FFFF) return 0;
        return 4;
    }
    return 0;
}

// ------------------------------------------------------------------ parser

namespace {

class Parser {
public:
    Parser(const char* text, size_t length, const ParseOptions& opts)
        : s_(reinterpret_cast<const unsigned char*>(text)), n_(length), opts_(opts) {}

    bool run(Value& out, ParseError& err) {
        skipWhitespace();
        if (!parseValue(out, 0)) { err = error_; return false; }
        if (opts_.requireEof) {
            skipWhitespace();
            if (i_ != n_) { fail("trailing data after top level value"); err = error_; return false; }
        }
        return true;
    }

private:
    const unsigned char* s_;
    size_t n_;
    size_t i_ = 0;
    ParseOptions opts_;
    ParseError error_;

    bool fail(const char* msg) {
        if (!error_.ok) return false;  // keep the first failure
        error_.ok = false;
        error_.offset = i_;
        error_.message = msg;
        size_t line = 1, col = 1;
        for (size_t k = 0; k < i_ && k < n_; ++k) {
            if (s_[k] == '\n') { ++line; col = 1; } else { ++col; }
        }
        error_.line = line;
        error_.column = col;
        return false;
    }

    bool eof() const { return i_ >= n_; }
    unsigned char peek() const { return i_ < n_ ? s_[i_] : 0; }

    void skipWhitespace() {
        while (i_ < n_) {
            unsigned char c = s_[i_];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r') ++i_;
            else break;
        }
    }

    bool literal(const char* lit, size_t len) {
        if (i_ + len > n_ || std::memcmp(s_ + i_, lit, len) != 0) return fail("invalid literal");
        i_ += len;
        return true;
    }

    bool parseValue(Value& out, int depth) {
        if (depth > opts_.maxDepth) return fail("maximum nesting depth exceeded");
        if (eof()) return fail("unexpected end of input");
        switch (peek()) {
            case '{': return parseObject(out, depth);
            case '[': return parseArray(out, depth);
            case '"': {
                std::string s;
                if (!parseString(s)) return false;
                out = Value(std::move(s));
                return true;
            }
            case 't':
                if (!literal("true", 4)) return false;
                out = Value(true);
                return true;
            case 'f':
                if (!literal("false", 5)) return false;
                out = Value(false);
                return true;
            case 'n':
                if (!literal("null", 4)) return false;
                out = Value();
                return true;
            default: return parseNumber(out);
        }
    }

    bool parseObject(Value& out, int depth) {
        ++i_;  // '{'
        Object members;
        skipWhitespace();
        if (peek() == '}') { ++i_; out = Value(std::move(members)); return true; }
        for (;;) {
            skipWhitespace();
            if (peek() != '"') return fail("expected object key string");
            std::string key;
            if (!parseString(key)) return false;
            skipWhitespace();
            if (peek() != ':') return fail("expected ':' after object key");
            ++i_;
            skipWhitespace();
            Value v;
            if (!parseValue(v, depth + 1)) return false;
            members.emplace_back(std::move(key), std::move(v));
            skipWhitespace();
            if (peek() == ',') { ++i_; continue; }
            if (peek() == '}') { ++i_; break; }
            return fail("expected ',' or '}' in object");
        }
        out = Value(std::move(members));
        return true;
    }

    bool parseArray(Value& out, int depth) {
        ++i_;  // '['
        Array items;
        skipWhitespace();
        if (peek() == ']') { ++i_; out = Value(std::move(items)); return true; }
        for (;;) {
            skipWhitespace();
            Value v;
            if (!parseValue(v, depth + 1)) return false;
            items.push_back(std::move(v));
            skipWhitespace();
            if (peek() == ',') { ++i_; continue; }
            if (peek() == ']') { ++i_; break; }
            return fail("expected ',' or ']' in array");
        }
        out = Value(std::move(items));
        return true;
    }

    // Reads exactly four hex digits.
    bool hex4(uint32_t& out) {
        if (i_ + 4 > n_) return fail("truncated \\u escape");
        uint32_t v = 0;
        for (int k = 0; k < 4; ++k) {
            unsigned char c = s_[i_ + k];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= (uint32_t)(c - '0');
            else if (c >= 'a' && c <= 'f') v |= (uint32_t)(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= (uint32_t)(c - 'A' + 10);
            else return fail("invalid hex digit in \\u escape");
        }
        i_ += 4;
        out = v;
        return true;
    }

    bool parseString(std::string& out) {
        ++i_;  // opening quote
        out.clear();
        for (;;) {
            if (eof()) return fail("unterminated string");
            unsigned char c = s_[i_];
            if (c == '"') { ++i_; return true; }
            if (c == '\\') {
                ++i_;
                if (eof()) return fail("unterminated escape");
                unsigned char e = s_[i_++];
                switch (e) {
                    case '"': out.push_back('"'); break;
                    case '\\': out.push_back('\\'); break;
                    case '/': out.push_back('/'); break;
                    case 'b': out.push_back('\b'); break;
                    case 'f': out.push_back('\f'); break;
                    case 'n': out.push_back('\n'); break;
                    case 'r': out.push_back('\r'); break;
                    case 't': out.push_back('\t'); break;
                    case 'u': {
                        uint32_t cp = 0;
                        if (!hex4(cp)) return false;
                        if (cp >= 0xD800 && cp <= 0xDBFF) {
                            // High surrogate: a low surrogate must follow.
                            if (i_ + 1 < n_ && s_[i_] == '\\' && s_[i_ + 1] == 'u') {
                                i_ += 2;
                                uint32_t lo = 0;
                                if (!hex4(lo)) return false;
                                if (lo < 0xDC00 || lo > 0xDFFF)
                                    return fail("invalid low surrogate in \\u escape");
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                            } else {
                                return fail("unpaired high surrogate in \\u escape");
                            }
                        } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                            return fail("unpaired low surrogate in \\u escape");
                        }
                        appendUtf8(cp, out);
                        break;
                    }
                    default: return fail("invalid escape sequence");
                }
                continue;
            }
            if (c < 0x20) return fail("unescaped control character in string");
            size_t len = utf8SequenceLength(s_ + i_, s_ + n_);
            if (len == 0) return fail("invalid UTF-8 in string");
            out.append(reinterpret_cast<const char*>(s_ + i_), len);
            i_ += len;
        }
    }

    bool parseNumber(Value& out) {
        const size_t start = i_;
        if (peek() == '-') ++i_;
        if (eof()) return fail("truncated number");
        if (peek() == '0') {
            ++i_;
        } else if (peek() >= '1' && peek() <= '9') {
            while (!eof() && peek() >= '0' && peek() <= '9') ++i_;
        } else {
            return fail("invalid number");
        }
        if (!eof() && peek() == '.') {
            ++i_;
            if (eof() || peek() < '0' || peek() > '9') return fail("expected digit after '.'");
            while (!eof() && peek() >= '0' && peek() <= '9') ++i_;
        }
        if (!eof() && (peek() == 'e' || peek() == 'E')) {
            ++i_;
            if (!eof() && (peek() == '+' || peek() == '-')) ++i_;
            if (eof() || peek() < '0' || peek() > '9') return fail("expected digit in exponent");
            while (!eof() && peek() >= '0' && peek() <= '9') ++i_;
        }
        // strtod on a NUL terminated copy. The process locale is "C" (we never
        // call setlocale), so '.' is the decimal separator.
        char stack[64];
        const size_t len = i_ - start;
        std::string heap;
        const char* text;
        if (len < sizeof(stack)) {
            std::memcpy(stack, s_ + start, len);
            stack[len] = '\0';
            text = stack;
        } else {
            heap.assign(reinterpret_cast<const char*>(s_ + start), len);
            text = heap.c_str();
        }
        char* endp = nullptr;
        double d = std::strtod(text, &endp);
        if (endp != text + len) return fail("number could not be converted");
        out = Value(d);
        return true;
    }
};

}  // namespace

Value parse(const char* text, size_t length, ParseError* err, const ParseOptions& opts) {
    ParseError local;
    Value v;
    Parser p(text, length, opts);
    if (!p.run(v, local)) {
        if (err) *err = local;
        return Value();
    }
    if (err) *err = ParseError();
    return v;
}

Value parse(const std::string& text, ParseError* err, const ParseOptions& opts) {
    return parse(text.data(), text.size(), err, opts);
}

// -------------------------------------------------------------- serialiser

void escapeString(const std::string& s, std::string& out) {
    out.push_back('"');
    for (size_t i = 0; i < s.size(); ++i) {
        unsigned char c = static_cast<unsigned char>(s[i]);
        switch (c) {
            case '"': out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b"; break;
            case '\f': out += "\\f"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out.push_back(static_cast<char>(c));
                }
        }
    }
    out.push_back('"');
}

void formatNumber(double d, std::string& out) {
    if (!std::isfinite(d)) { out += "0"; return; }  // never emit NaN/Infinity
    char buf[40];
    // Integers (up to the exact range of double) print without a fraction so a
    // manifest written here is byte-identical to one written by Python's json
    // module for the index/offset fields.
    if (d == std::floor(d) && std::fabs(d) < 1e15) {
        std::snprintf(buf, sizeof(buf), "%lld", static_cast<long long>(d));
        out += buf;
        return;
    }
    for (int prec = 15; prec <= 17; ++prec) {
        std::snprintf(buf, sizeof(buf), "%.*g", prec, d);
        // Defensive: if a non-C locale ever leaks in, normalise the separator.
        for (char* p = buf; *p; ++p) {
            if (*p == ',') *p = '.';
        }
        if (std::strtod(buf, nullptr) == d) break;
    }
    out += buf;
}

static void dumpValue(const Value& v, std::string& out, int indent, int depth, bool sortKeys) {
    const bool pretty = indent >= 0;
    auto newlineIndent = [&](int d) {
        if (!pretty) return;
        out.push_back('\n');
        out.append(static_cast<size_t>(indent * d), ' ');
    };

    switch (v.type()) {
        case Type::Null: out += "null"; break;
        case Type::Bool: out += v.asBool() ? "true" : "false"; break;
        case Type::Number: formatNumber(v.asDouble(), out); break;
        case Type::String: escapeString(v.asString(), out); break;
        case Type::Array: {
            const Array& a = v.array();
            if (a.empty()) { out += "[]"; break; }
            out.push_back('[');
            for (size_t i = 0; i < a.size(); ++i) {
                if (i) out.push_back(',');
                newlineIndent(depth + 1);
                dumpValue(a[i], out, indent, depth + 1, sortKeys);
            }
            newlineIndent(depth);
            out.push_back(']');
            break;
        }
        case Type::Object: {
            const Object& o = v.object();
            if (o.empty()) { out += "{}"; break; }
            std::vector<const Member*> members;
            members.reserve(o.size());
            for (const Member& m : o) members.push_back(&m);
            if (sortKeys) {
                std::sort(members.begin(), members.end(),
                          [](const Member* a, const Member* b) { return a->first < b->first; });
            }
            out.push_back('{');
            for (size_t i = 0; i < members.size(); ++i) {
                if (i) out.push_back(',');
                newlineIndent(depth + 1);
                escapeString(members[i]->first, out);
                out.push_back(':');
                if (pretty) out.push_back(' ');
                dumpValue(members[i]->second, out, indent, depth + 1, sortKeys);
            }
            newlineIndent(depth);
            out.push_back('}');
            break;
        }
    }
}

void Value::dump(std::string& out, int indent, bool sortKeys) const {
    dumpValue(*this, out, indent, 0, sortKeys);
}

std::string Value::dump(int indent, bool sortKeys) const {
    std::string out;
    out.reserve(256);
    dumpValue(*this, out, indent, 0, sortKeys);
    return out;
}

bool readFloats(const Value& v, float* out, size_t n) {
    if (!v.isArray() || v.size() < n) return false;
    for (size_t i = 0; i < n; ++i) {
        if (!v[i].isNumber()) return false;
    }
    for (size_t i = 0; i < n; ++i) out[i] = v[i].asFloat();
    return true;
}

}  // namespace json
}  // namespace fcxr
