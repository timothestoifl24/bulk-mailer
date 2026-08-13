"""A throwaway SMTP sink for local testing.

Accepts everything, prints a one-line summary and drops the message (or writes
it to a directory with --out). Point the app's SMTP settings at
127.0.0.1:8025 with security "none" to watch a campaign run without sending
anything to real people.

    python tools/dev_smtp.py --port 8025 --out ./data/sent
"""

from __future__ import annotations

import argparse
import asyncio
import email
from datetime import datetime
from pathlib import Path

CRLF = b"\r\n"


class SmtpSink:
    def __init__(self, out_dir: Path | None) -> None:
        self.out_dir = out_dir
        self.count = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")

        async def send(line: str) -> None:
            writer.write(line.encode() + CRLF)
            await writer.drain()

        await send("220 dev-smtp-sink ready")
        sender = ""
        recipients: list[str] = []
        try:
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                command = raw.decode("utf-8", "replace").strip()
                upper = command.upper()

                if upper.startswith(("HELO", "EHLO")):
                    await send("250-dev-smtp-sink")
                    await send("250 8BITMIME")
                elif upper.startswith("MAIL FROM"):
                    sender = command.partition(":")[2].strip()
                    await send("250 OK")
                elif upper.startswith("RCPT TO"):
                    recipients.append(command.partition(":")[2].strip())
                    await send("250 OK")
                elif upper == "DATA":
                    await send("354 End data with <CR><LF>.<CR><LF>")
                    body = await self._read_data(reader)
                    self._store(sender, recipients, body)
                    await send("250 OK: queued")
                    sender, recipients = "", []
                elif upper == "RSET":
                    sender, recipients = "", []
                    await send("250 OK")
                elif upper == "NOOP":
                    await send("250 OK")
                elif upper == "QUIT":
                    await send("221 Bye")
                    break
                elif upper.startswith("AUTH"):
                    await send("235 Authentication successful")
                else:
                    await send("250 OK")
        except (ConnectionResetError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            print(f"  connection from {peer} closed", flush=True)

    async def _read_data(self, reader: asyncio.StreamReader) -> bytes:
        lines: list[bytes] = []
        while True:
            line = await reader.readline()
            if not line or line.strip() == b".":
                break
            lines.append(line[1:] if line.startswith(b"..") else line)
        return b"".join(lines)

    def _store(self, sender: str, recipients: list[str], body: bytes) -> None:
        self.count += 1
        message = email.message_from_bytes(body)
        subject = message.get("Subject", "(no subject)")
        print(
            f"[{self.count:04d}] {datetime.now():%H:%M:%S} {sender} -> {', '.join(recipients)} | {subject}",
            flush=True,
        )
        if self.out_dir is not None:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            path = self.out_dir / f"{self.count:05d}.eml"
            path.write_bytes(body)


async def main_async(host: str, port: int, out_dir: Path | None) -> None:
    sink = SmtpSink(out_dir)
    server = await asyncio.start_server(sink.handle, host, port)
    where = f" (saving to {out_dir})" if out_dir else ""
    print(f"Dev SMTP sink listening on {host}:{port}{where}. Ctrl+C to stop.", flush=True)
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8025)
    parser.add_argument("--out", type=Path, default=None, help="write each message as an .eml file")
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.host, args.port, args.out))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
