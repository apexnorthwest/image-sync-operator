FROM docker.io/library/rust:1 AS builder

WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN set -e ; if [ "$TARGETARCH" = "amd64" ]; then \
    rustup component add clippy && CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo clippy --release --target x86_64-unknown-linux-gnu &&\
    CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo build --target x86_64-unknown-linux-gnu --release &&\
    cp target/x86_64-unknown-linux-gnu/release/image-sync-operator /image-sync-operator; \
  elif [ "$TARGETARCH" = "arm64" ]; then \
    rustup component add clippy && CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo clippy --release --target aarch64-unknown-linux-gnu &&\
    CARGO_BUILD_WARNINGS=deny RUSTFLAGS="-C target-feature=+crt-static" cargo build --target aarch64-unknown-linux-gnu --release &&\
    cp target/aarch64-unknown-linux-gnu/release/image-sync-operator /image-sync-operator; \
  else \
    echo "Unsupported architecture: $TARGETARCH"; \
    exit 1; \
  fi

FROM scratch AS release
COPY --from=builder /image-sync-operator /image-sync-operator
USER 9999:9999
CMD ["/image-sync-operator"]
