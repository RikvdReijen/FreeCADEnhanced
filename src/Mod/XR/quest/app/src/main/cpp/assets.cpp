// SPDX-License-Identifier: LGPL-2.1-or-later
#include "assets.h"

#include <android/asset_manager.h>

#include "log.h"

namespace fcxr {

bool Assets::read(const std::string& path, std::vector<uint8_t>* out) const {
    if (!manager_ || !out) return false;
    AAsset* asset = AAssetManager_open(manager_, path.c_str(), AASSET_MODE_BUFFER);
    if (!asset) {
        LOGW("asset not found: %s", path.c_str());
        return false;
    }
    const off64_t length = AAsset_getLength64(asset);
    out->resize(size_t(length));
    bool ok = true;
    if (length > 0) {
        const int read = AAsset_read(asset, out->data(), size_t(length));
        ok = read == int(length);
        if (!ok) LOGE("short read on asset %s (%d of %lld)", path.c_str(), read,
                      static_cast<long long>(length));
    }
    AAsset_close(asset);
    return ok;
}

bool Assets::readText(const std::string& path, std::string* out) const {
    std::vector<uint8_t> data;
    if (!read(path, &data)) return false;
    out->assign(reinterpret_cast<const char*>(data.data()), data.size());
    return true;
}

std::vector<std::string> Assets::list(const std::string& dir) const {
    std::vector<std::string> names;
    if (!manager_) return names;
    AAssetDir* handle = AAssetManager_openDir(manager_, dir.c_str());
    if (!handle) return names;
    while (const char* name = AAssetDir_getNextFileName(handle)) names.push_back(name);
    AAssetDir_close(handle);
    return names;
}

}  // namespace fcxr
