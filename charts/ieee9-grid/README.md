# ieee9-grid

Helm chart deploying the IEEE 9-Bus grid simulator: a central power-flow engine
and three asset simulators (diesel generator, battery BESS, data center) as
cooperating microservices.

## Install

```bash
helm install ieee9 oci://ghcr.io/begonia-uc3/charts/ieee9-grid --version 0.1.0
```

Port-forward the central dashboard:

```bash
kubectl port-forward svc/grid-central 8000:8000
open http://localhost:8000
```

## Services

| Service        | Role                               | Default port |
| -------------- | ---------------------------------- | ------------ |
| grid-central   | Newton–Raphson power-flow engine   | 8000         |
| diesel-gen     | Diesel generator simulator (Bus 2) | 8000         |
| battery        | Battery BESS simulator (Bus 3)     | 8000         |
| datacenter     | Data-center load simulator (Bus 5) | 8000         |

All four services are enabled by default. Disable per service via
`services.<name>.enabled=false`.

## Values

See [`values.yaml`](./values.yaml). Key knobs:

- `image.registry` / `image.repository` / `image.tag` — image coordinates;
  each service image lives at `{registry}/{repository}/<service>`.
- `services.<name>.replicas` / `resources` / `env` — per-service overrides.
- `ingress.enabled` — expose services via an Ingress (see `hosts[].service`
  to route to a particular component).
- `podSecurityContext` / `securityContext` — hardening defaults (non-root,
  read-only rootfs, drop all capabilities); override if your workload needs
  to write outside `/tmp`.

## Upstream images

Images are built and published by this repository's CI to
`ghcr.io/begonia-uc3/ieee9-bus/<service>:<appVersion>`.
