# >>> Image Sync Operator
[![OpenSSF Scorecard](https://img.shields.io/ossf-scorecard/github.com/apexnorthwest/image-sync-operator?label=openssf+scorecard&style=flat)](https://scorecard.dev/viewer/?uri=github.com/apexnorthwest/image-sync-operator) [![OpenSSF Best Practices](https://www.bestpractices.dev/projects/13927/badge)](https://www.bestpractices.dev/projects/13927) [![Main Branch Build](https://github.com/apexnorthwest/image-sync-operator/actions/workflows/devbuild.yaml/badge.svg)](https://github.com/apexnorthwest/image-sync-operator/actions/workflows/devbuild.yaml) [![CodeQL](https://github.com/apexnorthwest/image-sync-operator/actions/workflows/github-code-scanning/codeql/badge.svg)](https://github.com/apexnorthwest/image-sync-operator/actions/workflows/github-code-scanning/codeql)
 [![Code-Scans](https://github.com/apexnorthwest/image-sync-operator/actions/workflows/codescans.yaml/badge.svg)](https://github.com/apexnorthwest/image-sync-operator/actions/workflows/codescans.yaml) ![GitHub Release](https://img.shields.io/github/v/release/apexnorthwest/image-sync-operator)


This Kubernetes operator is designed with one purpose: To copy container images
from one repository to another, either on demand or on a schedule.

To accomplish this, the operator creates Jobs and CronJobs that leverage Skopeo to perform image replication tasks. A simple Custom Resource is used to configure each sync rule.

The operator fully supports cluster-scoped, namespace-scoped, and multi-namespace-scoped modes. The operator only permits one instance to run in a given namespace to avoid collisions.

## Usage
This project is still a work in progress, and should be considered pre-alpha. Until this
notice is removed, we do not suggest using this code for anything other than experimental
purposes.

TODO: Write an actual user guide

## Building
TODO: write some how-to build guides

## Bug Reports and Feature Requests
We welcome all feedback and bug reports. All such reports and requests should be created in the Issues on this repository.

You should include the following information in all bug reports or support requests:
- Version of the operator image you're using (hash or tag)
- All logs from the operator pod as well as any Job pods related to the issue.  
  Ideally, you will enable `RUST_BACKTRACE=full` in the env vars of the pods. You may have to manually edit the pods to enable it.
- Detailed steps of how to reproduce the issue. This includes the operator config as passed to your helm chart as well as the ImageSync that produces the error. Please sanitize any sensitive or private information.

For all feature requests, please provide the version of the operator you're currently using and an example of what you'd like the feature to look like once implemented.

For example, if you wanted to have an extra field on the CR to perform some extra task, then you should show what that field would look like.

## Contributing
You may find our [code of conduct](CODE_OF_CONDUCT.md) and [contributor guidelines](CONTRIBUTING.md)
in this repo. We expect everyone to follow the guidelines and policies to ensure a positive,
productive, and enjoyable experience for all involved.

## License
Copyright 2026 Apex Northwest

```
Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```