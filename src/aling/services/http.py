import httpx


async def read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    """Reject compression before httpx decoding; cap uncompressed streaming bytes."""
    if response.headers.get("content-encoding", "identity").lower() != "identity":
        raise ValueError("Compressed responses are disabled")
    body = bytearray()
    async for chunk in response.aiter_bytes(chunk_size=8192):
        if len(body) + len(chunk) > max_bytes:
            raise ValueError("Response exceeds size limit")
        body.extend(chunk)
    return bytes(body)
