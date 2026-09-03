// SPDX-License-Identifier: LGPL-2.1-or-later
#include "sync_client.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "log.h"

namespace fcxr {
namespace {

constexpr int kDiscoveryPort = 47811;
constexpr const char* kApiPrefix = "/api/v1";
constexpr int kDefaultTimeoutMs = 8000;
constexpr int kEventTimeoutMs = 40000;   // the server long polls for ~30 s
constexpr size_t kMaxResponse = 192u * 1024u * 1024u;

bool setTimeouts(int fd, int timeoutMs) {
    struct timeval tv;
    tv.tv_sec = timeoutMs / 1000;
    tv.tv_usec = (timeoutMs % 1000) * 1000;
    const bool a = ::setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv)) == 0;
    const bool b = ::setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv)) == 0;
    return a && b;
}

// Connects with a bounded wait so a wrong IP cannot stall the worker for the
// kernel's default two minutes.
int connectTo(const std::string& host, int port, int timeoutMs, std::string* error) {
    char portText[16];
    std::snprintf(portText, sizeof(portText), "%d", port);

    struct addrinfo hints;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_INET;  // the sync server binds IPv4
    hints.ai_socktype = SOCK_STREAM;
    struct addrinfo* list = nullptr;
    const int rc = ::getaddrinfo(host.c_str(), portText, &hints, &list);
    if (rc != 0 || !list) {
        if (error) *error = "cannot resolve " + host;
        return -1;
    }

    int fd = -1;
    for (struct addrinfo* ai = list; ai; ai = ai->ai_next) {
        fd = ::socket(ai->ai_family, ai->ai_socktype, ai->ai_protocol);
        if (fd < 0) continue;
        const int flags = ::fcntl(fd, F_GETFL, 0);
        ::fcntl(fd, F_SETFL, flags | O_NONBLOCK);
        int result = ::connect(fd, ai->ai_addr, ai->ai_addrlen);
        if (result != 0 && errno == EINPROGRESS) {
            fd_set writeSet;
            FD_ZERO(&writeSet);
            FD_SET(fd, &writeSet);
            struct timeval tv;
            tv.tv_sec = timeoutMs / 1000;
            tv.tv_usec = (timeoutMs % 1000) * 1000;
            result = ::select(fd + 1, nullptr, &writeSet, nullptr, &tv) > 0 ? 0 : -1;
            if (result == 0) {
                int soError = 0;
                socklen_t length = sizeof(soError);
                ::getsockopt(fd, SOL_SOCKET, SO_ERROR, &soError, &length);
                if (soError != 0) result = -1;
            }
        }
        ::fcntl(fd, F_SETFL, flags);
        if (result == 0) {
            const int one = 1;
            ::setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
            setTimeouts(fd, timeoutMs);
            break;
        }
        ::close(fd);
        fd = -1;
    }
    ::freeaddrinfo(list);
    if (fd < 0 && error) *error = "cannot reach " + host + ":" + std::to_string(port);
    return fd;
}

bool sendAll(int fd, const uint8_t* data, size_t size) {
    size_t sent = 0;
    while (sent < size) {
        const ssize_t n = ::send(fd, data + sent, size - sent, MSG_NOSIGNAL);
        if (n <= 0) {
            if (n < 0 && (errno == EINTR)) continue;
            return false;
        }
        sent += size_t(n);
    }
    return true;
}

std::string lowerCase(std::string s) {
    for (char& c : s) {
        if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    }
    return s;
}

std::string trim(const std::string& s) {
    size_t a = 0, b = s.size();
    while (a < b && (s[a] == ' ' || s[a] == '\t')) ++a;
    while (b > a && (s[b - 1] == ' ' || s[b - 1] == '\t' || s[b - 1] == '\r')) --b;
    return s.substr(a, b - a);
}

}  // namespace

std::string urlEncode(const std::string& value) {
    static const char* kHex = "0123456789ABCDEF";
    std::string out;
    out.reserve(value.size() + 8);
    for (unsigned char c : value) {
        if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
            c == '-' || c == '_' || c == '.' || c == '~') {
            out.push_back(char(c));
        } else {
            out.push_back('%');
            out.push_back(kHex[c >> 4]);
            out.push_back(kHex[c & 0xF]);
        }
    }
    return out;
}

