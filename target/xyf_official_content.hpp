#ifndef XYF_OFFICIAL_CONTENT_HPP
#define XYF_OFFICIAL_CONTENT_HPP
#include <stdint.h>
#include "../native/hash.hpp"
// The official 1.2.1 content beyond its digests (usb_service.hpp), for a base's content set (#183) on an OP-XY
// another tool changed (#331). Facts about teenage engineering's files, never the files. Pure: host-tested
// (tests/test_official_content.py).
namespace xyf { namespace official {
// The content set's paths (content.sha256's, relative to /yaffs2/content/), each as the first four bytes of its
// SHA-256, big-endian, sorted. The content set's job reads only these: files another tool left beside them
// (Open-XY's presets, screens and aux presets) stay out of the set. tests/test_official_content.py checks the table
// against the private 1.2.1 manifest.
static const uint32_t path_count = 839;
static const uint32_t path_hashes[path_count] = {
    0x0021257du, 0x008afe4bu, 0x00b49fdcu, 0x00d5d88bu, 0x01589e10u, 0x01b14135u, 0x01b5ece4u, 0x01bf541du, 0x0256fcc5u,
    0x025724bbu, 0x02589318u, 0x027f5b36u, 0x02d30bf7u, 0x030fab9fu, 0x035592a8u, 0x035b9119u, 0x03642858u, 0x043f7323u,
    0x04756bacu, 0x04a2693fu, 0x05214b8cu, 0x056df3cdu, 0x05cc2d5au, 0x05f892ecu, 0x061f7b85u, 0x06578831u, 0x06615edau,
    0x06bcd887u, 0x06e5ed64u, 0x06e7a503u, 0x07504451u, 0x0772390fu, 0x07d9f5c0u, 0x07dd454fu, 0x0865ae2cu, 0x08e86b7du,
    0x092d9400u, 0x0980972au, 0x09ea254fu, 0x0aa9bf7au, 0x0aba3338u, 0x0b88f640u, 0x0b94f450u, 0x0c0bd581u, 0x0c1d3c98u,
    0x0c771fc2u, 0x0c904117u, 0x0c94e266u, 0x0cc73622u, 0x0d22cfd7u, 0x0d31b3ebu, 0x0d93601eu, 0x0d9c6f78u, 0x0e9534c4u,
    0x0ef35a4eu, 0x0f3593afu, 0x0f60163bu, 0x0f8d0007u, 0x103ce532u, 0x1073e1f4u, 0x10e067a6u, 0x113a3271u, 0x11e4e72cu,
    0x121e8314u, 0x127a74e9u, 0x129e2116u, 0x12f400d7u, 0x130f6fddu, 0x13389a37u, 0x13f8bf9fu, 0x146634cfu, 0x14f5d37cu,
    0x1573aff0u, 0x157b4c66u, 0x158ba4d5u, 0x15b3dc6bu, 0x16aee174u, 0x16bf6d96u, 0x16f2adf5u, 0x17260d4cu, 0x1770f137u,
    0x17ae5dffu, 0x17b6b989u, 0x17b73a61u, 0x1844d5f9u, 0x18b883dcu, 0x18cb35c9u, 0x18d38bbbu, 0x1902f1f1u, 0x1919fcb3u,
    0x192de97bu, 0x19373bd4u, 0x194b8f34u, 0x194d5a80u, 0x199485eau, 0x19d3d4e1u, 0x1a1bb3d5u, 0x1a7c6551u, 0x1b317c28u,
    0x1b34baa3u, 0x1b91b2cbu, 0x1bd5e00eu, 0x1c4c1697u, 0x1cfddf90u, 0x1d366282u, 0x1dfd56ceu, 0x1e07f199u, 0x1e75e495u,
    0x1e7a22cdu, 0x1e7b6d22u, 0x1e7d67fau, 0x1e802123u, 0x1f2c8f49u, 0x1f4418a8u, 0x1f724db7u, 0x1fafe6d9u, 0x1fc8688du,
    0x1fe3108fu, 0x200509eau, 0x20164ebau, 0x2041a54cu, 0x208d0397u, 0x20eed0cbu, 0x211c276cu, 0x21a1eda0u, 0x21ac34ebu,
    0x21c4d0c5u, 0x220045dbu, 0x22c4f4cbu, 0x23410393u, 0x23caaa0au, 0x247657ecu, 0x248fec98u, 0x24aaf335u, 0x24ac0670u,
    0x24d0809eu, 0x24e6f7ddu, 0x24e9d915u, 0x2503a558u, 0x255e4095u, 0x258a32ccu, 0x25e1859bu, 0x2604aec2u, 0x268c4733u,
    0x26a97bd0u, 0x26cf6384u, 0x26e9e904u, 0x2705a757u, 0x27a1f59cu, 0x27caa36du, 0x287451d1u, 0x28bfa56eu, 0x28d8c27du,
    0x29047cd8u, 0x29b81d9bu, 0x2a8e4a9eu, 0x2a991750u, 0x2ac39450u, 0x2ad52409u, 0x2b3e31f8u, 0x2b4cc1e3u, 0x2bce0d66u,
    0x2be81f15u, 0x2c6ffcdbu, 0x2e40b8d2u, 0x2e4e7149u, 0x2e7f5a05u, 0x2f2cecf3u, 0x2f6afe32u, 0x2fc12f67u, 0x2ffdcb32u,
    0x3005864fu, 0x304dfce4u, 0x30a0ccd7u, 0x30b42495u, 0x31007895u, 0x31d3a2a0u, 0x3236d8b3u, 0x3250db4bu, 0x32efe62bu,
    0x331fb2fdu, 0x33692358u, 0x337121a9u, 0x33c767a3u, 0x33cec806u, 0x33dc31c7u, 0x3403d16au, 0x34042e2du, 0x34202944u,
    0x34b0443au, 0x34e34a82u, 0x35068779u, 0x3557a862u, 0x35939779u, 0x359d883bu, 0x35cfd7ccu, 0x362026b0u, 0x36a5040du,
    0x36e8e232u, 0x37058b0fu, 0x3732c17au, 0x376eb74du, 0x378bbda2u, 0x379f7bdfu, 0x381a9dfbu, 0x386c8beeu, 0x38aa2e67u,
    0x39232970u, 0x3a4e3358u, 0x3a76ff9du, 0x3a7d3209u, 0x3b837c1cu, 0x3c478ed8u, 0x3cf7e232u, 0x3d453198u, 0x3d4569b1u,
    0x3d68ca16u, 0x3d6c0735u, 0x3d9bcf46u, 0x3e6e8658u, 0x3ef744a4u, 0x3f0ec4a9u, 0x3f56c996u, 0x3f584a9bu, 0x3f724b49u,
    0x3fcccd2au, 0x40852dd1u, 0x40aa04fau, 0x40c3e959u, 0x40d5d6afu, 0x4106e908u, 0x42b387ebu, 0x42ce6fa1u, 0x4378cbe3u,
    0x43991bbau, 0x44329f3du, 0x445c4952u, 0x44796fc4u, 0x448f401du, 0x456086dau, 0x457ffd17u, 0x4599154cu, 0x464a2874u,
    0x47fe4e2fu, 0x482015c9u, 0x486209b4u, 0x48872d2fu, 0x49388d38u, 0x4a0ded24u, 0x4a96ca0cu, 0x4bef8df2u, 0x4c0e75bfu,
    0x4c28ae8au, 0x4c75e6d4u, 0x4c7c4ddbu, 0x4c864bafu, 0x4d179f5au, 0x4d63342eu, 0x4d7b35e2u, 0x4d8e6475u, 0x4db97c11u,
    0x4dbf2f2eu, 0x4deeb6e9u, 0x4dfc4d8eu, 0x4e374bdbu, 0x4e529ff9u, 0x4e7a8bdau, 0x4f4a4536u, 0x4f957854u, 0x4fa4b42bu,
    0x4fb545d5u, 0x4fc87cf2u, 0x5095a276u, 0x50be1adau, 0x50d87201u, 0x50ebede1u, 0x5121d99fu, 0x512e9adfu, 0x513198ffu,
    0x51c7dd0eu, 0x51e2cff1u, 0x52e7d15bu, 0x53731650u, 0x538f1719u, 0x544eb635u, 0x54571a14u, 0x546381acu, 0x54949e78u,
    0x55e8bfa8u, 0x55f2f6f2u, 0x56366fd9u, 0x563ef231u, 0x5642dba9u, 0x57105a72u, 0x574dd76cu, 0x57b9e94du, 0x582239e2u,
    0x58333c38u, 0x590344e5u, 0x599c4873u, 0x59fea8c0u, 0x5abee8d0u, 0x5b0b7276u, 0x5b57cb31u, 0x5b5a00cau, 0x5babcf3fu,
    0x5bd7d180u, 0x5be3ab6fu, 0x5c14b545u, 0x5c6cfe07u, 0x5c7915d6u, 0x5cc5b00fu, 0x5d024eafu, 0x5d581ad6u, 0x5d5ef28du,
    0x5d75c5d3u, 0x5dfc50fcu, 0x5e33cfa2u, 0x5f14c3c1u, 0x5f24a035u, 0x5f2d0048u, 0x5fa0a22eu, 0x604256dau, 0x60908e18u,
    0x61955675u, 0x61da6a50u, 0x626096f0u, 0x62721e1du, 0x627830acu, 0x631f620eu, 0x639919c0u, 0x64305ec3u, 0x646646a7u,
    0x6535c3b8u, 0x657f662du, 0x65c5ebd6u, 0x65c609f9u, 0x65f21d78u, 0x66956d3au, 0x66d8d596u, 0x676dcd10u, 0x67c8b9e5u,
    0x68974c32u, 0x68eeda60u, 0x6941d24eu, 0x698c110eu, 0x69f3c083u, 0x6a20be34u, 0x6aaff66du, 0x6ae5ce47u, 0x6af88ccau,
    0x6bacb20bu, 0x6c4554dfu, 0x6d06f7cbu, 0x6d49513fu, 0x6d506623u, 0x6d75d7deu, 0x6dc8b398u, 0x6deb04e9u, 0x6e6acfc6u,
    0x6f7c477eu, 0x70869d97u, 0x708d0157u, 0x70e8ffabu, 0x71cf12b9u, 0x71f28006u, 0x72c262a1u, 0x7302441au, 0x733de865u,
    0x73567b9eu, 0x73f5c93fu, 0x74051f75u, 0x743363e4u, 0x74567b96u, 0x747a5f2au, 0x74d311cbu, 0x74e866ffu, 0x74f0a790u,
    0x7542cf57u, 0x7614bb56u, 0x7616ec89u, 0x7624ec1au, 0x7638cc39u, 0x76434a1bu, 0x76769d6du, 0x76796cfcu, 0x76cf813au,
    0x76e80c6du, 0x76fe7346u, 0x76fee7b0u, 0x7745b8a6u, 0x7772a0c4u, 0x77960f7du, 0x77c43d97u, 0x780967fau, 0x7824cd45u,
    0x783c302du, 0x78e3e5adu, 0x78f3f748u, 0x7909e3cfu, 0x794e6765u, 0x794ebfbdu, 0x79788ac5u, 0x79897a04u, 0x799dbb60u,
    0x7a0fa600u, 0x7a12c835u, 0x7a1bd5e7u, 0x7a29015cu, 0x7a7ffd89u, 0x7a9e7945u, 0x7acb8552u, 0x7af92116u, 0x7b48de58u,
    0x7b51d212u, 0x7b61d0abu, 0x7b7ee3e3u, 0x7bb8efd6u, 0x7bc5a2b9u, 0x7c227660u, 0x7c74d9f2u, 0x7c8f609cu, 0x7d3d2ca3u,
    0x7d98b521u, 0x7dbe5e76u, 0x7e6615ccu, 0x7ea9fcf8u, 0x7f1ba391u, 0x7f5e4be6u, 0x7f771338u, 0x8002d4edu, 0x807062e0u,
    0x80be802cu, 0x80ca53d9u, 0x81dd0238u, 0x822a2aa5u, 0x824358b6u, 0x824dc96eu, 0x82b0a5e5u, 0x82ee0e8eu, 0x831d126fu,
    0x84059fd4u, 0x841d8843u, 0x845d074eu, 0x849ed76du, 0x850d101au, 0x8564bd26u, 0x859819bcu, 0x85e4c5c8u, 0x86011082u,
    0x8620b4aau, 0x865726bfu, 0x86c2247cu, 0x86f6cc36u, 0x87295f5cu, 0x87959c99u, 0x883c4020u, 0x88ab92efu, 0x88d6c478u,
    0x8910a0a8u, 0x8952bf91u, 0x8953465au, 0x89805b55u, 0x8991a675u, 0x89b73de9u, 0x8ae8c116u, 0x8b14cba4u, 0x8b1d0984u,
    0x8b264bbau, 0x8bbdc0f8u, 0x8c1fad12u, 0x8c89f41bu, 0x8c8f5738u, 0x8cf09e91u, 0x8d168bbbu, 0x8d2910efu, 0x8d4ecea7u,
    0x8d84ca78u, 0x8dc63da5u, 0x8e1b80d4u, 0x8e42d958u, 0x8e538245u, 0x8e5e4affu, 0x8eef57bau, 0x8f6c766eu, 0x8f80bbc9u,
    0x9047589fu, 0x9051f3b0u, 0x90535041u, 0x90a83c7bu, 0x90aa9ff8u, 0x90b5a761u, 0x90c61d54u, 0x91d65f28u, 0x91e43623u,
    0x9226aca8u, 0x922896feu, 0x9246c97cu, 0x924e1e58u, 0x92a4d76bu, 0x92ec0de8u, 0x93410c3du, 0x9342fca7u, 0x94779853u,
    0x954f81e5u, 0x96936de3u, 0x96abb50au, 0x9737a2c5u, 0x98375cb3u, 0x9854ad6du, 0x98922d67u, 0x98d405b5u, 0x99525ba2u,
    0x995e2051u, 0x999e81c2u, 0x99d6a72cu, 0x9a0db95du, 0x9ad727ebu, 0x9afadd67u, 0x9b3aa10du, 0x9ba0ea17u, 0x9c702713u,
    0x9c7896e4u, 0x9c8b1779u, 0x9c9b3af8u, 0x9ced348du, 0x9d2ddfc3u, 0x9d4ae7a9u, 0x9d8d1122u, 0x9deddc02u, 0x9dff0b99u,
    0x9e0ee4aau, 0x9e1515f0u, 0x9e31d59au, 0x9e8c3c31u, 0x9e92c406u, 0x9ecf855cu, 0x9f584125u, 0x9fb2615bu, 0x9fd3f6dbu,
    0xa019674du, 0xa07daf91u, 0xa0978d4fu, 0xa0a1400au, 0xa0b87cf3u, 0xa0daae6bu, 0xa0fe9b08u, 0xa1603283u, 0xa1a95a36u,
    0xa22ae500u, 0xa26050b5u, 0xa2bf5dc9u, 0xa3940767u, 0xa39fa665u, 0xa3d56c13u, 0xa4047009u, 0xa45d83bbu, 0xa46c827fu,
    0xa4844e3au, 0xa4939bbau, 0xa58ea8aeu, 0xa59e59b2u, 0xa5bc254cu, 0xa6149274u, 0xa65ae173u, 0xa721da2cu, 0xa763f60au,
    0xa7857dcdu, 0xa83c62dbu, 0xa88b275fu, 0xa92a96c2u, 0xa9492c25u, 0xa99fa03fu, 0xaa2bebceu, 0xaa8cff36u, 0xaa902baeu,
    0xaa92a7cdu, 0xaa9db1a3u, 0xaad162aau, 0xaad25e54u, 0xaae80524u, 0xab827465u, 0xab912872u, 0xabd8719du, 0xac9cf460u,
    0xace4534fu, 0xadb35ad5u, 0xadbbb687u, 0xadfdca96u, 0xae0a1fa9u, 0xae1f0f9fu, 0xae208070u, 0xae3ad078u, 0xaea1ecc1u,
    0xaedb1d56u, 0xaf086b9au, 0xaf2bcce5u, 0xafa835b7u, 0xaff1c490u, 0xb007f202u, 0xb09ac7b0u, 0xb109cc3fu, 0xb14afc91u,
    0xb1789b7au, 0xb19dea88u, 0xb35e47deu, 0xb3d87c81u, 0xb446fcb2u, 0xb4ab6b6bu, 0xb4e04074u, 0xb544a345u, 0xb69389ecu,
    0xb6af5a9eu, 0xb6fbc23cu, 0xb75b87e0u, 0xb7eb9423u, 0xb8103c48u, 0xb83bc543u, 0xb8442023u, 0xb87c2f44u, 0xb92f3f19u,
    0xb954a685u, 0xb9b31eabu, 0xb9db8340u, 0xba01bf2cu, 0xba33e06fu, 0xbab90532u, 0xbad2a8ddu, 0xbaf1971eu, 0xbb11d765u,
    0xbb246b67u, 0xbb641633u, 0xbb9f04dcu, 0xbd757e13u, 0xbdb1ea1au, 0xbdca54b8u, 0xbe00b540u, 0xbe12dfd8u, 0xbe32ef29u,
    0xbe5cb133u, 0xbf427dcbu, 0xbf56277fu, 0xbffe7157u, 0xc037db34u, 0xc03f4fc5u, 0xc0b36466u, 0xc0f0be30u, 0xc101ab4du,
    0xc2008703u, 0xc20fa1dbu, 0xc236ce0eu, 0xc252b3d0u, 0xc28e785fu, 0xc2d2cf2bu, 0xc36ed8f8u, 0xc375f869u, 0xc3abc08eu,
    0xc3f6676fu, 0xc4156f94u, 0xc45c33d1u, 0xc4ca619au, 0xc527dc25u, 0xc57c8eddu, 0xc5842e5au, 0xc59557efu, 0xc5cb8c97u,
    0xc5de46ecu, 0xc5ead6beu, 0xc65d0d65u, 0xc6fd8022u, 0xc73da7afu, 0xc7ae7420u, 0xc7bee91bu, 0xc7f2d002u, 0xc8257781u,
    0xc83396ffu, 0xc8502a98u, 0xc87887e3u, 0xc87c07edu, 0xc93afd1cu, 0xc97022cfu, 0xc9ac5040u, 0xca01deb8u, 0xca193558u,
    0xca4b9359u, 0xca8a8ab4u, 0xcb1a798cu, 0xcbeabdf6u, 0xcc364f5du, 0xcc57b98du, 0xce8496fbu, 0xcf02d793u, 0xcf14a362u,
    0xcf203cf2u, 0xcf3d6a32u, 0xcf851974u, 0xcfc17dc7u, 0xcfdfcd26u, 0xd00eabdcu, 0xd0425c68u, 0xd04b1f31u, 0xd06fec8fu,
    0xd1da939du, 0xd1e2e86cu, 0xd2056d52u, 0xd208700du, 0xd2337b88u, 0xd25942aau, 0xd28a66ebu, 0xd2d89829u, 0xd3ccc125u,
    0xd3dbb4acu, 0xd50c1099u, 0xd547a9d5u, 0xd5cbb29au, 0xd6c9213eu, 0xd745c818u, 0xd76382fbu, 0xd7eda253u, 0xd7f4bd0eu,
    0xd80484bdu, 0xd870e461u, 0xd8997b2eu, 0xd8d33be7u, 0xd8d64ca6u, 0xda14f347u, 0xda7ffddfu, 0xdb397d70u, 0xdb4bf761u,
    0xdc17d25au, 0xdd855740u, 0xdf2ab939u, 0xdf9fd958u, 0xdfb2adb2u, 0xdff20fd4u, 0xe01fcd70u, 0xe02a6205u, 0xe0813a32u,
    0xe08b04ceu, 0xe14895ecu, 0xe14c32e0u, 0xe1fa96e1u, 0xe29c6b33u, 0xe2aaf40eu, 0xe2cb7050u, 0xe2e6c577u, 0xe2eb84c7u,
    0xe30f0826u, 0xe32ed9aeu, 0xe33031dau, 0xe341938au, 0xe3c03a7du, 0xe402f84fu, 0xe41baee8u, 0xe425c79eu, 0xe443ea27u,
    0xe46c911bu, 0xe4856c02u, 0xe4d35f44u, 0xe550e002u, 0xe58f6da7u, 0xe660eaacu, 0xe6720358u, 0xe69c4fa7u, 0xe6bcaf8fu,
    0xe6d5db9au, 0xe7d4cd30u, 0xe94bbf9cu, 0xe99561e2u, 0xe9f68824u, 0xea8249c7u, 0xea99b802u, 0xeaa0eeeeu, 0xeb28e95cu,
    0xeb459353u, 0xeb9968b3u, 0xebd70c4bu, 0xebec2dd1u, 0xebf27305u, 0xec2d360bu, 0xec7e0a5du, 0xecec6df4u, 0xed00d1dfu,
    0xed258340u, 0xed8a80ceu, 0xedcf0f80u, 0xede7ef8eu, 0xee0b7089u, 0xee20ef90u, 0xee6b76cau, 0xee7bed15u, 0xeee95167u,
    0xeef744ebu, 0xefaa259du, 0xefdc79d0u, 0xf137ecf6u, 0xf202da6du, 0xf2e176a9u, 0xf337c243u, 0xf361ef37u, 0xf378b108u,
    0xf3b03938u, 0xf40b972du, 0xf431a4b8u, 0xf4835ca0u, 0xf57ff0bcu, 0xf635825au, 0xf72e4fcbu, 0xf7375cafu, 0xf7b87fd8u,
    0xf7bac5b5u, 0xf849facbu, 0xf85a171au, 0xf8704d3fu, 0xf8cd2502u, 0xf90d3c79u, 0xf9367881u, 0xf95b8c56u, 0xfa9f42dbu,
    0xfaa058d1u, 0xfaeb7c73u, 0xfb419352u, 0xfb832abcu, 0xfb9b204cu, 0xfbcad61du, 0xfbe6a3b7u, 0xfc503a47u, 0xfc52417fu,
    0xfc6fb203u, 0xfd1c79bbu, 0xfd39a396u, 0xfd3cd4e0u, 0xfd4188c6u, 0xfe757b6du, 0xfe928607u, 0xfeeb1a90u, 0xff53d24du,
    0xffa89848u, 0xffc29167u};
inline uint32_t path_hash(const char* path) {
    uint32_t n = 0;
    while (path[n]) ++n;
    xyf_core::hash::Sha256 h;
    xyf_core::hash::Digest d;
    h.update(reinterpret_cast<const uint8_t*>(path), n);
    h.finish(d);
    return uint32_t(d.bytes[0]) << 24 | uint32_t(d.bytes[1]) << 16 | uint32_t(d.bytes[2]) << 8 | d.bytes[3];
}
// Is path one of table's (sorted, count entries)?
inline bool listed(const char* path, const uint32_t* table, uint32_t count) {
    const uint32_t h = path_hash(path);
    uint32_t lo = 0, hi = count;
    while (lo < hi) {
        const uint32_t mid = (lo + hi) >> 1;
        if (table[mid] < h) lo = mid + 1;
        else hi = mid;
    }
    return lo < count && table[lo] == h;
}

// op1_factory.json as Open-XY's builder leaves it (open-xy builder/core.mjs buildAssets and tooling/native.py
// extend_catalog, upstream 22aa31c): the official file with each chosen machine's catalog entry inserted as text
// (",\n        {...}") after the last official entry of engine_types, fx_types or seq_types; nothing else changes.
// Open-XY has no uninstall: its "disable" leaves the file as it is. restore() takes the inserted entries out again.
struct List {
    const char* key;
    uint32_t count;   // the official entries
};
static const uint32_t list_count = 3;
static const List lists[list_count] = {{"engine_types", 13}, {"fx_types", 6}, {"seq_types", 3}};
static const uint32_t catalog_max = 16384;   // the largest op1_factory.json restored (the mod job's bound)

// Inlined: the build's call audit accepts only calls whose target address loads beside them.
inline __attribute__((always_inline)) bool space(uint8_t c) { return c == ' ' || c == '\t' || c == '\n' || c == '\r'; }
inline __attribute__((always_inline)) uint32_t skip_space(const uint8_t* t, uint32_t n, uint32_t i) {
    while (i < n && space(t[i])) ++i;
    return i;
}
// One past the string at i (t[i] is '"'), or 0.
inline __attribute__((always_inline)) uint32_t string_end(const uint8_t* t, uint32_t n, uint32_t i) {
    for (++i; i < n; ++i) {
        if (t[i] == '\\') ++i;
        else if (t[i] == '"') return i + 1;
    }
    return 0;
}
// One past the value at i, or i when there is none. Only its nesting is checked: the caller's hash decides.
inline __attribute__((always_inline)) uint32_t value_end(const uint8_t* t, uint32_t n, uint32_t i) {
    const uint32_t start = i;
    uint32_t depth = 0;
    while (i < n) {
        const uint8_t c = t[i];
        if (c == '"') {
            const uint32_t e = string_end(t, n, i);
            if (!e) return start;
            if (!depth) return e;
            i = e;
            continue;
        }
        if (c == '{' || c == '[') ++depth;
        else if (c == '}' || c == ']') {
            if (!depth) return i;
            if (!--depth) return i + 1;
        } else if (!depth && (c == ',' || space(c))) return i;
        ++i;
    }
    return depth ? start : i;
}
inline __attribute__((always_inline)) bool same_key(const uint8_t* t, uint32_t n, const char* key) {
    uint32_t i = 0;
    for (; i != n && key[i]; ++i)
        if (t[i] != uint8_t(key[i])) return false;
    return i == n && !key[i];
}
// In place: each list of `wanted` keeps its first `count` entries; the text from the end of the last one kept up to the
// line the list's `]` is on goes (an inserted entry may end with a newline of its own; the list's closing line is the
// official one). The new length, or 0 when the text is not an object holding every wanted list with at least its
// count. The caller accepts the result only when its SHA-256 is the official one.
inline uint32_t restore(uint8_t* t, uint32_t n, const List* wanted = lists, uint32_t wanted_count = list_count) {
    uint32_t i = skip_space(t, n, 0), found = 0;
    if (i == n || t[i] != '{') return 0;
    for (i = skip_space(t, n, i + 1); i < n && t[i] != '}';) {
        if (t[i] != '"') return 0;
        const uint32_t key = i, key_end = string_end(t, n, i);
        if (!key_end) return 0;
        i = skip_space(t, n, key_end);
        if (i == n || t[i] != ':') return 0;
        i = skip_space(t, n, i + 1);
        const List* l = 0;
        for (uint32_t k = 0; k != wanted_count; ++k)
            if (same_key(t + key + 1, key_end - key - 2, wanted[k].key)) l = wanted + k;
        if (l && i < n && t[i] == '[') {
            uint32_t j = skip_space(t, n, i + 1), entries = 0, keep = i + 1, last = i + 1;
            while (j < n && t[j] != ']') {
                const uint32_t e = value_end(t, n, j);
                if (e == j) return 0;
                if (++entries == l->count) keep = e;
                last = e;
                j = skip_space(t, n, e);
                if (j < n && t[j] == ',') j = skip_space(t, n, j + 1);
                else if (j == n || t[j] != ']') return 0;
            }
            if (j == n || entries < l->count) return 0;
            ++found;
            if (entries != l->count) {
                uint32_t cut = last;
                for (uint32_t k = last; k != j; ++k)
                    if (t[k] == '\n') cut = k;
                const uint32_t gone = cut - keep;
                for (uint32_t k = cut; k != n; ++k) t[k - gone] = t[k];
                n -= gone;
                j -= gone;
            }
            i = j + 1;
        } else {
            const uint32_t e = value_end(t, n, i);
            if (e == i) return 0;
            i = e;
        }
        i = skip_space(t, n, i);
        if (i < n && t[i] == ',') i = skip_space(t, n, i + 1);
        else if (i == n || t[i] != '}') return 0;
    }
    return i < n && found == wanted_count ? n : 0;
}
}}
#endif
