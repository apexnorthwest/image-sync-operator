# Contribution and Development
All rules and guidelines in this document are expected to be followed by
contributors. Any pull requests that are found not to follow these rules will
be rejected. No worries though, we won't be mean about it. :) If your PR is
rejected due to a contribution guideline issue, we'll do our best to be clear
about why and what you can do to resolve it. We're all on the same team.

## Guidelines for Contributing
### Rule 1: Style and Linting
This project uses standard rust conventions. You should have an appropriate
amount of comments to make clear what your code does without going into too
much detail. Additionally, we expect the code to pass machete, cargo check,
cargo clippy, and be in line with cargo standard format. You can do the following
to format and check your code locally without building the container:

```shell
# Run these once to set up the toolchain
rustup update stable
rustup default stable
rustup component add clippy
cargo install cargo-machete

# Run the formatter and checks. This takes a while the first time
cargo fmt
cargo check
cargo clippy
cargo machete
```

### Rule 2: External tooling
This application offloads the work of actually moving the images around to
[Skopeo](https://skopeo.org/). However we do not pack that in with the
operator. We use the upstream image as the job image.

This image will be packed as a static binary in a scratch container to prevent
exposure surface and minimize the final image size.

### Rule 3: Vulnerability Scanning
Multiple vulnerability and code quality scanners are in use on this project.
Alerts from any of them should be considered mandatory prior to merging a pull
request against the project. It is expected that you as a contributor will have
compiled the project container image and run the test suite prior to opening the
PR.

Should you discover a critical vulnerability, you are encouraged to report it via
<admin@apexnorthwest.com>. Should the maintainers fail to respond within 30 days,
you should open an issue on this repo. If an unpatched vulnerability is found in a
library or included binary, please open an issue right away with the appropriate details.

### Rule 4: Use of AI tools
Use of AI assisted coding tools is acceptable, but all code is expected to be
of high quality and comply with this standards document. All code should be
reviewed, tested, and understood by the contributor. You as the developer are
responsible for the code you submit, AI assisted or otherwise. You, as a developer,
must be able to explain and defend your code when asked. 'Vibe coding' is not
of suitable quality for use in an operator.

Pull requests submitted by an automated agent or code written entirely by an
agent are very likely to be rejected. Autonomous AI agents are powerful, but
at this time they are still prone to subtle errors and misunderstanding of
the finer semantics of many applications. Bugs introduced by agents can be
difficult to debug, especially without anyone who knows how their code works.

This section is subject to change as these technologies develop rapidly. Our
stance is to remain conservative on what we permit. While these tools are
very valuable, and this project's maintainers do use some of them, there is
tangible value in having people who know and deeply understand code. As in all
things in life, balance is important.

## Development Guide
The basic workflow for contributing will look like the following:
1. Fork the repository and check out your own version of the project. You should also create a branch with a name the represents the work you're doing.
```sh
git clone https://github.com/<your username>image-sync-operator.git
cd image-sync-operator
```
2. Set up your development machine.  
  This step is mostly out of scope for this guide but the requirements are:
  > - Docker (or Podman if you prefer)
  > - Kubectl and Helm (For the test suite)
  > - KIND (Also for the test suite)
  > - Rustup for managing your compiler toolchain
3. Do your development. This should be the fun part :)
4. Building the code using your build tool of choice.  
  While you *can* build the code using cargo directly, it's not that useful since it's an operator.
```sh
# Run code checks. Probably not worth building at all if these fail
cargo fmt
cargo check
cargo clippy
cargo machete

# Build in debug mode. Be advised that the debug images are much larger than production builds
docker build -t image-sync-operator:debug -f Dockerfile-debug .
# OR
podman build -t image-sync-operator:debug --layers -f Dockerfile-debug .
```
5. At this point you can put that image somewhere a k8s cluster can access it and test using the helm chart:
```sh
# Put our test image in a repo for our cluster to consume
docker tag image-sync-operator:debug my-private-repo.example.com/image-sync-operator:debug
docker push my-private-repo.example.com/image-sync-operator:debug

# Install the CRDs, (or update them)
kubectl apply -f crds/imagesyncs.yaml

# Install the operator (in this case, limited to only the image-sync-operator namespace)
helm install -n image-sync-operator --create-namespace \
  --set operator.image.repository=my-private-repo.example.com/image-sync-operator \
  --set operator.image.tag=debug \
  --set operator.image.pullPolicy=Always

# Now you can create some ImageSync objects in the image-sync-operator for testing.
```
6. Run the test suite  
  Note: The test suite requires docker cli. The short reason is that rootless podman is not stable with KIND and we don't want to run tests as root.
```sh
# Enter the testing folder. IMPORTANT: All scripts assume this is your working directory.
cd testing
# Set up the kind environment
bash 01_kind_setup.sh
# Install local registries inside the kind cluster to use as test targets
bash 02_registry_setup.sh
# Test those registries to ensure they work, this helps ensure that we can tell if test failures are code related or not
bash 03_test_registry.sh
# Setup and do some basic tests of the operator
bash 04_operator_startup.sh

# At this point you can use kubectl to create additional test ImageSyncs to exercise the operator:
KUBECONFIG=./testing-kubeconfig kubectl apply -f mytests.yaml

# To clean up the environment, a helper script is provided:
bash 99_kind_teardown.sh
```
7. Open a Pull Request  
  Your pull request should explain what problem you're trying to solve and link to an Issue if there's one that you expect
  to be fixed by the change. If it's a new feature, you must provide background on why the feature is needed and an example
  of both the problem and how to use the new feature.