// ------------------------------------------------------------------ client

SyncClient::SyncClient() = default;

SyncClient::~SyncClient() { stop(); }

void SyncClient::start() {
    if (running_.exchange(true)) return;
    worker_ = std::thread([this] { workerMain(); });
}

void SyncClient::stop() {
    if (!running_.exchange(false)) return;
    eventsActive_.store(false);
    wake_.notify_all();
    if (worker_.joinable()) worker_.join();
    std::lock_guard<std::mutex> lock(mutex_);
    jobs_.clear();
}

void SyncClient::setServer(const std::string& address, int port) {
    std::lock_guard<std::mutex> lock(mutex_);
    address_ = address;
    port_ = port > 0 ? port : 47810;
}
void SyncClient::setToken(const std::string& token) {
    std::lock_guard<std::mutex> lock(mutex_);
    token_ = token;
}
std::string SyncClient::token() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return token_;
}
std::string SyncClient::serverAddress() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return address_;
}
int SyncClient::serverPort() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return port_;
}

void SyncClient::push(Job job) {
    {
        std::lock_guard<std::mutex> lock(mutex_);
        jobs_.push_back(std::move(job));
    }
    wake_.notify_one();
}

void SyncClient::deliver(SyncResult result) {
    std::lock_guard<std::mutex> lock(mutex_);
    // Keep the queue bounded: a UI that stops polling must not grow memory
    // without limit.
    if (results_.size() > 64) results_.pop_front();
    results_.push_back(std::move(result));
}

bool SyncClient::poll(SyncResult* out) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (results_.empty()) return false;
    if (out) *out = std::move(results_.front());
    results_.pop_front();
    return true;
}

