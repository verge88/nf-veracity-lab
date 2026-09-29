FROM ubuntu:24.04
ARG DEBIAN_FRONTEND=noninteractive
ARG OPEN5GS_REF=v2.7.6
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git build-essential meson ninja-build pkg-config cmake flex bison \
    libsctp-dev libc-ares-dev libgnutls28-dev libgcrypt-dev libssl-dev libidn-dev libmongoc-dev \
    libbson-dev libyaml-dev libnghttp2-dev libmicrohttpd-dev libcurl4-gnutls-dev \
    libtins-dev libtalloc-dev python3 python3-pip python3-venv && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch ${OPEN5GS_REF} https://github.com/open5gs/open5gs.git /build/open5gs \
    && cd /build/open5gs && meson setup build --prefix=/opt/open5gs \
    && ninja -C build && ninja -C build install \
    && git rev-parse HEAD > /opt/open5gs/SOURCE_COMMIT
WORKDIR /lab
RUN python3 -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
ENV LD_LIBRARY_PATH=/opt/open5gs/lib/x86_64-linux-gnu:/opt/open5gs/lib
COPY requirements.txt requirements.txt
RUN python3 -m pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONPATH=/lab
CMD ["python3", "scripts/core.py"]
