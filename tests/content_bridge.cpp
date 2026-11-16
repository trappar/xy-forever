#include "../native/content.hpp"
extern "C" int content_run(const xyf_core::package::Item* desired,unsigned int count,
    const xyf_core::package::Item* previous,unsigned int old_count,bool remove_old,
    const xyf_core::content::IO* io,xyf_core::content::Report* report) {
    xyf_core::package::Manifest manifest={};manifest.items=desired;manifest.count=count;
    xyf_core::content::Operation operations[64];xyf_core::content::Plan plan;
    *report={0,0,0};
    const xyf_core::content::Status status=xyf_core::content::plan(manifest,previous,old_count,
        remove_old,*io,operations,64,plan);
    if (status!=xyf_core::content::Status::ok) return static_cast<int>(status);
    return static_cast<int>(xyf_core::content::apply(plan,*io,*report));
}
