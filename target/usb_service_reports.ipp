// Required package/state/space reports. Package hashes are streamed one IO per menu pass.
// The completed-job workspace is reusable; last job.status and MTP's slot remain intact.
struct PackageReport {
    ContentIo io;
    xyf::manifest::Manifest manifest;
    xyf::package_record::Workspace record;
    xyf_core::hash::Sha256 sha;
    U32 seq, area, outer, inner, stage, folder, valid, n, bytes, prefix_n, text_n, have_identity, single, legacy;
    int fd;
    char path[240], parent[240], id[256], name[256], text[980];
    uint8_t buffer[xyf_core::json::document_bytes], prefix[268], identity[32];
};
static_assert(sizeof(PackageReport) <= 0x10000u, "report workspace fits below the reserved MTP slot");
PackageReport& report_slot() { return *reinterpret_cast<PackageReport*>(job_work_end - 0x10000u); }
void report_end(U32 code, bool more = true) {
    PackageReport& r = report_slot();
    if (r.fd >= 0 && !r.io.lost()) r.io.close(r.fd);
    r.fd = -1;
    uint8_t* p = files; P::put32(p, code);
    if (code == P::s_ok) {
        const U32 values[4] = {1,r.outer,r.inner,more ? 1u : 0u};
        for (U32 i=0;i!=4;++i)P::put32(p+4+4*i,values[i]);
        for (U32 i=0;i!=r.text_n;++i)p[20+i]=uint8_t(r.text[i]);
    }
    reporting=0;result.last_status=code;send(P::t_package_list,uint8_t(r.seq),p,code==P::s_ok?20+r.text_n:4);
}
bool report_field(PackageReport& r,const char* key,const char* value) {
    U32 need=2;for(const char* p=key;*p;++p)++need;for(const char* p=value;*p;++p)++need;
    if(need>sizeof(r.text)-r.text_n)return false;
    for(;*key;++key)r.text[r.text_n++]=*key;
    r.text[r.text_n++]='=';
    for(;*value;++value)r.text[r.text_n++]=*value;
    r.text[r.text_n++]='\n';return true;
}
void report_finish_item() {
    PackageReport& r=report_slot();char h[65];
    const char* type="unknown";
    if(r.folder) {
        type=r.manifest.type==xyf::manifest::type_mod_base?"mod-base":r.manifest.type==xyf::manifest::type_mod?"mod":
             r.manifest.type==xyf::manifest::type_companion?"companion":"firmware";
    } else {
        xyf::manifest::Recognition found;
        xyf::manifest::recognise_file(r.name,r.bytes,r.prefix,r.prefix_n,found);
        type=found.kind==xyf::manifest::kind_official_tfw?"tfw":found.kind==xyf::manifest::kind_ldr_image?"firmware":
             found.kind==xyf::manifest::kind_xyf_stage2?"stage2":found.kind==xyf::manifest::kind_zip?"zip":"unknown";
    }
    r.text_n=0;bool ok=report_field(r,"id",r.id)&&report_field(r,"type",type)&&report_field(r,"version",r.manifest.version);
    if(r.have_identity)xj::hex(h,r.identity,32);else h[0]=0;
    ok=ok&&report_field(r,"identity",h);
    if(r.single) {ok=ok&&report_field(r,"sha256",h);} else {xj::hex(h,r.manifest.sha256,32);ok=ok&&report_field(r,"sha256",h);}
    xj::hex(h,r.manifest.needs_os,32);ok=ok&&report_field(r,"needs_os",h)&&report_field(r,"generation",r.manifest.generation)&&
       report_field(r,"profile",r.manifest.profile)&&report_field(r,"valid",r.valid?"1":"0");
    report_end(ok?P::s_ok:P::s_too_large);
}
void package_list(uint8_t seq,const uint8_t* payload,U32 length) {
    U32 code=length!=16?P::s_bad_request:P::get32(payload)>1?P::s_unsupported:
             P::get32(payload)!=1||P::get32(payload+4)>2?P::s_bad_request:P::s_ok;
    if(!code && ((job_running()&&!package_active)||reporting||closing||recovery_pending XYF_OR_MTP_BUSY))code=P::s_busy;
    if(!code && !a_serving())code=P::s_link;
    if(!code)code=file_client::mount_ready(mounted);
    if(code){status_only(P::t_package_list,seq,code);return;}
    PackageReport& r=report_slot();for(U32 i=0;i!=sizeof(r);++i)reinterpret_cast<uint8_t*>(&r)[i]=0;
    r.fd=-1;r.seq=seq;r.area=P::get32(payload+4);r.outer=P::get32(payload+8);r.inner=P::get32(payload+12);reporting=1;
}
void report_step() {
    if(!reporting)return;
    PackageReport& r=report_slot();
    if(r.stage==0) {
        static const char* const roots[3]={"/yaffs2/xy-forever/inbox","/yaffs2/xy-forever/firmware","/yaffs2/xy-forever/mods"};
        uint32_t kind=0;const int found=r.io.list_one(roots[r.area],r.outer,r.name,kind);
        if(found<0){report_end(r.io.lost()?P::s_link:P::s_storage);return;}if(!found){report_end(P::s_ok,false);return;}
        if(!xyf::manifest::valid_name(r.name)){++r.outer;return;}
        if(!join(r.parent,roots[r.area],"/",r.name)){report_end(P::s_too_large);return;}
        if(r.area==2){if(kind!=1){++r.outer;return;}xj::copy(r.id,r.name);r.stage=1;return;}
        xj::copy(r.id,r.name);r.folder=kind==1;r.single=!r.folder;++r.outer;r.stage=r.folder?2:4;xj::copy(r.path,r.parent);return;
    }
    if(r.stage==1) {
        uint32_t kind=0;const int found=r.io.list_one(r.parent,r.inner,r.name,kind);
        if(found<0){report_end(r.io.lost()?P::s_link:P::s_storage);return;}if(!found){++r.outer;r.inner=0;r.stage=0;return;}
        ++r.inner;if(kind!=1||!xyf::manifest::valid_name(r.name))return;
        if(!join(r.path,r.parent,"/",r.name)){report_end(P::s_too_large);return;}
        xj::copy(r.parent,r.path);U32 n=text_bytes(r.id);if(n+1+text_bytes(r.name)>=sizeof(r.id)){report_end(P::s_too_large);return;}
        r.id[n++]='/';xj::copy(r.id+n,r.name);r.folder=1;r.stage=2;return;
    }
    if(r.stage==2) {
        if(!join(r.path,r.parent,r.legacy?"/info.txt":"/manifest.json")){report_end(P::s_too_large);return;}
        r.fd=r.io.open(r.path,false);
        if(r.fd==-2){if(!r.legacy){r.legacy=1;return;}r.stage=3;return;}
        if(r.fd<0){report_end(r.io.lost()?P::s_link:P::s_storage);return;}
        r.n=0;r.stage=6;return;
    }
    if(r.stage==6) {
        const U32 cap=r.legacy?xyf::store::max_text:xyf_core::json::document_bytes;
        uint8_t extra;const U32 want=cap-r.n<1024?cap-r.n:1024;
        const int n=r.io.read(r.fd,want?r.buffer+r.n:&extra,want?want:1);
        if(n<0||uint32_t(n)>want){report_end(r.io.lost()?P::s_link:P::s_too_large);return;}
        if(n){r.n+=uint32_t(n);return;}r.stage=7;return;
    }
    if(r.stage==7) {
        if(r.io.close(r.fd)){r.fd=-1;report_end(P::s_storage);return;}r.fd=-1;
        if(r.legacy) {
            r.valid=xyf::manifest::parse(reinterpret_cast<const char*>(r.buffer),r.n,r.manifest)==xyf::manifest::ok;
            if(!r.valid){xyf::store::Info info={};xyf::store::parse_info(reinterpret_cast<const char*>(r.buffer),r.n,info);xj::copy(r.manifest.version,info.version);}
        } else {
            r.valid=xyf::package_record::parse(reinterpret_cast<const char*>(r.buffer),r.n,r.record)==xyf_core::json::ok &&
                    r.record.manifest.status==xyf::manifest::ok;
            const uint8_t* from=reinterpret_cast<const uint8_t*>(&r.record.manifest);
            for(U32 i=0;i!=sizeof(r.manifest);++i)reinterpret_cast<uint8_t*>(&r.manifest)[i]=from[i];
        }
        r.stage=3;return;
    }
    if(r.stage==3) {
        if(!join(r.path,r.parent,"/files.sha256")){report_end(P::s_too_large);return;}
        r.fd=r.io.open(r.path,false);
        if(r.fd==-2){r.single=1;if(!join(r.path,r.parent,"/os.ldr")){report_end(P::s_too_large);return;}r.stage=4;return;}
        if(r.fd<0){report_end(r.io.lost()?P::s_link:P::s_storage);return;}r.sha.reset();r.bytes=0;r.stage=5;return;
    }
    if(r.stage==4) {
        r.fd=r.io.open(r.path,false);if(r.fd==-2 && r.folder){r.valid=0;report_finish_item();return;}
        if(r.fd<0){report_end(r.fd==-2?P::s_not_found:r.io.lost()?P::s_link:P::s_storage);return;}
        r.sha.reset();r.bytes=0;r.stage=5;return;
    }
    if(r.stage==5) {
        const int n=r.io.read(r.fd,r.buffer,1024);if(n<0||n>1024){report_end(r.io.lost()?P::s_link:P::s_storage);return;}
        if(n){if(r.bytes>0xffffffffu-uint32_t(n)){report_end(P::s_too_large);return;}r.bytes+=uint32_t(n);r.sha.update(r.buffer,uint32_t(n));
            for(int i=0;i!=n && r.prefix_n<sizeof(r.prefix);++i)r.prefix[r.prefix_n++]=r.buffer[i];
            return;}
        r.stage=8;return;
    }
    if(r.io.close(r.fd)){r.fd=-1;report_end(P::s_storage);return;}r.fd=-1;
    xyf_core::hash::Digest digest;r.sha.finish(digest);for(U32 i=0;i!=32;++i)r.identity[i]=digest.bytes[i];
    r.have_identity=1;report_finish_item();
}
void report_turn() {
    if (reporting && package_active) package_ms = now(); // this owner is still reading a package
    const U32 began = now();
    do report_step(); while (reporting && report_slot().stage == 5 && now() - began < job_slice_ms);
}
void state_get(uint8_t seq,const uint8_t* payload,U32 length) {
    U32 code=length!=12?P::s_bad_request:P::get32(payload)>1?P::s_unsupported:
             P::get32(payload)!=1||P::get32(payload+4)>4?P::s_bad_request:P::s_ok;
    if(!code && P::get32(payload+8)>0x7fffffffu)code=P::s_range;
    if(!code && ((job_running()&&!package_active)||reporting||closing||recovery_pending XYF_OR_MTP_BUSY))code=P::s_busy;
    if(!code && !a_serving())code=P::s_link;
    if(code){status_only(P::t_state_get,seq,code);return;}
    const U32 which=P::get32(payload+4),offset=P::get32(payload+8);uint32_t bytes=0;
    static const char* const paths[5]={"/yaffs2/xy-forever/state-a.txt","/yaffs2/xy-forever/state-b.txt","/yaffs2/xy-forever/state.txt",xc::record_path,xj::path};
    code=file_client::read_file(paths[which],offset,true,files+20,1004,bytes,mounted);
    if(code==P::s_not_found && which>=3)code=file_client::read_file(which==3?xc::record_old:xj::old,offset,true,files+20,1004,bytes,mounted);
    P::put32(files,code);P::put32(files+4,1);P::put32(files+8,which);P::put32(files+12,offset);P::put32(files+16,bytes==1004?1:0);
    result.last_status=code;send(P::t_state_get,seq,files,code?4:20+bytes);
}
// Same unsigned product as the MTP responder; no target 64-bit runtime helper.
void storage_space_product(U32 a,U32 b,U32& hi,U32& lo) {
    const U32 al=a&0xffffu,ah=a>>16,bl=b&0xffffu,bh=b>>16;
    const U32 ll=al*bl,lh=al*bh,hl=ah*bl,hh=ah*bh;
    const U32 mid=(ll>>16)+(lh&0xffffu)+(hl&0xffffu);
    lo=(ll&0xffffu)|(mid<<16);hi=hh+(lh>>16)+(hl>>16)+(mid>>16);
}
void storage_space(uint8_t seq,const uint8_t* payload,U32 length) {
    U32 code=length!=4?P::s_bad_request:P::get32(payload)>1?P::s_unsupported:P::get32(payload)!=1?P::s_bad_request:P::s_ok;
    if(!code && ((job_running()&&!package_active)||reporting||closing||recovery_pending XYF_OR_MTP_BUSY))code=P::s_busy;
    if(!code && !a_serving())code=P::s_link;
    if(!code)code=file_client::mount_ready(mounted);
    if(!code){
        uint8_t record[file_client::space_bytes];
        const file_client::Reply r=file_client::statfs("/yaffs2",record);
        if(r.outcome)code=file_client::status(r.outcome);
        else if(r.value!=0)code=r.error==file_client::enoent?P::s_not_found:P::s_storage;
        else if(!P::get32(record))code=P::s_storage;
        else{
            U32 hi=0,lo=0;storage_space_product(P::get32(record),P::get32(record+8),hi,lo);
            P::put32(files+8,lo);P::put32(files+12,hi);
        }
        // Successful stock queries retain an earlier YAFFS errno; only the return value decides success.
    }
    P::put32(files,code);P::put32(files+4,1);result.last_status=code;send(P::t_storage_space,seq,files,code?4:16);
}
