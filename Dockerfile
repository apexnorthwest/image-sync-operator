# rust @ 1.99-slim
FROM docker.io/library/rust@sha256:7eab7ce19273ce785e0ca7578bdf7bc1fcbae621775a8197379a04c9f6e03cd5 AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src src
# Static builds require us to specify the target arch, so we have to get a little specific
# In practice, this is just a clippy run and a normal static build with auditable enabled.
# ARM64 builds use musl for static linking, as glibc static only works on AMD64 right now.
RUN set -e ; if lscpu | grep -q x86_64; then \
    rustup component add clippy &&\
    cargo install cargo-auditable &&\
    CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo auditable clippy --release --target x86_64-unknown-linux-gnu &&\
    CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo auditable build --target x86_64-unknown-linux-gnu --release &&\
    cp target/x86_64-unknown-linux-gnu/release/image-sync-operator /image-sync-operator || exit 1; \
  elif lscpu | grep -q aarch64; then \
    apt update && apt install -y musl musl-dev &&\
    rustup component add clippy &&\
    rustup target add aarch64-unknown-linux-musl &&\
    cargo install cargo-auditable &&\
    CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo auditable clippy --release --target aarch64-unknown-linux-musl &&\
    CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo auditable build --target aarch64-unknown-linux-musl --release &&\
    cp target/aarch64-unknown-linux-musl/release/image-sync-operator /image-sync-operator; \
  else \
    echo "Unsupported architecture: $(lscpu | grep Architecture | awk '{print $2}')"; \
    exit 1; \
  fi ; \
  rm -rf target # This speeds up the layer commit and thus the build

FROM scratch AS release
# Copy the binary
COPY --from=build /image-sync-operator /image-sync-operator
ENTRYPOINT ["/image-sync-operator"]
