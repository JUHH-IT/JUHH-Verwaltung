"""Minimaler SMTP-Server nur für Tests: nimmt Nachrichten an (ohne Weiterleitung) und speichert sie.

Als Modul: server = FakeSMTP(password='geheim'); server.start(); … server.messages; server.stop()
Als Skript (für den Browser-Durchlauf): python3 tests/fake_smtp.py <Ordner> → schreibt Port in <Ordner>/port,
jede Nachricht als <Ordner>/<n>.eml.
"""
import base64
import socketserver
import sys
import threading
from pathlib import Path


class _Handler(socketserver.StreamRequestHandler):
    def reply(self, line):
        self.wfile.write((line + '\r\n').encode())

    def handle(self):
        srv = self.server.owner
        self.reply('220 test ESMTP')
        authed, sender, rcpts = False, None, []
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            cmd = line.split(' ', 1)[0].upper()
            if cmd in ('EHLO', 'HELO'):
                self.wfile.write(b'250-test\r\n250-AUTH PLAIN LOGIN\r\n250 8BITMIME\r\n')
            elif cmd == 'AUTH':
                parts = line.split()
                if parts[1].upper() == 'PLAIN':
                    data = parts[2] if len(parts) > 2 else (self.reply('334 ') or self.rfile.readline().decode().strip())
                    _, user, pw = base64.b64decode(data).decode('utf-8').split('\0')
                else:
                    self.reply('334 VXNlcm5hbWU6')
                    user = base64.b64decode(self.rfile.readline().strip()).decode()
                    self.reply('334 UGFzc3dvcmQ6')
                    pw = base64.b64decode(self.rfile.readline().strip()).decode()
                if user == srv.user and pw == srv.password:
                    authed = True
                    self.reply('235 ok')
                else:
                    srv.failed_logins += 1
                    self.reply('535 authentication failed')
            elif cmd == 'MAIL':
                if not authed:
                    self.reply('530 authentication required')
                    continue
                sender, rcpts = line[10:].strip('<> '), []
                self.reply('250 ok')
            elif cmd == 'RCPT':
                addr = line[8:].strip('<> ').split('>')[0]
                if addr in srv.reject:
                    self.reply('550 no such user')
                else:
                    rcpts.append(addr)
                    self.reply('250 ok')
            elif cmd == 'DATA':
                self.reply('354 go')
                body = []
                while True:
                    chunk = self.rfile.readline()
                    if chunk in (b'.\r\n', b'.\n', b''):
                        break
                    body.append(chunk[1:] if chunk.startswith(b'..') else chunk)
                srv.store(sender, rcpts, b''.join(body))
                if set(rcpts) & srv.drop_after_data:  # angenommen, aber Verbindung bricht vor der Bestätigung ab
                    return
                self.reply('250 queued')
            elif cmd == 'RSET':
                sender, rcpts = None, []
                self.reply('250 ok')
            elif cmd == 'NOOP':
                self.reply('250 ok')
            elif cmd == 'QUIT':
                self.reply('221 bye')
                return
            else:
                self.reply('502 not implemented')


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class FakeSMTP:
    def __init__(self, user='test@example.org', password='geheim', folder=None):
        self.user, self.password, self.folder = user, password, folder
        self.messages, self.reject, self.drop_after_data, self.failed_logins = [], set(), set(), 0
        self._server = _Server(('127.0.0.1', 0), _Handler)
        self._server.owner = self
        self.port = self._server.server_address[1]

    def store(self, sender, rcpts, data):
        self.messages.append({'from': sender, 'to': rcpts, 'data': data})
        if self.folder:
            (Path(self.folder) / f'{len(self.messages)}.eml').write_bytes(data)

    def start(self):
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()


if __name__ == '__main__':
    folder = Path(sys.argv[1])
    folder.mkdir(parents=True, exist_ok=True)
    server = FakeSMTP(folder=folder)
    (folder / 'port').write_text(str(server.port))
    server._server.serve_forever()
