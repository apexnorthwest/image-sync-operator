# rust @ 1.97-slim
FROM docker.io/library/rust@sha256:5c6f46a6e4472ab1ca7ba7d494e6677f2f219ebc02f32025d3986f057635ec9c AS build
WORKDIR /app
COPY Cargo.toml Cargo.lock ./
COPY src src
ADD https://github.com/anchore/syft/releases/download/v1.50.0/syft_1.50.0_linux_amd64.deb /tmp/syft-amd64.deb
ADD https://github.com/anchore/syft/releases/download/v1.50.0/syft_1.50.0_linux_arm64.deb /tmp/syft-arm64.deb
RUN set -e ; if lscpu | grep -q x86_64; then \
    dpkg -i /tmp/syft-amd64.deb; \
    rustup component add clippy && CARGO_BUILD_WARNINGS=deny cargo clippy --release --target x86_64-unknown-linux-gnu &&\
    CARGO_BUILD_WARNINGS=deny cargo build --target x86_64-unknown-linux-gnu --release &&\
    cp target/x86_64-unknown-linux-gnu/release/image-sync-operator /image-sync-operator || exit 1; \
  elif lscpu | grep -q aarch64; then \
    dpkg -i /tmp/syft-arm64.deb; \
    rustup component add clippy && CARGO_BUILD_WARNINGS=deny cargo clippy --release --target aarch64-unknown-linux-gnu &&\
    CARGO_BUILD_WARNINGS=deny cargo build --target aarch64-unknown-linux-gnu --release &&\
    cp target/aarch64-unknown-linux-gnu/release/image-sync-operator /image-sync-operator; \
  else \
    echo "Unsupported architecture: $(lscpu | grep Architecture | awk '{print $2}')"; \
    exit 1; \
  fi ; \
  rm -rf target /tmp/syft-*.deb # This speeds up the layer commit and thus the build
# Run syft in the build container since our stripped binary makes it hard to generate this from the packed release image layer.
# While we would usually prefer to do this outside the build container, this gives us the most accurate results.
# Given the final image is distroless, the only thing that could drift is the glibc and even then it won't be by much.
RUN syft . -o spdx-json=sbom.spdx.json

# Use distroless-cc (we can't do scratch because aarch64 doesn't fully support static builds with our particular libraries)
# Based on cc-debian13:nonroot
FROM gcr.io/distroless/cc-debian13@sha256:a77defd6fedbb3392b175ba8ea3d1c22be963c1597c248c3ba987ddd80bfb512 AS release
# Copy the binary
COPY --from=build /image-sync-operator /image-sync-operator
# Copy the SBOM and Cargo.lock. We do this in the release container so that the SBOM, Cargo.lock, and Cargo.toml used to generate the binary get
# signed by the same attestation as the image itself. This ensures the provenance of those files and makes it easy to match them
# to a specific image version.
COPY --from=build /app/sbom.spdx.json /sbom.spdx.json
COPY --from=build /app/Cargo.lock /Cargo.lock
COPY --from=build /app/Cargo.toml /Cargo.toml
ENTRYPOINT ["/image-sync-operator"]
