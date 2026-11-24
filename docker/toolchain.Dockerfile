# The firmware build environment (#321): Open-XY's Blackfin toolchain built from its pinned source by its own
# tools/gcc/build-linux.sh, plus the Python that runs xyf-build. One image per platform (linux/arm64, linux/amd64).
#
#   docker buildx build --platform linux/arm64 -f docker/toolchain.Dockerfile -t xyf-toolchain:arm64 --load docker
#
# Upstream: etcetc000/open-xy at OPEN_XY (tools/gcc/build-linux.sh, linux.Dockerfile, patches; unchanged since
# 0498de9469507b3f5d5b7a74871bf722e2a92525), the GCC/binutils fork at FORK (its release-sources.json). The base image
# and every Debian package are upstream's linux.Dockerfile pins: one snapshot.debian.org date. The tools are built at
# upstream's paths (/opt/opxy-toolchain/...), which are compiled into them.
ARG DEBIAN=debian:bookworm-20251020@sha256:26f2a7cab45014541c65f9d140ccfa6aaefbb49686c6759bea9c6f7f5bb3d72f
ARG UV=ghcr.io/astral-sh/uv:0.13.9@sha256:d3b601f0c7b118bc1753febf7c41cc80981df678445e0254284eecf84db79a7d

FROM ${UV} AS uv

FROM ${DEBIAN} AS toolchain
# Upstream's linux.Dockerfile, verbatim.
RUN rm -f /etc/apt/sources.list.d/* && \
    printf '%s\n' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20251020T000000Z bookworm main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian/20251020T000000Z bookworm-updates main' \
      'deb [check-valid-until=no] http://snapshot.debian.org/archive/debian-security/20251020T000000Z bookworm-security main' \
      > /etc/apt/sources.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends gcc g++ make bison flex m4 git \
      libgmp-dev libmpfr-dev libmpc-dev && \
    rm -rf /var/lib/apt/lists/*
ARG OPEN_XY=c235d310ec5956577d7f65721d9633a60c46712d
ARG FORK=e4408a15b9be6cd911283f103a1a3f69e9a6b8c0
ADD --keep-git-dir=false https://github.com/etcetc000/open-xy.git#${OPEN_XY} /opt/opxy-toolchain/open-xy
ADD --keep-git-dir=false https://github.com/deanm1278/blackfin-plus-gnu.git#${FORK} /opt/opxy-toolchain/fork
# build-linux.sh names the x86_64 multiarch folders for the static gmp/mpfr/mpc; on any other platform, that
# platform's (a no-op on amd64, where the script runs as upstream wrote it).
RUN arch=$(gcc -dumpmachine) && \
    sed -i "s/x86_64-linux-gnu/$arch/g" /opt/opxy-toolchain/open-xy/tools/gcc/build-linux.sh && \
    /opt/opxy-toolchain/open-xy/tools/gcc/build-linux.sh /opt/opxy-toolchain/fork /opt/opxy-toolchain/out/blackfin

FROM ${DEBIAN}
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=toolchain /opt/opxy-toolchain/out/blackfin /opt/opxy-toolchain/out/blackfin
# mise.toml's Python, installed by uv (python-build-standalone); nothing is downloaded at run time but the wheels.
ENV UV_PYTHON_INSTALL_DIR=/opt/python
RUN uv python install 3.15.2
ENV UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=3.15.2 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    XYF_TOOLCHAIN=/opt/opxy-toolchain/out/blackfin
WORKDIR /src
