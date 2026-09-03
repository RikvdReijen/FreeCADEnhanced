// SPDX-License-Identifier: LGPL-2.1-or-later
#include "storage.h"

#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

#include <algorithm>
#include <cerrno>
#include <cstdio>
#include <cstring>

#include "fcxr.h"
#include "log.h"

namespace fcxr {

bool fileExists(const std::string& path) {
    struct stat st;
    return ::stat(path.c_str(), &st) == 0;
}

bool makeDirectories(const std::string& path) {
    if (path.empty()) return false;
    std::string current;
    for (size_t i = 0; i <= path.size(); ++i) {
        if (i == path.size() || path[i] == '/') {
            if (current.size() > 1) {
                if (::mkdir(current.c_str(), 0770) != 0 && errno != EEXIST) {
                    LOGE("mkdir %s failed: %s", current.c_str(), std::strerror(errno));
                    return false;
                }
            }
        }
        if (i < path.size()) current.push_back(path[i]);
    }
    return true;
}

bool readWholeFile(const std::string& path, std::vector<uint8_t>* out) {
    if (!out) return false;
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::fseek(f, 0, SEEK_END);
    const long size = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    if (size < 0) {
        std::fclose(f);
        return false;
    }
    out->resize(size_t(size));
    const size_t read = size ? std::fread(out->data(), 1, size_t(size), f) : 0;
    std::fclose(f);
    if (read != size_t(size)) {
        out->clear();
        return false;
    }
    return true;
}

bool writeWholeFileAtomic(const std::string& path, const uint8_t* data, size_t size) {
    const std::string tmp = path + ".tmp";
    FILE* f = std::fopen(tmp.c_str(), "wb");
    if (!f) {
        LOGE("cannot open %s for writing: %s", tmp.c_str(), std::strerror(errno));
        return false;
    }
    const size_t written = size ? std::fwrite(data, 1, size, f) : 0;
    const bool flushed = std::fflush(f) == 0;
    std::fclose(f);
    if (written != size || !flushed) {
        ::unlink(tmp.c_str());
        return false;
    }
    if (::rename(tmp.c_str(), path.c_str()) != 0) {
        LOGE("rename %s failed: %s", tmp.c_str(), std::strerror(errno));
        ::unlink(tmp.c_str());
        return false;
    }
    return true;
}

// ------------------------------------------------------------------ storage

bool Storage::init(const std::string& filesDir, const std::string& cacheDir) {
    filesDir_ = filesDir;
    cacheDir_ = cacheDir;
    while (!filesDir_.empty() && filesDir_.back() == '/') filesDir_.pop_back();
    if (filesDir_.empty()) return false;
    makeDirectories(libraryDir());
    makeDirectories(filesDir_ + "/thumbs");
    loadSettings();
    LOGI("storage ready at %s", filesDir_.c_str());
    return true;
}

std::string Storage::safeFileName(const std::string& name) {
    std::string out;
    out.reserve(name.size());
    for (char c : name) {
        const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
                        (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == ' ';
        out.push_back(ok ? c : '_');
    }
    // Strip leading dots so nothing can become a hidden file or "..".
    size_t start = 0;
    while (start < out.size() && (out[start] == '.' || out[start] == ' ')) ++start;
    out = out.substr(start);
    if (out.empty()) out = "scene";
    if (out.size() < 5 || out.compare(out.size() - 5, 5, ".fcxr") != 0) out += ".fcxr";
    if (out.size() > 120) out = out.substr(0, 115) + ".fcxr";
    return out;
}

std::vector<LibraryEntry> Storage::list() const {
    std::vector<LibraryEntry> entries;
    if (filesDir_.empty()) return entries;
    const std::string dir = libraryDir();
    DIR* handle = ::opendir(dir.c_str());
    if (!handle) return entries;
    while (struct dirent* item = ::readdir(handle)) {
        const std::string name = item->d_name;
        if (name.size() < 6 || name.compare(name.size() - 5, 5, ".fcxr") != 0) continue;
        LibraryEntry entry;
        entry.fileName = name;
        entry.path = dir + "/" + name;
        struct stat st;
        if (::stat(entry.path.c_str(), &st) == 0) {
            entry.bytes = uint64_t(st.st_size);
            entry.modified = int64_t(st.st_mtime);
        }
        entry.title = name.substr(0, name.size() - 5);

        // Peek at the manifest: read just the header plus the JSON chunk.
        std::vector<uint8_t> head;
        FILE* f = std::fopen(entry.path.c_str(), "rb");
        if (f) {
            head.resize(64 * 1024);
            const size_t read = std::fread(head.data(), 1, head.size(), f);
            head.resize(read);
            std::fclose(f);
        }
        if (head.size() > 20) {
            // fcxrRead needs the whole file; instead parse the JSON chunk here.
            const uint8_t* p = head.data();
            if (!std::memcmp(p, "FCXR", 4)) {
                const uint32_t payload = uint32_t(p[12]) | (uint32_t(p[13]) << 8) |
                                         (uint32_t(p[14]) << 16) | (uint32_t(p[15]) << 24);
                if (!std::memcmp(p + 16, "JSON", 4) && payload + 20 <= head.size()) {
                    json::ParseError err;
                    const json::Value manifest =
                        json::parse(reinterpret_cast<const char*>(p + 20), payload, &err);
                    if (err.ok) {
                        const std::string source =
                            manifest["asset"]["source_document"].asString();
                        if (!source.empty()) entry.title = source;
                        entry.environment = manifest["scene"]["environment"].asString();
                    }
                }
            }
        }
        entry.hasThumbnail = fileExists(thumbnailPath(entry.path));
        entries.push_back(std::move(entry));
    }
    ::closedir(handle);
    std::sort(entries.begin(), entries.end(),
              [](const LibraryEntry& a, const LibraryEntry& b) {
                  if (a.modified != b.modified) return a.modified > b.modified;
                  return a.fileName < b.fileName;
              });
    return entries;
}

bool Storage::save(const std::string& name, const uint8_t* data, size_t size,
                   std::string* pathOut) {
    if (filesDir_.empty() || !data || !size) return false;
    makeDirectories(libraryDir());
    const std::string path = libraryDir() + "/" + safeFileName(name);
    if (!writeWholeFileAtomic(path, data, size)) return false;
    if (pathOut) *pathOut = path;
    LOGI("saved %zu bytes to %s", size, path.c_str());
    return true;
}

bool Storage::load(const std::string& path, std::vector<uint8_t>* out) const {
    return readWholeFile(path, out);
}

bool Storage::remove(const std::string& path) {
    const std::string thumb = thumbnailPath(path);
    if (fileExists(thumb)) ::unlink(thumb.c_str());
    settings_.recents.erase(
        std::remove(settings_.recents.begin(), settings_.recents.end(), path),
        settings_.recents.end());
    saveSettings();
    return ::unlink(path.c_str()) == 0;
}

std::string Storage::thumbnailPath(const std::string& libraryPath) const {
    const size_t slash = libraryPath.find_last_of('/');
    std::string name = slash == std::string::npos ? libraryPath : libraryPath.substr(slash + 1);
    if (name.size() > 5 && name.compare(name.size() - 5, 5, ".fcxr") == 0)
        name = name.substr(0, name.size() - 5);
    return filesDir_ + "/thumbs/" + name + ".png";
}

bool Storage::saveThumbnail(const std::string& libraryPath, const uint8_t* png, size_t size) {
    makeDirectories(filesDir_ + "/thumbs");
    return writeWholeFileAtomic(thumbnailPath(libraryPath), png, size);
}

bool Storage::loadSettings() {
    std::vector<uint8_t> data;
    if (!readWholeFile(filesDir_ + "/settings.json", &data) || data.empty()) return false;
    json::ParseError err;
    const json::Value root =
        json::parse(reinterpret_cast<const char*>(data.data()), data.size(), &err);
    if (!err.ok) {
        LOGW("settings.json is corrupt (%s); using defaults", err.message.c_str());
        return false;
    }
    settings_.serverHost = root["server_host"].asString();
    settings_.serverPort = root["server_port"].asInt(47810);
    settings_.serverToken = root["server_token"].asString();
    settings_.serverName = root["server_name"].asString();
    settings_.environment = root["environment"].asString();
    settings_.lastDocument = root["last_document"].asString();
    settings_.passthrough = root["passthrough"].asBool(false);
    settings_.userScaleOverride = root["user_scale_override"].asFloat(0.0f);
    settings_.recents.clear();
    const json::Value& recents = root["recents"];
    for (size_t i = 0; i < recents.size(); ++i) {
        if (recents[i].isString()) settings_.recents.push_back(recents[i].asString());
    }
    return true;
}

bool Storage::saveSettings() const {
    json::Value root = json::Value::makeObject();
    root.set("server_host", json::Value(settings_.serverHost));
    root.set("server_port", json::Value(settings_.serverPort));
    root.set("server_token", json::Value(settings_.serverToken));
    root.set("server_name", json::Value(settings_.serverName));
    root.set("environment", json::Value(settings_.environment));
    root.set("last_document", json::Value(settings_.lastDocument));
    root.set("passthrough", json::Value(settings_.passthrough));
    root.set("user_scale_override", json::Value(double(settings_.userScaleOverride)));
    json::Value recents = json::Value::makeArray();
    for (const std::string& r : settings_.recents) recents.push(json::Value(r));
    root.set("recents", recents);
    const std::string text = root.dump(2);
    return writeWholeFileAtomic(filesDir_ + "/settings.json",
                                reinterpret_cast<const uint8_t*>(text.data()), text.size());
}

void Storage::noteRecent(const std::string& path) {
    if (path.empty()) return;
    settings_.recents.erase(
        std::remove(settings_.recents.begin(), settings_.recents.end(), path),
        settings_.recents.end());
    settings_.recents.insert(settings_.recents.begin(), path);
    if (settings_.recents.size() > 12) settings_.recents.resize(12);
    settings_.lastDocument = path;
    saveSettings();
}

}  // namespace fcxr