std::string SyncClient::statusText() const {
    switch (state_.load()) {
        case SyncState::Offline: return "offline";
        case SyncState::Discovering: return "searching for desktop";
        case SyncState::Connecting: return "connecting";
        case SyncState::Unpaired: return "pairing required";
        case SyncState::Ready: return "connected";
        case SyncState::Busy: return "transferring";
        default: break;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    return lastError_.empty() ? "error" : lastError_;
}

void SyncClient::discover(int timeoutMs) { push({SyncJobKind::Discover, "", "", {}, timeoutMs}); }
void SyncClient::hello() { push({SyncJobKind::Hello, "", "", {}, 0}); }
void SyncClient::pair(const std::string& code, const std::string& deviceName) {
    json::Value body = json::Value::makeObject();
    body.set("code", json::Value(code));
    body.set("device", json::Value(deviceName));
    push({SyncJobKind::Pair, code, body.dump(), {}, 0});
}
void SyncClient::requestDocuments() { push({SyncJobKind::Documents, "", "", {}, 0}); }
void SyncClient::requestScene(const std::string& doc, int lod) {
    push({SyncJobKind::Scene, doc, "", {}, lod});
}
void SyncClient::requestSceneHash(const std::string& doc) {
    push({SyncJobKind::SceneHash, doc, "", {}, 0});
}
void SyncClient::requestEnvironments() { push({SyncJobKind::Environments, "", "", {}, 0}); }
void SyncClient::requestEnvironment(const std::string& id) {
    push({SyncJobKind::Environment, id, "", {}, 0});
}
void SyncClient::requestThumbnail(const std::string& doc) {
    push({SyncJobKind::Thumbnail, doc, "", {}, 0});
}
void SyncClient::requestState() { push({SyncJobKind::State, "", "", {}, 0}); }

void SyncClient::startEvents(int64_t since) {
    lastEventSeq_.store(since);
    if (eventsActive_.exchange(true)) return;
    push({SyncJobKind::Events, "", "", {}, int(since)});
}
void SyncClient::stopEvents() { eventsActive_.store(false); }

void SyncClient::uploadPaint(std::vector<uint8_t> fcxr) {
    push({SyncJobKind::UploadPaint, "", "", std::move(fcxr), 0});
}
void SyncClient::uploadVector(const std::string& jsonText) {
    push({SyncJobKind::UploadVector, "", jsonText, {}, 0});
}

void SyncClient::workerMain() {
    LOGI("sync worker started");
    while (running_.load()) {
        Job job;
        {
            std::unique_lock<std::mutex> lock(mutex_);
            wake_.wait(lock, [this] { return !jobs_.empty() || !running_.load(); });
            if (!running_.load()) break;
            job = std::move(jobs_.front());
            jobs_.pop_front();
        }
        runJob(job);
    }
    LOGI("sync worker stopped");
}

// -------------------------------------------------------------- HTTP client

int SyncClient::request(const char* method, const std::string& path,
                        const std::string& contentType, const uint8_t* body, size_t bodySize,
                        std::vector<uint8_t>* response, std::string* error, int timeoutMs,
                        bool trackProgress) {
    std::string host;
    int port;
    std::string token;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        host = address_;
        port = port_;
        token = token_;
    }
    if (host.empty()) {
        if (error) *error = "no server configured";
        return 0;
    }

    const int fd = connectTo(host, port, timeoutMs, error);
    if (fd < 0) return 0;

    std::string header;
    header.reserve(320);
    header += method;
    header += ' ';
    header += path;
    header += " HTTP/1.1\r\nHost: ";
    header += host;
    header += ':';
    header += std::to_string(port);
    header += "\r\nUser-Agent: FreeCAD-XR-Quest/1.0\r\nAccept: */*\r\nConnection: close\r\n";
    if (!token.empty()) {
        header += "Authorization: Bearer ";
        header += token;
        header += "\r\n";
    }
    if (bodySize) {
        header += "Content-Type: ";
        header += contentType;
        header += "\r\nContent-Length: ";
        header += std::to_string(bodySize);
        header += "\r\n";
    }
    header += "\r\n";

    if (!sendAll(fd, reinterpret_cast<const uint8_t*>(header.data()), header.size()) ||
        (bodySize && !sendAll(fd, body, bodySize))) {
        ::close(fd);
        if (error) *error = "connection closed while sending";
        return 0;
    }

    // ---- read the response ------------------------------------------------
    std::vector<uint8_t> buffer;
    buffer.reserve(64 * 1024);
    uint8_t chunk[16 * 1024];
    size_t headerEnd = std::string::npos;
    int status = 0;
    long long contentLength = -1;
    bool chunked = false;
    size_t bodyStart = 0;

    auto findHeaderEnd = [&]() -> size_t {
        if (buffer.size() < 4) return std::string::npos;
        for (size_t i = 0; i + 3 < buffer.size(); ++i) {
            if (buffer[i] == '\r' && buffer[i + 1] == '\n' && buffer[i + 2] == '\r' &&
                buffer[i + 3] == '\n')
                return i + 4;
        }
        return std::string::npos;
    };

    for (;;) {
        const ssize_t n = ::recv(fd, chunk, sizeof(chunk), 0);
        if (n < 0) {
            if (errno == EINTR) continue;
            ::close(fd);
            if (error) *error = "timed out waiting for the desktop";
            return 0;
        }
        if (n == 0) break;  // server closed
        if (buffer.size() + size_t(n) > kMaxResponse) {
            ::close(fd);
            if (error) *error = "response too large";
            return 0;
        }
        buffer.insert(buffer.end(), chunk, chunk + n);

        if (headerEnd == std::string::npos) {
            headerEnd = findHeaderEnd();
            if (headerEnd != std::string::npos) {
                const std::string head(reinterpret_cast<const char*>(buffer.data()), headerEnd);
                // status line
                const size_t sp1 = head.find(' ');
                if (sp1 != std::string::npos) status = std::atoi(head.c_str() + sp1 + 1);
                // headers
                size_t line = head.find("\r\n");
                while (line != std::string::npos) {
                    const size_t next = head.find("\r\n", line + 2);
                    if (next == std::string::npos || next == line + 2) break;
                    const std::string field = head.substr(line + 2, next - line - 2);
                    const size_t colon = field.find(':');
                    if (colon != std::string::npos) {
                        const std::string name = lowerCase(trim(field.substr(0, colon)));
                        const std::string value = trim(field.substr(colon + 1));
                        if (name == "content-length") contentLength = std::atoll(value.c_str());
                        else if (name == "transfer-encoding")
                            chunked = lowerCase(value).find("chunked") != std::string::npos;
                    }
                    line = next;
                }
                bodyStart = headerEnd;
            }
        }
        if (headerEnd != std::string::npos && trackProgress && contentLength > 0) {
            const double have = double(buffer.size() - bodyStart);
            progress_.store(float(have / double(contentLength)));
        }
        if (headerEnd != std::string::npos && !chunked && contentLength >= 0 &&
            buffer.size() - bodyStart >= size_t(contentLength))
            break;
    }
    ::close(fd);

    if (headerEnd == std::string::npos) {
        if (error) *error = "malformed response";
        return 0;
    }

    std::vector<uint8_t> payload(buffer.begin() + long(bodyStart), buffer.end());
    if (chunked) {
        // Decode `size CRLF data CRLF ...` until a zero sized chunk.
        std::vector<uint8_t> decoded;
        size_t i = 0;
        while (i < payload.size()) {
            size_t lineEnd = i;
            while (lineEnd + 1 < payload.size() &&
                   !(payload[lineEnd] == '\r' && payload[lineEnd + 1] == '\n'))
                ++lineEnd;
            if (lineEnd + 1 >= payload.size()) break;
            const std::string sizeText(reinterpret_cast<const char*>(&payload[i]), lineEnd - i);
            const long long size = std::strtoll(sizeText.c_str(), nullptr, 16);
            i = lineEnd + 2;
            if (size <= 0) break;
            if (i + size_t(size) > payload.size()) break;
            decoded.insert(decoded.end(), payload.begin() + long(i),
                           payload.begin() + long(i + size_t(size)));
            i += size_t(size) + 2;  // skip the trailing CRLF
        }
        payload.swap(decoded);
    } else if (contentLength >= 0 && payload.size() > size_t(contentLength)) {
        payload.resize(size_t(contentLength));
    }

    if (response) *response = std::move(payload);
    return status;
}

