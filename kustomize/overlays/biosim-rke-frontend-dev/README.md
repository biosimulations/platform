# biosim-rke-frontend-dev

Frontend-only WIP-preview overlay on the **biosim-rke** cluster, in the **frontend-dev** namespace. Lets the frontend developer deploy any `frontend-vX.Y.Z` build to a shareable URL — useful for showing in-progress work to non-developers and for catching production-build SSR bugs that `npm run dev` hides. Backend traffic is sent over the public ingress to the RKE prod-tier API host (`api.biosim.cam.uchc.edu`).

For personal iteration, `npm run dev` against the deployed API is usually faster — this overlay is for the share-a-WIP / production-build-SSR-validation use case.

## Bootstrap (one-time, before first deploy)

1. **DNS** — `biosim-dev.cam.uchc.edu` needs to resolve to the RKE ingress IP (separate DNS ticket; on-premise zone is not self-service).
2. **Image pull secret** — sealed secrets are namespace-bound, so the `ghcr-secret` from `biosim-rke` does not apply here. Generate one for `frontend-dev`:
   ```bash
   kubectl create namespace frontend-dev
   kustomize/scripts/sealed_secret_ghcr.sh frontend-dev > secret-ghcr.yaml
   # then add `- secret-ghcr.yaml` to the resources list in kustomization.yaml
   ```
   (Not committed here because the encrypted blob is bound to the cluster's sealed-secrets controller key.)
3. **CORS** — the RKE prod-tier `api` allows this host via `CORS_EXTRA_ORIGINS` in `kustomize/config/biosim-rke/api.env`. If you rename or add a preview host, update that file (not this overlay) and re-apply `biosim-rke`.

## Deploy

Update the `frontend-0.1.0` image tag in `kustomization.yaml` to whichever `frontend-X.Y.Z` build you want, then:

```bash
export KUBECONFIG=<rke-cluster-kubeconfig>
kubectl kustomize . | kubectl apply -f -
```

## Promote dev → prod

The image is the same one prod can pull (`ghcr.io/biosimulations/platform-frontend:frontend-X.Y.Z`). To promote a tested build, just bump that same tag in `kustomize/overlays/biosim-rke/kustomization.yaml` (or whichever prod overlay applies) and apply.
