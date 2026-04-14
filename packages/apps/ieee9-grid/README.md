# ieee9-grid — Cozystack external app

Bridge chart registering the `ieee9-grid` simulator with Cozystack. Renders a
Flux `HelmRepository` pointing at `oci://ghcr.io/begonia-uc3/charts` and a
`HelmRelease` that pulls the [`ieee9-grid`](../../../charts/ieee9-grid) chart.

## Install via Cozystack

Register this bridge package (see `cozystack/external-apps-example` for the
general pattern), then create a release:

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: Application
metadata:
  name: grid
  namespace: tenant-example
spec:
  type: ieee9-grid
  values:
    chartVersion: "0.1.0"
    replicas: 1
    ingressEnabled: false
```

## Values

| Key                | Default                | Description                                          |
| ------------------ | ---------------------- | ---------------------------------------------------- |
| `chartVersion`     | `0.1.0`                | Version of the `ieee9-grid` chart to deploy.         |
| `imageTag`         | `""`                   | Override image tag (defaults to chart `appVersion`). |
| `replicas`         | `1`                    | Replica count applied to each service.               |
| `ingressEnabled`   | `false`                | Expose `grid-central` via Ingress.                   |
| `ingressHost`      | `ieee9.example.com`    | Ingress host when enabled.                           |
| `ingressClassName` | `""`                   | Optional `IngressClass`.                             |

For the full set of knobs the underlying chart exposes, see
[`charts/ieee9-grid/values.yaml`](../../../charts/ieee9-grid/values.yaml).
