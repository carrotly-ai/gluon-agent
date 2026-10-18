"""Offline production-image checks; run with Compose exec, without installing pytest."""

import asyncio
import json
import os
import ssl
import subprocess
import tempfile
from importlib.metadata import distributions
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.request import urlopen

import jwt
from anyio.streams.tls import TLSStream
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

assert os.getuid() == 1000, "Entrypoint must drop privileges to the gluon user"
pid1_status = Path("/proc/1/status").read_text().splitlines()
assert next(line for line in pid1_status if line.startswith("Uid:")).split()[1:] == ["1000"] * 4
with urlopen("http://127.0.0.1:45866/api/health", timeout=5) as response:
    assert response.status == 200
    assert json.load(response) == {"status": "ok"}
with urlopen("http://127.0.0.1:45866/", timeout=5) as response:
    assert response.status == 200
    assert '<div id="root">' in response.read().decode()
assert list(Path("/app/src/gluon/web/dist/assets").glob("*.js"))

public_key = ec.generate_private_key(ec.SECP256R1()).public_key()
original = public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
for pem in (
    original.replace(b"\n", b"\r"),
    original.replace(b"-----END", b"\t-----END"),
    b" ".join(original.splitlines()),
):
    serialization.load_pem_public_key(pem)
    try:
        jwt.encode({"sub": "fictional-user"}, pem, algorithm="HS256")
    except jwt.InvalidKeyError:
        pass
    else:
        raise AssertionError("Asymmetric key accepted as an HMAC secret")


async def check_tls():
    context = ssl.create_default_context()
    with (
        patch.object(ssl.SSLContext, "wrap_bio", return_value=MagicMock()) as wrap_bio,
        patch.object(TLSStream, "_call_sslobject_method", new_callable=AsyncMock),
    ):
        await TLSStream.wrap(MagicMock(), hostname="faß.example", ssl_context=context)
    assert wrap_bio.call_args.kwargs["server_hostname"] == b"xn--fa-hia.example"


asyncio.run(check_tls())
for session_id in ("--version", "fictional-session"):
    transport = SubprocessCLITransport(prompt="fictional prompt", options=ClaudeAgentOptions(resume=session_id))
    transport._cli_path = "/fictional/claude"
    command = transport._build_command()
    assert f"--resume={session_id}" in command
    assert session_id not in command
    assert "--resume" not in command

# Prove the image's installer refuses stale metadata without modifying /app or
# its running environment. Preserve the owned fixture for inspection.
stale_project = Path(tempfile.mkdtemp(prefix="gluon-smoke-stale-"))
# A dependency-free fictional project needs no cached registry metadata offline.
(stale_project / "pyproject.toml").write_text(
    '[project]\nname = "fictional-lock-check"\nversion = "1.0.1"\nrequires-python = ">=3.12"\ndependencies = []\n'
)
(stale_project / "uv.lock").write_text(
    'version = 1\nrevision = 3\nrequires-python = ">=3.12"\n'
    '[[package]]\nname = "fictional-lock-check"\nversion = "1.0.0"\nsource = { virtual = "." }\n'
)
result = subprocess.run(
    [
        "uv",
        "sync",
        "--locked",
        "--offline",
        "--no-dev",
        "--no-install-project",
        "--project",
        str(stale_project),
    ],
    env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(stale_project / "venv")},
    capture_output=True,
    text=True,
    check=False,
)
assert result.returncode != 0, "Stale manifest must fail a locked install"
assert "lockfile" in result.stderr and "needs to be updated" in result.stderr, result.stderr

print(
    json.dumps(
        {"health": "ok", "web_assets": "ok", "dependency_boundaries": 6, "stale_lock": "rejected", "uid": os.getuid()}
    )
)
print(
    json.dumps(
        sorted(
            ({"name": d.metadata["Name"], "version": d.version} for d in distributions()),
            key=lambda d: d["name"].lower(),
        )
    )
)
