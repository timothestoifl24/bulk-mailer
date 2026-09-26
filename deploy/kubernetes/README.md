# Kubernetes manifests

[kustomize](https://kustomize.io/) layouts for Bulk Mailer. `kubectl` has
kustomize built in.

| Directory | What it deploys |
| --- | --- |
| [`base/`](base/) | One pod, SQLite on a persistent volume, in the namespace `bulk-mailer` |
| [`overlays/postgres/`](overlays/postgres/) | The base plus a PostgreSQL StatefulSet; the starting point for more than one replica |

```bash
cp base/secret.env.example base/secret.env   # fill in SECRET_KEY and ADMIN_PASSWORD
$EDITOR base/config.env                      # PUBLIC_BASE_URL and SMTP
kubectl apply -k base                        # or: kubectl apply -k overlays/postgres
```

The full guide - PostgreSQL, Ingress, administration, upgrades, backups,
scaling out, troubleshooting - is at
[bulkmailer.stoifl.app/kubernetes](https://bulkmailer.stoifl.app/kubernetes)
([source](../../docs/kubernetes.md)).
