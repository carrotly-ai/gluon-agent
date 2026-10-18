# Isolated container validation

This fixture builds the production Dockerfile and tests its installed runtime
without host accounts, credentials, volumes, published ports, or external runtime
network access. Do not combine it with the development or production Compose
files. The normal entrypoint starts Redis inside the owned container and drops
privileges. Uvicorn disables application lifespan tasks so health checks cannot
dispatch agents, schedules, or provider requests. This validates liveness and
packaging; it does not validate provider integrations or production deployment.

Create a new empty directory for the build context and export only these tracked
paths from the commit being tested:

```sh
git archive HEAD Dockerfile .dockerignore pyproject.toml uv.lock README.md LICENSE src web-ui docker-entrypoint.sh | tar -x -C "$GLUON_SMOKE_CONTEXT"
```

Set `GLUON_SMOKE_CONTEXT` to that absolute directory and `GLUON_SMOKE_IMAGE` to a
unique local image tag. Always use an owned project name and explicitly disable
automatic environment-file loading:

```sh
docker compose --env-file /dev/null -p gluon-owned-smoke -f tests/container/compose.yml build
docker compose --env-file /dev/null -p gluon-owned-smoke -f tests/container/compose.yml up -d --wait --wait-timeout 90
docker compose --env-file /dev/null -p gluon-owned-smoke -f tests/container/compose.yml exec -T --user gluon app gluon --help
docker compose --env-file /dev/null -p gluon-owned-smoke -f tests/container/compose.yml exec -T --user gluon app python < tests/container/smoke.py
docker compose --env-file /dev/null -p gluon-owned-smoke -f tests/container/compose.yml exec -T --user gluon app uv pip check
```

The smoke checks HTTP health, the built dashboard, the main process UID, and the
six critical dependency boundaries using fictional input and generated in-memory
keys. It also verifies that the installer rejects a stale lock in a separate,
dependency-free fictional project, without network access or changing `/app`.
Its final JSON line is the actual installed Python inventory. Compare it
with the Linux/Python-specific `uv export --locked --extra all --no-dev` graph,
then audit every installed third-party package using exact versions. Record OS
and global CLI packages separately; a Python audit does not cover those layers.
Only the owned project may be redeployed during validation. Preserve other
containers, listeners, workers, and prior fixtures.
