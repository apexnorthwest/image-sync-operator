FROM docker.io/library/rust:1 AS builder

WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src src
RUN RUSTFLAGS="-C target-feature=+crt-static" cargo build --target x86_64-unknown-linux-gnu --release

FROM scratch AS release
COPY --from=builder /app/target/x86_64-unknown-linux-gnu/release/image-sync-operator /image-sync-operator
COPY --from=builder /app/target/x86_64-unknown-linux-gnu/release/crdgen /crdgen
CMD ["/image-sync-operator"]
