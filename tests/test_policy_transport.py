"""Temporary rule delivery stays local, exact-path only, and closes reliably."""
import http.client
from pathlib import Path
import socket
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'macos'))
import policy_transport as transport


class PolicyTransportTests(unittest.TestCase):
    def test_serves_only_expected_path_and_closes_after_transaction(self):
        with transport.serve(0, {'/synthetic-secret/direct.yaml': b'payload:\n'}) as server:
            port = server.server_address[1]
            self.assertEqual(server.server_address[0], '127.0.0.1')
            for path, expected in [('/', 404), ('/wrong/direct.yaml', 404),
                                   ('/synthetic-secret/../direct.yaml', 404),
                                   ('/synthetic-secret/direct.yaml', 200)]:
                client = http.client.HTTPConnection('127.0.0.1', port)
                client.request('GET', path)
                response = client.getresponse()
                self.assertEqual(response.status, expected)
                body = response.read()
                if expected == 200:
                    self.assertEqual(body, b'payload:\n')
                client.close()
            self.assertEqual(server.received, {'/synthetic-secret/direct.yaml'})
        with self.assertRaises(OSError):
            socket.create_connection(('127.0.0.1', port), timeout=.2)

    def test_occupied_port_does_not_select_another_port(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0)); occupied.listen()
            with self.assertRaises(OSError):
                with transport.serve(occupied.getsockname()[1], {}):
                    self.fail('must not enter the transaction')

    def test_exception_releases_port(self):
        with self.assertRaisesRegex(RuntimeError, 'synthetic'):
            with transport.serve(0, {}) as server:
                port = server.server_address[1]
                raise RuntimeError('synthetic')
        with transport.serve(port, {}) as server:
            self.assertEqual(server.server_address[1], port)


if __name__ == '__main__':
    unittest.main()
