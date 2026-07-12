##############################################################################
# Copyright 2026 Apex Northwest
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
##############################################################################
FROM gcr.io/distroless/python3-debian13 as check

# Get the latest uv and uvx binaries from the Astral distroless image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Get our code into the image
WORKDIR /app
ENV HOME=/app
COPY pyproject.toml /app/
COPY src/ /app/src/

# Install dependencies
RUN ["/bin/uv", "sync"]

# Run syntax and type checks on the code
RUN ["/bin/uvx", "ruff", "check", "src"]
RUN ["/bin/uvx", "ty", "check", "src"]

##############################################################################
# Start from a clean image for the release
FROM gcr.io/distroless/python3-debian13 as release

# Copy the source and venv from the check stage
COPY --from=check /app/src /app/src
COPY --from=check /app/.venv/ /app/.venv/

# Set the working directory and entrypoint for the release image
WORKDIR /app
ENV HOME=/app
ENV PYTHONPATH=/app/src
ENTRYPOINT ["/app/.venv/bin/python3", "-u", "/app/src/main.py"]
