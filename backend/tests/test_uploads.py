"""Upload validation and encrypted attachments."""
import base64

import pytest

from app.redis_client import redis_client

pytestmark = pytest.mark.asyncio

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


async def _clear_rate(user):
    await redis_client.delete(f"rl:upload:{user['id']}")


async def test_image_upload_and_inline_serving(client, alice):
    await _clear_rate(alice)
    res = await client.post(
        "/uploads",
        headers=alice["headers"],
        files={"file": ("photo.png", PNG, "image/png")},
    )
    assert res.status_code == 201, res.text
    att = res.json()
    assert att["is_image"] is True
    assert att["content_type"] == "image/png"

    served = await client.get(f"/uploads/{att['id']}")
    assert served.status_code == 200
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.headers["content-disposition"].startswith("inline")


async def test_documents_are_forced_to_download(client, alice):
    await _clear_rate(alice)
    res = await client.post(
        "/uploads",
        headers=alice["headers"],
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 201
    served = await client.get(f"/uploads/{res.json()['id']}")
    assert served.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize(
    "name", ["evil.exe", "shell.php", "x.html", "a.svg", "noext", ".htaccess"]
)
async def test_dangerous_types_rejected(client, alice, name):
    await _clear_rate(alice)
    res = await client.post(
        "/uploads",
        headers=alice["headers"],
        files={"file": (name, b"data", "application/octet-stream")},
    )
    assert res.status_code == 415, f"{name} was accepted"


async def test_content_type_is_derived_from_extension_not_client(client, alice):
    """A lying Content-Type must not decide how the file is served — that's
    what would turn an upload into stored XSS."""
    await _clear_rate(alice)
    res = await client.post(
        "/uploads",
        headers=alice["headers"],
        files={"file": ("x.png", b"<script>alert(1)</script>", "text/html")},
    )
    assert res.status_code == 201
    assert res.json()["content_type"] == "image/png"


async def test_upload_rate_limited(client, alice):
    await _clear_rate(alice)
    codes = []
    for i in range(7):
        r = await client.post(
            "/uploads",
            headers=alice["headers"],
            files={"file": (f"f{i}.png", PNG, "image/png")},
        )
        codes.append(r.status_code)
    assert 429 in codes, codes
    await _clear_rate(alice)


async def test_encrypted_upload_hides_name_and_type(client, alice):
    """The server must learn nothing about an encrypted attachment."""
    await _clear_rate(alice)
    res = await client.post(
        "/uploads",
        headers=alice["headers"],
        data={"encrypted": "true", "enc_meta": "BASE64_CIPHERTEXT_OF_NAME_AND_TYPE"},
        files={"file": ("blob.bin", b"\x00\x01ciphertext\x02", "application/octet-stream")},
    )
    assert res.status_code == 201, res.text
    att = res.json()
    assert att["encrypted"] is True
    assert att["name"] == "Encrypted file"          # real name never stored
    assert att["content_type"] == "application/octet-stream"
    assert att["is_image"] is False                  # never rendered inline
    assert att["enc_meta"] == "BASE64_CIPHERTEXT_OF_NAME_AND_TYPE"

    served = await client.get(f"/uploads/{att['id']}")
    assert served.status_code == 200
    assert served.content == b"\x00\x01ciphertext\x02"
    assert served.headers["content-disposition"].startswith("attachment")


async def test_malformed_upload_id_is_404_not_500(client):
    for bad in ["not-a-uuid", "' OR 1=1--", "../../etc/passwd"]:
        res = await client.get(f"/uploads/{bad}")
        assert res.status_code == 404, f"{bad} -> {res.status_code}"