// ------------------------------------------------------------------- jobs

std::vector<ServerInfo> SyncClient::discoverBlocking(int timeoutMs) {
    std::vector<ServerInfo> found;
    const int fd = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return found;
    const int one = 1;
    ::setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &one, sizeof(one));
    ::setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    setTimeouts(fd, 250);

    // Bind to an ephemeral port; the server replies by unicast to it.
    struct sockaddr_in local;
    std::memset(&local, 0, sizeof(local));
    local.sin_family = AF_INET;
    local.sin_addr.s_addr = htonl(INADDR_ANY);
    local.sin_port = 0;
    ::bind(fd, reinterpret_cast<struct sockaddr*>(&local), sizeof(local));

    struct sockaddr_in target;
    std::memset(&target, 0, sizeof(target));
    target.sin_family = AF_INET;
    target.sin_port = htons(kDiscoveryPort);
    target.sin_addr.s_addr = htonl(INADDR_BROADCAST);

    static const char kRequest[] = "FCXR-DISCOVER?v=1";
    ::sendto(fd, kRequest, sizeof(kRequest) - 1, 0,
             reinterpret_cast<struct sockaddr*>(&target), sizeof(target));

    const int deadlineMs = timeoutMs > 0 ? timeoutMs : 700;
    int waited = 0;
    char reply[512];
    while (waited < deadlineMs) {
        struct sockaddr_in from;
        socklen_t fromLength = sizeof(from);
        const ssize_t n = ::recvfrom(fd, reply, sizeof(reply) - 1, 0,
                                     reinterpret_cast<struct sockaddr*>(&from), &fromLength);
        if (n <= 0) {
            waited += 250;
            continue;
        }
        reply[n] = '\0';
        // "FCXR-OFFER v=1 name=<host> port=47810 id=<uuid>"
        if (std::strncmp(reply, "FCXR-OFFER", 10) != 0) continue;
        ServerInfo info;
        char address[INET_ADDRSTRLEN] = {0};
        ::inet_ntop(AF_INET, &from.sin_addr, address, sizeof(address));
        info.address = address;
        for (char* token = std::strtok(reply + 10, " \t\r\n"); token;
             token = std::strtok(nullptr, " \t\r\n")) {
            const char* eq = std::strchr(token, '=');
            if (!eq) continue;
            const std::string key(token, size_t(eq - token));
            const std::string value(eq + 1);
            if (key == "port") info.port = std::atoi(value.c_str());
            else if (key == "name") info.name = value;
            else if (key == "id") info.id = value;
            else if (key == "v") info.protocolVersion = std::atoi(value.c_str());
        }
        bool duplicate = false;
        for (const ServerInfo& s : found) {
            if (s.address == info.address && s.port == info.port) duplicate = true;
        }
        if (!duplicate) {
            LOGI("discovered %s at %s:%d", info.name.c_str(), info.address.c_str(), info.port);
            found.push_back(info);
        }
    }
    ::close(fd);
    return found;
}

