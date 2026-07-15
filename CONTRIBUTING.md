# Contribution and Development
All rules and guidelines in this document are expected to be followed by
contributors. Any pull requests that are found not to follow these rules will
be rejected. No worries though, we won't be mean about it. :) If your PR is
rejected due to a contribution guideline issue, we'll do our best to be clear
about why and what you can do to resolve it. We're all on the same team.

## Rule 1: Python Version
All submitted code should run without errors against the latest stable version
of python as shown on the [official website](https://www.python.org/downloads/).

If you are unable to to run the code on that version do to errors in code
outside of your change set, you may make a clear note of the error you found
in the pull request and list the newest python version that tested without
errors. If those bugs have an issue, please link it. If not, please create one.

That all said, we build against gcr.io/distroless/python3-debian13 so the
release images may not always be on the latest python if it isn't what that
repo is shipping.

## Rule 2: Style
This project uses the formatter built into `ruff`. Our rule customizations are
included in the pyproject.toml this repo contains. The main points are:

- We prefer single quotes over double quotes
- We permit line lengths up to 120 characters

You should add a docstring to every function, class, and complex object.
Follow the [pep 257](https://peps.python.org/pep-0257/) format. You should also
include the parameters of any function in the docstring with a good description.

All code, variables, and comments will be written in English. Use meaningful
variable names and clear data structures. Do not waste cpu time or memory where
simple changes would be more effective. That said, we prefer simplicity over
optimization unless a notable pain point is identified.

## Rule 3: Libraries
Python has a fantastic collection of libraries available, but their quality and
utility can vary a lot. The list of approved libraries are below. The
dependencies of these libraries are implicitly allowed. You may request the
approval of a new library by opening an issue. Be aware that you will be asked
to make a good case for why the extra requirement is worth it.

Approved Libraries:
- [jinja2](https://pypi.org/project/jinja2)
- [kubedantic](https://pypi.org/project/kubedantic/)
- [pydantic](https://pypi.org/project/pydantic/)
- [requests](https://pypi.org/project/requests/)

## Rule 4: Use of the standard Kubernetes library
You may notice that this repo does not import or utilize the
[kubernetes](https://pypi.org/project/kubernetes/) library. The reason is simple.
In our testing, we found that the kubernetes library has a bad habit of allocating
far more memory than required and not freeing it promptly.

As a result, we have implemented the required kubernetes api calls using the
`requests` library directly. This is both faster to load, and uses less memory.
The added complexity has been judged worth the effort by the maintainers.

## Rule 5: Packaging
This application offloads the work of actually moving the images around to
[Skopeo](https://skopeo.org/). No need to reinvent the wheel when high quality
tools already exist. Aside from this tool, we don't package any unneeded binaries.

The libraries used by this application don't package their own binaries, so we use
a distroless final image. The builder images are used to fetch libraries and
run checks and tests.

We use [uv](https://docs.astral.sh/uv/) to install and manage packages, so
we ship a `pyproject.toml` rather than a `requirements.txt`.

## Rule 6: Linting and Type Checking
All code is required to pass the default test suite of both
[ruff](https://docs.astral.sh/ruff/) and [ty](https://docs.astral.sh/ty/).
`ty` specifically is a bit new so there may be some cases where it fails a check
in error. This is rare but if it happens, you can bypass that check with a comment
and make a clear note as to why it's a false positive.

We expect all code to have type annotations everywhere. Yes it can be tedious,
but reliability is a chief concern in this codebase. Both `ruff` and `ty` have
IDE extensions that work very well.

## Rule 7: Vulnerability Scanning
All releases must pass [trivy](https://trivy.dev/) scans before they will be published. 
Should you discover a critical vulnerability, you are encouraged to report it via
<admin@apexnorthwest.com>. Should the maintainers fail to respond within 30 days,
you should open an issue on this repo. If an unpatched vulnerability is found in a
library or included binary, please open an issue with the appropriate patched version details.

## Rule 8: Use of AI tools
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
