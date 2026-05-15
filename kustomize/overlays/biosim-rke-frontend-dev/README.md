# biosim-rke-frontend-dev

Frontend-only overlay on the **biosim-rke** cluster, in the **frontend-dev** namespace. Lets the frontend developer deploy any `frontend-vX.Y.Z` build for testing without touching prod. Backend traffic is sent over the public ingress to the production API host (`api.biosim.biosimulations.org`).

## Bootstrap (one-time, before first deploy)

1. **DNS** — point `biosim-dev.biosimulations.org` at the RKE ingress IP.
2. **Image pull secret** — sealed secrets are namespace-bound, so the `ghcr-secret` from `biosim-rke` does not apply here. Generate one for `frontend-dev`:
   ```bash
   kubectl create namespace frontend-dev
   kustomize/scripts/sealed_secret_ghcr.sh frontend-dev > secret-ghcr.yaml
   # then add `- secret-ghcr.yaml` to the resources list in kustomization.yaml
   ```
   (Not committed here because the encrypted blob is bound to the cluster's sealed-secrets controller key.)

## Deploy

Update the `frontend-0.1.0` image tag in `kustomization.yaml` to whichever `frontend-X.Y.Z` build you want, then:

```bash
export KUBECONFIG=<rke-cluster-kubeconfig>
kubectl kustomize . | kubectl apply -f -
```

## Promote dev → prod

The image is the same one prod can pull (`ghcr.io/biosimulations/platform-frontend:frontend-X.Y.Z`). To promote a tested build, just bump that same tag in `kustomize/overlays/biosim-rke/kustomization.yaml` (or whichever prod overlay applies) and apply.
