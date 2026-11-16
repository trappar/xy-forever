#include "aes.hpp"
namespace xyf_core { namespace crypto {
namespace {
Byte twice(Byte a) { return Byte((U32(a)<<1)^((a&128)?0x11b:0)); }
Byte multiply(Byte a,Byte b) {
    Byte result=0;
    for (U32 i=0;i<8;++i) {if (b&1) result^=a;a=twice(a);b>>=1;}
    return result;
}
Byte rotate(Byte a,U32 n) { return Byte((U32(a)<<n)|(U32(a)>>(8-n))); }
Byte substitution(Byte value) {
    Byte inverse=0;
    if (value) {
        Byte factor=value;inverse=1;
        for (U32 exponent=254;exponent;exponent>>=1) {
            if (exponent&1) inverse=multiply(inverse,factor);
            factor=multiply(factor,factor);
        }
    }
    return Byte(inverse^rotate(inverse,1)^rotate(inverse,2)^rotate(inverse,3)^rotate(inverse,4)^0x63);
}
void add(Byte* state,const Byte* key) {for (U32 i=0;i<16;++i) state[i]^=key[i];}
void shift(Byte* state) {
    Byte previous[16];for (U32 i=0;i<16;++i) previous[i]=state[i];
    for (U32 row=0;row<4;++row)
        for (U32 column=0;column<4;++column) state[row+4*column]=previous[row+4*((column+4-row)&3)];
}
void mix(Byte* state) {
    for (U32 column=0;column<4;++column) {
        Byte a[4],nine[4],eleven[4],thirteen[4],fourteen[4];
        for (U32 row=0;row<4;++row) {
            a[row]=state[4*column+row];
            const Byte two=twice(a[row]),four=twice(two),eight=twice(four);
            nine[row]=Byte(eight^a[row]);eleven[row]=Byte(eight^two^a[row]);
            thirteen[row]=Byte(eight^four^a[row]);fourteen[row]=Byte(eight^four^two);
        }
        state[4*column]=Byte(fourteen[0]^eleven[1]^thirteen[2]^nine[3]);
        state[4*column+1]=Byte(nine[0]^fourteen[1]^eleven[2]^thirteen[3]);
        state[4*column+2]=Byte(thirteen[0]^nine[1]^fourteen[2]^eleven[3]);
        state[4*column+3]=Byte(eleven[0]^thirteen[1]^nine[2]^fourteen[3]);
    }
}
}
Aes256::Aes256():ready_(false) {}
Aes256::~Aes256() {erase();}
void Aes256::erase() {
    volatile Byte* key=schedule_;
    for (U32 i=0;i<240;++i) key[i]=0;
    ready_=false;
}
bool Aes256::initialize(const Byte* key) {
    erase();if (!key) return false;
    Byte forward[256];
    for (U32 i=0;i<256;++i) {forward[i]=substitution(Byte(i));inverse_[forward[i]]=Byte(i);}
    for (U32 i=0;i<32;++i) schedule_[i]=key[i];
    U32 size=32;Byte round=1;
    while (size<240) {
        Byte temp[4];for (U32 i=0;i<4;++i) temp[i]=schedule_[size-4+i];
        if (!(size&31)) {
            const Byte first=temp[0];temp[0]=forward[temp[1]]^round;
            temp[1]=forward[temp[2]];temp[2]=forward[temp[3]];temp[3]=forward[first];round=twice(round);
        } else if ((size&31)==16) for (U32 i=0;i<4;++i) temp[i]=forward[temp[i]];
        for (U32 i=0;i<4;++i) {schedule_[size]=schedule_[size-32]^temp[i];++size;}
        volatile Byte* wipe=temp;for (U32 i=0;i<4;++i) wipe[i]=0;
    }
    ready_=true;return true;
}
bool Aes256::decrypt(const Byte* input,Byte* output) const {
    if (!ready_ || !input || !output) return false;
    Byte state[16];for (U32 i=0;i<16;++i) state[i]=input[i];
    add(state,schedule_+224);
    for (U32 round=13;round;--round) {
        shift(state);for (U32 i=0;i<16;++i) state[i]=inverse_[state[i]];
        add(state,schedule_+16*round);mix(state);
    }
    shift(state);for (U32 i=0;i<16;++i) state[i]=inverse_[state[i]];
    add(state,schedule_);for (U32 i=0;i<16;++i) output[i]=state[i];
    volatile Byte* wipe=state;for (U32 i=0;i<16;++i) wipe[i]=0;
    return true;
}
} }
