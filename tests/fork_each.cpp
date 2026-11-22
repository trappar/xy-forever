// Many runs of a sanitized host binary in one process start (tests/fork_each.py drives it; hosttest/CMakeLists.txt builds it in).
//
// The binary's own main is renamed by the build (-Dmain=fork_each_main) and this file supplies the real one:
//   BINARY RUNS   where RUNS holds one run a line, tab-separated: STDIN STDOUT STDERR ARG...
// Each run is a forked child that starts from the parent's untouched state (as a fresh process would) with its
// streams on those files and argv = BINARY ARG...; its exit status (128 + the signal when one ended it) is
// printed one a line. The sanitizer runtime starts once, in the parent: on CI's macOS VMs a start costs about
// 0.2 s of CPU, a fork far less.
#undef main
#include <errno.h>
#include <sys/wait.h>
#include <unistd.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

int fork_each_main(int argc, char** argv);

// No leak check in the runs: LeakSanitizer (on by default on Linux, off on macOS) stops the world in every forked
// child's exit, which hangs there. The runs check memory errors and undefined behaviour, never leaks.
extern "C" const char* __asan_default_options() { return "detect_leaks=0"; }

namespace {
std::vector<std::string> fields(const std::string& line) {
    std::vector<std::string> out(1);
    for (char c : line)
        if (c == '\t') out.emplace_back();
        else out.back() += c;
    return out;
}

int run(char* self, const std::vector<std::string>& each) {
    std::fflush(nullptr);
    const pid_t pid = fork();
    if (pid < 0) return 127;
    if (pid == 0) {
        if (!std::freopen(each[0].c_str(), "rb", stdin) || !std::freopen(each[1].c_str(), "wb", stdout) ||
            !std::freopen(each[2].c_str(), "wb", stderr))
            _exit(127);
        std::vector<char*> argv{self};
        for (size_t i = 3; i < each.size(); ++i) argv.push_back(const_cast<char*>(each[i].c_str()));
        argv.push_back(nullptr);
        std::exit(fork_each_main(int(argv.size() - 1), argv.data()));
    }
    int status = 0;
    while (waitpid(pid, &status, 0) < 0)
        if (errno != EINTR) return 127;
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128 + WTERMSIG(status);
}
} // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::FILE* runs = std::fopen(argv[1], "rb");
    if (!runs) return 2;
    std::string line;
    for (int c; (c = std::fgetc(runs)) != EOF;) {
        if (c != '\n') {
            line += char(c);
            continue;
        }
        const std::vector<std::string> each = fields(line);
        line.clear();
        if (each.size() < 3) return 2;
        std::printf("%d\n", run(argv[0], each));
    }
    std::fclose(runs);
    return 0;
}
