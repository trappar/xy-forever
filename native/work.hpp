#ifndef XYF_WORK_HPP
#define XYF_WORK_HPP

namespace xyf_core {
// Optional checkpoints let the current owner service transport and cancel work.
// The callback must not reenter the operation or modify its input or workspace.
// A false answer is terminal for this Work instance.
class Work {
public:
    Work(void* context, bool (*keep_going)(void*)) : context_(context), keep_going_(keep_going), cancelled_(false) {}
    __attribute__((always_inline)) bool checkpoint() {
        if (!cancelled_ && keep_going_) cancelled_ = !keep_going_(context_);
        return !cancelled_;
    }
    bool cancelled() const { return cancelled_; }

private:
    void* context_;
    bool (*keep_going_)(void*);
    bool cancelled_;
};
// Keep the callback load beside the indirect call for the Blackfin call audit.
// Local linkage keeps callers of this thunk from becoming register calls too.
namespace work_detail {
static __attribute__((noinline, unused)) bool call(Work& work) { return work.checkpoint(); }
}
static inline bool checkpoint(Work* work) { return !work || work_detail::call(*work); }
inline bool cancelled(const Work* work) { return work && work->cancelled(); }
}

#endif
