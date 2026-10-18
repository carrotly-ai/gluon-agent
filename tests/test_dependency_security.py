"""Exercise critical dependency boundaries without providers or network access."""

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from anyio.streams.tls import TLSStream
from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


@pytest.mark.parametrize("mutation", ["cr", "indented_end", "single_line"])
def test_pyjwt_rejects_asymmetric_public_keys_as_hmac_secrets(mutation):
    public_key = ec.generate_private_key(ec.SECP256R1()).public_key()
    pem = public_key.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
    if mutation == "cr":
        pem = pem.replace(b"\n", b"\r")
    elif mutation == "indented_end":
        pem = pem.replace(b"-----END", b"\t-----END")
    else:
        pem = b" ".join(pem.splitlines())

    # Prove this is still a usable asymmetric key, rather than malformed input.
    serialization.load_pem_public_key(pem)
    with pytest.raises(jwt.InvalidKeyError):
        jwt.encode({"sub": "fictional-user"}, pem, algorithm="HS256")


@pytest.mark.asyncio
async def test_anyio_tls_preserves_idna2008_hostname():
    context = ssl.create_default_context()
    ssl_object = MagicMock()
    with (
        patch.object(ssl.SSLContext, "wrap_bio", return_value=ssl_object) as wrap_bio,
        patch.object(TLSStream, "_call_sslobject_method", new_callable=AsyncMock),
    ):
        await TLSStream.wrap(MagicMock(), hostname="faß.example", ssl_context=context)

    # IDNA 2003 would turn ß into ss and authenticate the wrong domain.
    assert wrap_bio.call_args.kwargs["server_hostname"] == b"xn--fa-hia.example"


@pytest.mark.parametrize("session_id", ["--version", "fictional-session"])
def test_sdk_binds_resume_value_to_its_option(session_id):
    transport = SubprocessCLITransport(prompt="fictional prompt", options=ClaudeAgentOptions(resume=session_id))
    # Build argv only: do not resolve or launch a real CLI/provider.
    transport._cli_path = "/fictional/claude"
    command = transport._build_command()
    assert f"--resume={session_id}" in command
    assert session_id not in command
    assert "--resume" not in command
