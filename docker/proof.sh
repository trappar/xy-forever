#!/bin/sh
# The #321 byte-identity proof, run inside the container (docker/run.sh PLATFORM sh docker/proof.sh): the product
# and the MTP USB device-test build, and the freestanding/stack-usage compiles of tests/test_xyf_freestanding.py
# (its flags, over every target/ and native/ source), then one SHA-256 list of every file. Each platform builds at the
# same path (build/proof: paths reach the listings and the ELF), then moves it to build/docker-<machine>/.
set -eu
out=build/proof
done=build/docker-$(uname -m)
rm -rf "$out" "$done"
mkdir -p "$out"
xyf-build --xyf-product --out "$out/product" > "$out/product.json"
xyf-build --xyf-mtp-usb --out "$out/mtp-usb" > "$out/mtp-usb.json"
gxx=$XYF_TOOLCHAIN/bin/bfin-elf-g++
for dir in target native; do
  mkdir -p "$out/free/$dir"
  for src in "/src/$dir"/*.cpp; do
    name=$(basename "$src" .cpp)
    (cd "$out/free/$dir" && "$gxx" -mcpu=bf707 -std=gnu++11 -ffreestanding -fno-exceptions -fno-rtti \
      -fno-threadsafe-statics -fno-use-cxa-atexit -fno-asynchronous-unwind-tables -fno-unwind-tables -fno-common \
      -fno-tree-loop-distribute-patterns -fomit-frame-pointer -nostdinc++ -Wall -Werror -Wno-unused-variable \
      -Os -fstack-usage -I /src/target -S "$src" -o "$name.s" 2> /dev/null) || echo "$dir/$name: does not compile alone"
  done
done
(cd "$out" && find product mtp-usb free -type f | LC_ALL=C sort | xargs sha256sum) > "$out/SHA256SUMS"
grep -E ' (product|mtp-usb)/candidate.ldr$' "$out/SHA256SUMS"
echo "free: $(find "$out/free" -type f | wc -l) files"
mv "$out" "$done"
