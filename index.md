# >>> image-sync-operator

To deploy image-sync-operator in your cluster, you should use Helm.

```sh
helm repo add apexnw oci://ghcr.io/apexnorthwest/charts
helm install image-sync-operator apexnw/image-sync-operator -n image-sync-opeator --create-namespace
```

Charts are also available over the old chart format at https://apexnorthwest.github.io/image-sync-operator/charts
