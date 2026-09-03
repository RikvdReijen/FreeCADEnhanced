// SPDX-License-Identifier: LGPL-2.1-or-later
//
// A small, strict JSON parser and serialiser.
//
// Everything in the Quest client that talks to the desktop workbench goes
// through this file (FCXR manifests, environment specs, the sync protocol and
// the Google Drive REST responses), so it aims to be *correct* rather than
// fast:
//
//   * RFC 8259 grammar, no trailing commas, no comments, no NaN/Infinity
//     literals (the writers on the Python side never emit them either).
//   * \uXXXX escapes including surrogate pairs -> UTF-8.
//   * Rejects invalid UTF-8 in string literals.
//   * Configurable nesting limit (default 256) so a hostile document cannot
//     blow the native stack.
//   * Numbers are parsed as double; integer accessors round-trip exactly for
//     anything up to 2^53 which covers every index and byte offset we use.
//
// Objects preserve insertion order and are stored as a flat vector of
// key/value pairs. Lookup is linear, which is the right trade for the small
// objects in our schemas and keeps allocations down.
#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace fcxr {
namespace json {

enum class Type { Null, Bool, Number, String, Array, Object };

class Value;
using Array = std::vector<Value>;
using Member = std::pair<std::string, Value>;
using Object = std::vector<Member>;

class Value {
public:
    Value() : type_(Type::Null) {}
    explicit Value(bool b) : type_(Type::Bool), bool_(b) {}
    explicit Value(double d) : type_(Type::Number), num_(d) {}
    explicit Value(int i) : type_(Type::Number), num_(static_cast<double>(i)) {}
    explicit Value(int64_t i) : type_(Type::Number), num_(static_cast<double>(i)) {}
    explicit Value(const char* s) : type_(Type::String), str_(s) {}
    explicit Value(std::string s) : type_(Type::String), str_(std::move(s)) {}
    explicit Value(Array a) : type_(Type::Array), arr_(std::move(a)) {}
    explicit Value(Object o) : type_(Type::Object), obj_(std::move(o)) {}

    static Value makeArray() { return Value(Array{}); }
    static Value makeObject() { return Value(Object{}); }

    Type type() const { return type_; }
    bool isNull() const { return type_ == Type::Null; }
    bool isBool() const { return type_ == Type::Bool; }
    bool isNumber() const { return type_ == Type::Number; }
    bool isString() const { return type_ == Type::String; }
    bool isArray() const { return type_ == Type::Array; }
    bool isObject() const { return type_ == Type::Object; }

    // ---- typed access with defaults (never throws) -----------------------
    bool asBool(bool def = false) const { return type_ == Type::Bool ? bool_ : def; }
    double asDouble(double def = 0.0) const { return type_ == Type::Number ? num_ : def; }
    float asFloat(float def = 0.0f) const {
        return type_ == Type::Number ? static_cast<float>(num_) : def;
    }
    int asInt(int def = 0) const;
    int64_t asInt64(int64_t def = 0) const;
    const std::string& asString(const std::string& def = kEmptyString) const {
        return type_ == Type::String ? str_ : def;
    }

    // ---- containers ------------------------------------------------------
    size_t size() const {
        if (type_ == Type::Array) return arr_.size();
        if (type_ == Type::Object) return obj_.size();
        return 0;
    }
    // Out of range / wrong type yields the shared null value.
    // Both an `int` and a `size_t` overload exist so that literal indices
    // (`v[0]`) are not ambiguous against the string-key overloads.
    const Value& operator[](size_t i) const {
        return (type_ == Type::Array && i < arr_.size()) ? arr_[i] : null();
    }
    const Value& operator[](int i) const {
        return (type_ == Type::Array && i >= 0 && (size_t)i < arr_.size()) ? arr_[(size_t)i]
                                                                          : null();
    }
    const Value& operator[](const char* key) const { return find(key); }
    const Value& operator[](const std::string& key) const { return find(key); }
    const Value& find(const std::string& key) const;
    bool has(const std::string& key) const { return !find(key).isNull(); }

    const Array& array() const { return arr_; }
    const Object& object() const { return obj_; }
    Array& array() { return arr_; }
    Object& object() { return obj_; }

    // ---- building --------------------------------------------------------
    void push(Value v) {
        if (type_ != Type::Array) { type_ = Type::Array; arr_.clear(); }
        arr_.push_back(std::move(v));
    }
    // Sets (replacing an existing member of the same name).
    void set(const std::string& key, Value v);

    static const Value& null();

    // ---- serialisation ---------------------------------------------------
    // `indent < 0` -> compact (no whitespace). `indent >= 0` -> pretty.
    std::string dump(int indent = -1) const;
    void dump(std::string& out, int indent = -1) const;

private:
    static const std::string kEmptyString;
    Type type_;
    bool bool_ = false;
    double num_ = 0.0;
    std::string str_;
    Array arr_;
    Object obj_;
};

struct ParseError {
    bool ok = true;
    size_t offset = 0;   // byte offset of the failure
    size_t line = 1;     // 1-based
    size_t column = 1;   // 1-based, in bytes
    std::string message;
    explicit operator bool() const { return ok; }
};

struct ParseOptions {
    int maxDepth = 256;
    // Trailing bytes after the top level value must be whitespace only.
    bool requireEof = true;
};

// Parses `text`. On failure returns a Null value and fills `err`.
Value parse(const char* text, size_t length, ParseError* err = nullptr,
            const ParseOptions& opts = ParseOptions());
Value parse(const std::string& text, ParseError* err = nullptr,
            const ParseOptions& opts = ParseOptions());

// Helpers used all over the FCXR / env-spec readers.
// Reads `n` numbers from an array value into `out`; returns false (leaving
// `out` untouched) if the value is not an array of at least `n` numbers.
bool readFloats(const Value& v, float* out, size_t n);

// Appends a JSON string literal (including the surrounding quotes) for `s`.
void escapeString(const std::string& s, std::string& out);

// Formats a double the way our writers do: shortest representation that
// round-trips, integers without a decimal point.
void formatNumber(double d, std::string& out);

}  // namespace json
}  // namespace fcxr
