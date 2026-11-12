# Changelog of releases (newest at the top)

## Release 0.2.0
Full rewrite of the test suite and extensive rewrite of operator code due to the many bugs
identified as part of that process. This version is far more stable than previous iterations.

### Key Changes
- **Three comprehensive test suites**: KMock (regression), Kind (integration), and KWOK (performance)
- **Unified error handling**: Single error type (`OpError`) for more stable error propagation
- **Bug fixes**: Improved Job/CronJob cleanup, better status updates, correct resource name handling

### Testing
We now have three test suites:
- **KMock** for rapid regression analysis (runs in CI and local development)
- **Kind** for testing against actual Kubernetes (run before releases)
- **KWOK** for performance regression testing (run before releases)

### Bug Fixes
- Fixed incorrect Job/CronJob resource naming (was using CR name directly, now uses `imagesync-<basename>` pattern)
- Fixed status updates to use passed-in client instead of creating new one
- Improved Job existence checking (now properly handles 404 vs other errors)
- Added proper owner references to Jobs for correct garbage collection
- Fixed spec change detection logic

### Upgrade Notes
This release contains no API or end-user changes and is safe to upgrade from any previous version.
We strongly recommend upgrading to benefit from the improved stability and test coverage.

However, you should note that the fix to cleanup behavior is not retroactive. Jobs and CronJobs
created by older versions of the operator will still not be cleaned up due to missing owner references.
Searching your cluster for any `imagesync-*` Jobs or CronJobs with no owner references will identify
the affected objects.

## Release 0.1.1
Initial release (should not be used). Testing and CI/CD pipelines were not yet established.
Please use version 0.2.0 or later. Many of the bugs fixed in that version were severe.

This version would not properly removed old resources when ImageSyncs were deleted.