void SyncClient::runJob(const Job& job) {
    SyncResult result;
    result.kind = job.kind;
    result.key = job.key;
    progress_.store(0.0f);

    auto parseJsonBody = [&](const std::vector<uint8_t>& data) {
        json::ParseError err;
        result.body = json::parse(reinterpret_cast<const char*>(data.data()), data.size(), &err);
        if (!err.ok) {
            result.ok = false;
            result.error = "malformed JSON from the server: " + err.message;
        }
    };
    auto finish = [&](int status, const std::string& error) {
        result.status = status;
        if (status == 0) {
            result.ok = false;
            result.error = error;
            setState(SyncState::Failed);
        } else if (status == 401 || status == 403) {
            result.ok = false;
            result.error = "not paired with this desktop";
            setState(SyncState::Unpaired);
        } else if (status >= 400) {
            result.ok = false;
            if (result.error.empty()) {
                const std::string message = result.body["message"].asString();
                result.error = message.empty() ? ("HTTP " + std::to_string(status)) : message;
            }
            setState(SyncState::Failed);
        } else {
            if (result.error.empty()) result.ok = true;
            setState(SyncState::Ready);
        }
        {
            std::lock_guard<std::mutex> lock(mutex_);
            lastError_ = result.ok ? std::string() : result.error;
        }
        progress_.store(0.0f);
    };

    switch (job.kind) {
        case SyncJobKind::Discover: {
            setState(SyncState::Discovering);
            result.servers = discoverBlocking(job.number);
            result.ok = true;
            result.status = 200;
            setState(result.servers.empty() ? SyncState::Offline : SyncState::Connecting);
            break;
        }
        case SyncJobKind::Hello: {
            setState(SyncState::Connecting);
            std::vector<uint8_t> data;
            std::string error;
            const int status = request("GET", std::string(kApiPrefix) + "/hello", "", nullptr,
                                       0, &data, &error, kDefaultTimeoutMs, false);
            if (status == 200) parseJsonBody(data);
            finish(status, error);
            if (result.ok && result.body["auth_required"].asBool(true) &&
                token().empty())
                setState(SyncState::Unpaired);
            break;
        }
        case SyncJobKind::Pair: {
            setState(SyncState::Connecting);
            std::vector<uint8_t> data;
            std::string error;
            const int status = request(
                "POST", std::string(kApiPrefix) + "/pair", "application/json",
                reinterpret_cast<const uint8_t*>(job.text.data()), job.text.size(), &data,
                &error, kDefaultTimeoutMs, false);
            if (status == 200) {
                parseJsonBody(data);
                const std::string newToken = result.body["token"].asString();
                if (!newToken.empty()) setToken(newToken);
            }
            finish(status, error);
            break;
        }
        case SyncJobKind::Documents:
        case SyncJobKind::Environments:
        case SyncJobKind::State:
        case SyncJobKind::SceneHash:
        case SyncJobKind::Environment: {
            setState(SyncState::Busy);
            std::string path = kApiPrefix;
            if (job.kind == SyncJobKind::Documents) path += "/documents";
            else if (job.kind == SyncJobKind::Environments) path += "/environments";
            else if (job.kind == SyncJobKind::State) path += "/state";
            else if (job.kind == SyncJobKind::SceneHash)
                path += "/scene/hash?doc=" + urlEncode(job.key);
            else path += "/environment?id=" + urlEncode(job.key);
            std::vector<uint8_t> data;
            std::string error;
            const int status =
                request("GET", path, "", nullptr, 0, &data, &error, kDefaultTimeoutMs, false);
            if (status >= 200 && status < 300) parseJsonBody(data);
            else if (!data.empty()) parseJsonBody(data);
            finish(status, error);
            break;
        }
        case SyncJobKind::Scene: {
            setState(SyncState::Busy);
            const std::string path = std::string(kApiPrefix) + "/scene?doc=" +
                                     urlEncode(job.key) + "&lod=" + std::to_string(job.number);
            std::string error;
            std::vector<uint8_t> data;
            const int status =
                request("GET", path, "", nullptr, 0, &data, &error, 60000, true);
            if (status == 200) {
                if (data.size() < 12 || std::memcmp(data.data(), "FCXR", 4) != 0) {
                    result.error = "the server did not return an FCXR package";
                    result.ok = false;
                } else {
                    result.data = std::move(data);
                }
            } else if (!data.empty()) {
                parseJsonBody(data);
            }
            finish(status, error);
            break;
        }
        case SyncJobKind::Thumbnail: {
            const std::string path =
                std::string(kApiPrefix) + "/thumbnail?doc=" + urlEncode(job.key);
            std::string error;
            std::vector<uint8_t> data;
            const int status =
                request("GET", path, "", nullptr, 0, &data, &error, kDefaultTimeoutMs, false);
            if (status == 200) result.data = std::move(data);
            finish(status, error);
            break;
        }
        case SyncJobKind::Events: {
            const int64_t since = lastEventSeq_.load();
            const std::string path =
                std::string(kApiPrefix) + "/events?since=" + std::to_string(since);
            std::vector<uint8_t> data;
            std::string error;
            const int status =
                request("GET", path, "", nullptr, 0, &data, &error, kEventTimeoutMs, false);
            if (status == 200) {
                parseJsonBody(data);
                const int64_t last = result.body["last_seq"].asInt64(since);
                if (last > since) lastEventSeq_.store(last);
                const json::Value& events = result.body["events"];
                for (size_t i = 0; i < events.size(); ++i) {
                    const int64_t seq = events[i]["seq"].asInt64(0);
                    if (seq > lastEventSeq_.load()) lastEventSeq_.store(seq);
                }
                // Only surface a result when something actually happened, so
                // the UI is not woken by every keep-alive.
                if (events.size() == 0) {
                    if (eventsActive_.load() && running_.load())
                        push({SyncJobKind::Events, "", "", {}, 0});
                    return;
                }
            }
            finish(status, error);
            // Re-arm unless the poll failed hard (a failed poll would spin).
            if (eventsActive_.load() && running_.load() && status != 0 && status != 401 &&
                status != 403)
                push({SyncJobKind::Events, "", "", {}, 0});
            else
                eventsActive_.store(false);
            break;
        }
        case SyncJobKind::UploadPaint: {
            setState(SyncState::Busy);
            std::vector<uint8_t> data;
            std::string error;
            const int status = request("POST", std::string(kApiPrefix) + "/paint",
                                       "application/x-fcxr", job.data.data(), job.data.size(),
                                       &data, &error, 30000, true);
            if (!data.empty()) parseJsonBody(data);
            finish(status, error);
            break;
        }
        case SyncJobKind::UploadVector: {
            setState(SyncState::Busy);
            std::vector<uint8_t> data;
            std::string error;
            const int status =
                request("POST", std::string(kApiPrefix) + "/vector", "application/json",
                        reinterpret_cast<const uint8_t*>(job.text.data()), job.text.size(),
                        &data, &error, 30000, false);
            if (!data.empty()) parseJsonBody(data);
            finish(status, error);
            break;
        }
    }
    deliver(std::move(result));
}

}  // namespace fcxr
