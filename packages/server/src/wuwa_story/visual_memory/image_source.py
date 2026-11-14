"""Temporary query images: HTTPS downloads with a pinned, public DNS address."""

import asyncio
import ipaddress
import socket

import httpx

MAX_QUERY_BYTES = 5 * 1024 * 1024


class _ImageTransport(httpx.AsyncHTTPTransport):
    async def handle_async_request(self, request):
        try:
            return await super().handle_async_request(request)
        finally:
            # AsyncClient logs request.url after transport returns. Do not log
            # signed query parameters or tokens embedded in image paths.
            request.url = request.url.copy_with(path="/", query=None)


async def public_address(url: httpx.URL) -> str:
    if (url.scheme != "https" or not url.host or url.userinfo
            or url.port not in (None, 443) or "%" in url.host):
        raise ValueError(
            "Image URL must be public HTTPS on port 443, without credentials")
    addresses = await asyncio.get_running_loop().getaddrinfo(
        url.host, 443, type=socket.SOCK_STREAM,
    )
    ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
    # Reject mixed public/private DNS answers too. Pin the actual connection below;
    # a second resolver lookup must not bypass this check (DNS rebinding).
    if not ips or any(not ip.is_global or ip.is_multicast
                      or isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped
                      for ip in ips):
        raise ValueError("Image URL must resolve only to public addresses")
    return str(ips[0])


async def download_image(value: str) -> bytes:
    try:
        async with asyncio.timeout(20):
            url = httpx.URL(value)
            async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False,
                                         transport=_ImageTransport(trust_env=False)) as client:
                for hop in range(4):
                    address = await public_address(url)
                    # Keep TLS certificate validation/SNI and HTTP Host on the original
                    # hostname, while connecting exclusively to the checked IP.
                    target = url.copy_with(host=address, fragment=None)
                    async with client.stream(
                        "GET", target, headers={"Host": url.netloc.decode("ascii"),
                                                "Accept": "image/*", "Accept-Encoding": "identity"},
                        extensions={"sni_hostname": url.host},
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get("location")
                            if not location or hop == 3:
                                raise ValueError("Too many image redirects")
                            url = url.join(location)
                            continue
                        response.raise_for_status()
                        if response.headers.get("content-encoding", "identity").strip().lower() not in ("", "identity"):
                            raise ValueError("Compressed HTTP image responses are unsupported")
                        content_type = response.headers.get(
                            "content-type", "").split(";")[0].strip().lower()
                        if content_type not in ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "application/octet-stream"):
                            raise ValueError("URL must return an image")
                        size = response.headers.get("content-length")
                        if size and (not size.isdecimal() or int(size) > MAX_QUERY_BYTES):
                            raise ValueError("Image exceeds 5 MiB")
                        result = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            result.extend(chunk)
                            if len(result) > MAX_QUERY_BYTES:
                                raise ValueError("Image exceeds 5 MiB")
                        from wuwa_story.visual_memory.images import load_image
                        await asyncio.to_thread(load_image, bytes(result), max_bytes=MAX_QUERY_BYTES)
                        return bytes(result)
    except (httpx.HTTPError, httpx.InvalidURL, OSError, TimeoutError) as exc:
        # Signed URL query strings and response bodies must not leak into tool output.
        raise ValueError("Image URL could not be loaded") from exc
    raise ValueError("Image URL could not be loaded")
