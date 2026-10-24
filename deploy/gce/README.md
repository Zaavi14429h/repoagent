# Google Cloud deployment

The reference deployment uses one dedicated Compute Engine VM so the API and ADK
services can share the isolated run workspace. Use a dedicated project/VM because
RepoAgent executes untrusted repository tests.

1. Create an Ubuntu VM with at least 4 vCPU, 8 GB RAM, a 40 GB disk, and no public
   service-account scopes beyond logging/monitoring.
2. Install Docker Engine and the Compose plugin.
3. Clone this repository to `/opt/repoagent`.
4. Copy `.env.example` to `.env` and store production secrets in Google Secret
   Manager. Materialize them only at service startup.
5. Build the restricted runner images:

   ```bash
   docker build -f runner/python.Dockerfile -t repoagent-runner-python:latest .
   docker build -f runner/node.Dockerfile -t repoagent-runner-node:latest .
   ```

6. For a production runner, run the API and ADK service directly under a dedicated
   unprivileged system user with rootless Docker, set `REPOAGENT_SANDBOX_MODE=docker`,
   and expose only the dashboard/API through an HTTPS load balancer. Do not mount a
   rootful Docker socket into a public web container.
7. Start the control plane with `docker compose up -d` for the classroom demo.

Firewall rules should expose 443 only. Keep ports 8000 and 8100 private. Put Identity-
Aware Proxy or another OIDC proxy in front of the dashboard and API.

