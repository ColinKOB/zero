"""The Mac home's door to the Wi-Fi: TLS on the LAN port, passed through to the hub on 127.0.0.1.

    tls_proxy.py --listen 0.0.0.0:PORT --to 127.0.0.1:HUB --cert CERT --key KEY

The hub itself listens only on 127.0.0.1 (the Mac app talks to it there), so nothing on the Wi-Fi can reach it
without TLS: a plain http request to the LAN port fails the handshake and is never answered. The certificate is the
self-signed one zero-engine made at install; the phone pins its SHA-256 fingerprint from the pairing QR code. Bytes
are passed through as they are, so streamed replies and WebSockets work unchanged. Standard library only.
"""

import argparse
import asyncio
import ssl


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--listen", required=True)
    ap.add_argument("--to", required=True)
    ap.add_argument("--cert", required=True)
    ap.add_argument("--key", required=True)
    a = ap.parse_args()
    lhost, lport = a.listen.rsplit(":", 1)
    thost, tport = a.to.rsplit(":", 1)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(a.cert, a.key)

    async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        try:
            ur, uw = await asyncio.open_connection(thost, int(tport))
        except OSError:
            cw.close()
            return
        await asyncio.gather(pipe(cr, uw), pipe(ur, cw))

    async def serve() -> None:
        server = await asyncio.start_server(handle, lhost, int(lport), ssl=ctx, ssl_handshake_timeout=15)
        async with server:
            await server.serve_forever()

    asyncio.run(serve())


if __name__ == "__main__":
    main()
