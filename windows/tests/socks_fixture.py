"""Isolated SOCKS5 endpoint: answer locally, never connect to the requested destination."""
import asyncio
import struct

HTTP = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\nConnection: close\r\n\r\nPROXY"
async def handle(reader, writer):
    try:
        version, count = await reader.readexactly(2)
        assert version == 5
        await reader.readexactly(count)
        writer.write(b"\x05\x00")
        await writer.drain()
        _, command, _, atype = await reader.readexactly(4)
        count = {1:4, 4:16}.get(atype)
        if count is None:
            count = (await reader.readexactly(1))[0]
        await reader.readexactly(count + 2)
        writer.write(b"\x05\x00\x00\x01\x7f\x00\x00\x01" + struct.pack("!H",29183))
        await writer.drain()
        if command == 3:
            await reader.read()
        elif command == 1:
            first = await reader.readexactly(1)
            if first == b"G":
                await reader.readuntil(b"\r\n\r\n")
                writer.write(HTTP)
            else:
                writer.write(first)
                while chunk := await reader.read(4096):
                    writer.write(chunk)
                    await writer.drain()
            await writer.drain()
    except (OSError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()

class Echo(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport
    def datagram_received(self, packet, source):
        self.transport.sendto(packet, source)

async def main():
    loop = asyncio.get_running_loop()
    await loop.create_datagram_endpoint(Echo, local_addr=("127.0.0.1",29183))
    async def direct(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        writer.write(HTTP.replace(b"PROXY",b"LOCAL"))
        await writer.drain()
        writer.close()
    await asyncio.start_server(direct, "127.0.0.1", 29184)
    server = await asyncio.start_server(handle, "127.0.0.1",29182)
    await server.serve_forever()
asyncio.run(main())
