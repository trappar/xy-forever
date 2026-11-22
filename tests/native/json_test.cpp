// tier: gate
// native/json.hpp under the sanitizers: every prefix of each shared fixture (tests/native-json.json), as a
// package and a configuration, and each byte of it with its top bit flipped, never reads out of bounds nor
// changes the source.
#include <cstdio>
#include <cstring>
#include <memory>
#include <string>
#include <vector>
#include "doctest.h"
#include "json.hpp"

namespace {

namespace j = xyf_core::json;

void view(const char* raw, unsigned length, bool config) {
    static j::Workspace workspace;
    static char out[4096];
    const j::Document d = j::parse(raw, length, workspace, config ? j::config_schema : j::package_schema);
    uint32_t bytes = 0;
    if (config) j::config_text(d, out, sizeof out, bytes);
    else j::package_text(d, out, sizeof out, bytes);
}

// The "source" strings of the fixture file (JSON string escapes decoded; \u only below 0x80, as the file uses).
std::vector<std::string> fixture_sources() {
    std::string path = __FILE__;
    path = path.substr(0, path.rfind('/')) + "/../native-json.json";
    std::string text;
    if (FILE* f = std::fopen(path.c_str(), "rb")) {
        char chunk[4096];
        for (size_t n; (n = std::fread(chunk, 1, sizeof chunk, f)) > 0;) text.append(chunk, n);
        std::fclose(f);
    }
    std::vector<std::string> sources;
    const std::string key = "\"source\": \"";
    for (size_t at = text.find(key); at != std::string::npos; at = text.find(key, at)) {
        std::string value;
        for (at += key.size(); at < text.size() && text[at] != '"'; ++at) {
            char c = text[at];
            if (c == '\\') {
                c = text[++at];
                if (c == 'n') c = '\n';
                else if (c == 't') c = '\t';
                else if (c == 'u') {
                    const unsigned code = static_cast<unsigned>(std::stoul(text.substr(at + 1, 4), nullptr, 16));
                    REQUIRE(code < 0x80);
                    c = static_cast<char>(code);
                    at += 4;
                }
            }
            value += c;
        }
        sources.push_back(value);
    }
    return sources;
}

}  // namespace

TEST_CASE("json: truncated and mutated fixtures stay in bounds and leave the source unchanged") {
    const std::vector<std::string> sources = fixture_sources();
    REQUIRE(sources.size() >= 20);
    for (const std::string& source : sources) {
        CAPTURE(source);
        REQUIRE(source.size() <= j::document_bytes);
        std::vector<char> raw(source.begin(), source.end());
        const unsigned bytes = static_cast<unsigned>(raw.size());
        for (unsigned n = 0; n <= bytes; ++n) {
            // Each prefix in a buffer of exactly its size, so ASan sees a read past it.
            std::unique_ptr<char[]> prefix(new char[n ? n : 1]);
            std::memcpy(prefix.get(), raw.data(), n);
            for (int config = 0; config != 2; ++config) view(prefix.get(), n, config != 0);
            CHECK(std::memcmp(prefix.get(), raw.data(), n) == 0);
        }
        for (unsigned n = 0; n != bytes; ++n) {
            raw[n] ^= char(0x80);
            view(raw.data(), bytes, false);
            raw[n] ^= char(0x80);
            CHECK(std::memcmp(raw.data(), source.data(), bytes) == 0);
        }
    }
}
