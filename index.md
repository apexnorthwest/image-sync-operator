# image-sync-operator
image-sync-operator is a Kubernetes operator that synchronizes container images between registries based on the ImageSync custom resource definition (CRD).

Installing the operator is typically done via helm by pulling the chart from the public oci repository.
You can also find the charts in the legacy helm registry format [here](charts/).

Crate documentation for contributors can be found [here](doc/image_sync_operator/)

All release images are signed with Cosign, available [here](https://github.com/sigstore/cosign)

## Release Security

Github released helm chart archives are signed with pgp, key 7C3165A81E6E0F5452473731536EB5AC1BB52D60, which is published on the ubuntu key server and in this repo as `releases.asc`.

Container images are signed with cosign's public key which is in `cosign.pub` in this repo.

If you want to verify the chart and image:

```sh
docker pull ghcr.io/apexnorthwest/image-sync-operator:0.2.1
curl -LO https://raw.githubusercontent.com/apexnorthwest/image-sync-operator/refs/heads/main/cosign.pub
cosign verify --key cosign.pub ghcr.io/apexnorthwest/image-sync-operator:0.2.1

gpg --receive-keys 7C3165A81E6E0F5452473731536EB5AC1BB52D60
curl -LO https://apexnorthwest.github.io/image-sync-operator/charts/image-sync-operator-0.2.1.tgz
curl -LO https://apexnorthwest.github.io/image-sync-operator/charts/image-sync-operator-0.2.1.tgz.sig
gpg --verify image-sync-operator-0.2.1.tgz.sig
```

You're looking for these two bits of information:
```
Verification for ghcr.io/apexnorthwest/image-sync-operator:0.2.1 --
The following checks were performed on each of these signatures:
  - The cosign claims were validated
  - Existence of the claims in the transparency log was verified offline
  - The signatures were verified against the specified public key
```

```
gpg: assuming signed data in 'image-sync-operator-0.2.1.tgz'
gpg: Signature made Fri 27 Nov 2026 08:31:51 PM MST
gpg:                using RSA key 7C3165A81E6E0F5452473731536EB5AC1BB52D60
gpg: Good signature from "Apex Northwest Releases (Release signing key) <admin@apexnorthwest.com>" [unknown]
```

GPG will complain the key isn't cross-trusted but that's normal in this case.

## Installing

To install the operator in single-namespace mode with the default settings you would do the following:
```sh
helm repo add apexnw https://apexnorthwest.github.io/image-sync-operator/charts
helm install image-sync-operator apexnw/image-sync-operator -n image-sync-operator --create-namespace
```

Note: This chart by default will require cluster admin level permissions to install the CRD file.

To fully customize the configuration you would pass a values.yaml files like so:
```yaml
---
# Settings for the operator itself. This is the container that runs the operator code and watches for ImageSync CRs.
operator:
  # Image url to pull from
  image: 
    repository: "ghcr.io/apexnorthwest/image-sync-operator"
    tag: "0.2.1"
    pullPolicy: "IfNotPresent"
  # Enable for debug logging. This is not recommended for production use but if you even need to open an issue
  # on the project then we will expect you to provide logs with this enabled.
  debug: false
service_account:
  # Should this chart create a service account?
  # If false, the service account named below must already exist
  create: true
  # Name of the service account to use/create
  # Defaults to release name if not set
  name: ""
# Settings related to the skopeo image used by the operator
# Technically, any image that contains skopeo can be used, but we prefer the official one
# Notably, the ca trust must be in /etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem, so
# if you use an image based on a different OS, it's likely it will have tls problems.
skopeo:
  # Image url to pull from
  image:
    repository: "quay.io/skopeo/stable"
    tag: "latest"
    pullPolicy: "IfNotPresent"
  # Optional CA trust bundle to use when verifying TLS connections to registries
  # This should be a PEM encoded bundle of CA certificates, and will be appended
  # to the system CA trust bundle in the skopeo container. This is generally mandatory when
  # working with private, authenticated registries.
  ca_trust_bundle: |-
    -----BEGIN CERTIFICATE-----
    ***********omitted***********
    -----END CERTIFICATE-----
# Settings related to the operator's access
rbac:
  # Should this chart create the cluster role `image-sync-operator`?
  # This must be set to true if cluster_scoped is true, otherwise it is optional.
  create_cluster_role: false
  # Will this operator need to be cluster scoped?
  # If true, the operator will watch all namespaces and require cluster role permissions.
  cluster_scoped: false
  # When not in cluster scoped mode, which namespaces should the operator watch?
  # If empty, the operator will watch the namespace it is deployed in only.
  # This will be ignored if cluster_scoped is true.
  # The operator service account will need to have a rolebinding in these namespaces to be able to watch for ImageSync CRs.
  # The Role can be copied from the install namespace, or if create_cluster_role is true then you can bind that cluster role into the namespace.
  watched_namespaces:
    - "image-sync-operator"
    - "default"
    - "some-other-application"
```

## Using the Operator

You would then create ImageSync CRs in that same namespace (as in this example we've installed the operator in restricted mode, as is the default)

The following is an example of an ImageSync:
```yaml
---
apiVersion: imagesync.apexnw.dev/v1alpha1
kind: ImageSync
metadata:
  name: alpine-latest
  namespace: image-sync-operator
spec:
  source:
    # Full URI of the source image as would be passed to skopeo (minus the docker:// prefix, which is presumed)
    # Note: You cannot use docker short names for images. You must use full docker.io paths, as skopeo cannot know what registry to use for short names. This applies to both source and dest.
    # This can also be an @ hash reference. Destination however, cannot be as you must tag the pushed image to something.
    image: "docker.io/library/alpine:latest"
    # Optional: The name of a Secret in the same namespace of type kubernetes.io/dockerconfigjson used to authenticate to the source registry.
    registryLoginSecret: "dockerhub"
  destination:
    # Full URI of the destination image as would be passed to skopeo (minus the docker:// prefix, which is presumed)
    image: "my-private-registry.example.com/library/alpine:latest"
    # Optional: The name of a Secret in the same namespace of type kubernetes.io/dockerconfigjson used to authenticate to the destination registry.
    registryLoginSecret: "private-registry"
  # Optional: If you want to rerun the sync on a schedule after the initial sync. Uses CronJob format. If unset, the sync only runs once. 
  cronSchedule: "*/15 * * * *"
  # Optional: If true, all architectures will be copied. If false, only the architecture of the operator pod will be copied. Defaults to false. This is the same as passing --all to skopeo.
  allArchitectures: true
  # Optional: If true, the destination image will be tagged with the digest of the source image. If false, the destination image will be tagged with the tag of the source image. Defaults to false. This is the same as passing --preserve-digests to skopeo
  # Notably, if this is set to true, and allArchitectures is set to false, multi-arch images will fail to copy and the sync will fail.
  preserveDigests: true
  # Optional: If you want to pass extra arguments to skopeo, you can do so here. This is a string that will be split on whitespace and passed to skopeo as-is. For example, if you want to pass --insecure-policy to skopeo, you would set this to "--insecure-policy".
  # Note that this is not a list of arguments, but a single string that will be split on whitespace. If it needs to have spaces in an argument (which it shouldn't), you will need to use single quotes inside the string.
  extraSkopeoArguments: ""
```

This project is not meant to be used as a library or installed via cargo. As a result, all contained functions and modules documented within this site are 
written for the use of contributors and not users of the operator. 