# Contribution and Development
All rules and guidelines in this document are expected to be followed by
contributors. Any pull requests that are found not to follow these rules will
be rejected. No worries though, we won't be mean about it. :) If your PR is
rejected due to a contribution guideline issue, we'll do our best to be clear
about why and what you can do to resolve it. We're all on the same team.

## Rule 1: Style
This project uses standard rust conventions. You should have an appropriate
amount of comments to make clear what your code does without going into too
much detail. Obviously AI comments that say a lot but communicate no value
are not acceptable.

## Rule 2: Packaging
This application offloads the work of actually moving the images around to
[Skopeo](https://skopeo.org/). However we do not pack that in with the
operator. We use the upstream image as the job image.

This image will be packed as a static binary in a scratch container to prevent
exposure surface and minimize the final image size.

## Rule 3: Vulnerability Scanning
Multiple vulnerability and code quality scanners are in use on this project.
Alerts from any of them should be considered mandatory prior to merging a pull
request against the project. It is expected that you as a contributor will have
compiled the project container image and run the test suite prior to opening the
PR.

Should you discover a critical vulnerability, you are encouraged to report it via
<admin@apexnorthwest.com>. Should the maintainers fail to respond within 30 days,
you should open an issue on this repo. If an unpatched vulnerability is found in a
library or included binary, please open an issue with the appropriate patched version details.

## Rule 4: Use of AI tools
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
