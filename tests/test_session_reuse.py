"""Session reuse and local IPC tests use synthetic credentials only."""

import json
import os
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from passwords_bridge import broker, client
from passwords_bridge.browser import BridgeError
from passwords_bridge.session import CredentialLookupError


def request(mode='password', domain='example.com', username='alice', idle=300):
    return {'op': 'copy', 'mode': mode, 'domain': domain, 'username': username,
            'clear_after': 0, 'idle_seconds': idle}


class Clock:
    def __init__(self): self.now = 100.0
    def __call__(self): return self.now
    def advance(self, seconds): self.now += seconds


class Monitor:
    locked = False
    def blocked(self): return self.locked
    def close(self): pass


class FakeSession:
    instances = None
    def __init__(self, domain, username, mode, guard=None):
        self.guard, self.paired, self.pairs, self.closed = guard, False, 0, False
        self.scope = None
        self.on_read = None
        self.on_pair = None
        self.lookup_error = False
        self.states = 0
        if self.instances is not None: self.instances.append(self)
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def set_scope(self, domain, username, mode): self.scope = (domain, username, mode)
    def clear_scope(self): self.scope = None
    def state(self):
        self.states += 1
        return {'paired': self.paired}
    def pair(self):
        if self.on_pair: self.on_pair()
        self.pairs += 1
        self.paired = True
    def read(self, mode):
        if self.on_read: self.on_read()
        if self.lookup_error: raise CredentialLookupError('refine username')
        return 'FAKE-SECRET-' + mode


class ReuseTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.monitor, self.copies = Clock(), Monitor(), []
        self.instances = []
        self.factory = type('FixtureSession', (FakeSession,), {'instances': self.instances})
        self.manager = broker.SessionManager(self.monitor, self.factory,
            lambda secret, seconds: self.copies.append(secret), self.clock, self.clock)
    def tearDown(self): self.manager.close_session()

    def test_password_then_otp_pair_once_and_clear_scope(self):
        self.manager.copy(request())
        owned = self.instances[0]
        self.assertIsNone(owned.scope)
        self.clock.advance(1)
        result = self.manager.copy(request('otp', 'another.example', 'bob'))
        self.assertEqual(len(self.instances), 1)
        self.assertEqual(owned.pairs, 1)
        self.assertIsNone(owned.scope)
        self.assertNotIn('FAKE-SECRET', repr(result))
        self.assertEqual(len(self.copies), 2)

    def test_empty_or_ambiguous_lookup_keeps_pairing_for_retry(self):
        self.manager.copy(request())
        owned = self.instances[0]
        owned.lookup_error = True
        with self.assertRaises(CredentialLookupError): self.manager.copy(request())
        self.assertFalse(owned.closed)
        self.assertIsNone(owned.scope)
        owned.lookup_error = False
        self.manager.copy(request(username='bob'))
        self.assertEqual(owned.pairs, 1)

    def test_idle_expiry_closes_without_keepalive_extending_it(self):
        self.manager.copy(request(idle=30))
        for _ in range(5):
            self.clock.advance(5)
            self.manager.tick()
        self.clock.advance(5)
        with self.assertRaises(broker.SessionExpired): self.manager.tick()
        self.assertTrue(self.instances[0].closed)
        self.assertIsNone(self.manager.session)

    def test_valid_copy_refreshes_idle_deadline(self):
        self.manager.copy(request(idle=30))
        for _ in range(5):
            self.clock.advance(5); self.manager.tick()
        self.manager.copy(request('otp', idle=30))
        for _ in range(5):
            self.clock.advance(5); self.manager.tick()
        self.assertFalse(self.instances[0].closed)
        self.assertEqual(self.instances[0].pairs, 1)

    def test_maximum_lifetime_wins_despite_repeated_use(self):
        self.manager.maximum = 30
        self.manager.copy(request())
        for _ in range(5):
            self.clock.advance(5); self.manager.copy(request())
        self.clock.advance(5)
        with self.assertRaises(broker.SessionExpired): self.manager.tick()
        self.assertTrue(self.instances[0].closed)

    def test_lock_between_read_and_clipboard_prevents_delivery(self):
        self.manager.copy(request())
        self.copies.clear()
        self.instances[0].on_read = lambda: setattr(self.monitor, 'locked', True)
        with self.assertRaises(broker.SessionExpired): self.manager.copy(request())
        self.assertEqual(self.copies, [])
        self.assertTrue(self.instances[0].closed)

    def test_manual_lock_interrupts_read(self):
        self.manager.copy(request())
        self.copies.clear()
        self.instances[0].on_read = self.manager.cancelled.set
        with self.assertRaises(broker.SessionExpired): self.manager.copy(request())
        self.assertEqual(self.copies, [])
        self.assertTrue(self.instances[0].closed)

    def test_apple_invalidates_pairing_next_copy_pairs_again(self):
        self.manager.copy(request())
        self.instances[0].paired = False
        self.clock.advance(2)
        self.manager.tick()
        self.assertTrue(self.instances[0].closed)
        self.manager.copy(request('otp'))
        self.assertEqual(len(self.instances), 2)
        self.assertEqual(self.instances[1].pairs, 1)

    def test_sleep_or_clock_gap_closes_session(self):
        self.manager.copy(request())
        self.clock.advance(20)
        with self.assertRaises(broker.SessionExpired): self.manager.tick()
        self.assertTrue(self.instances[0].closed)

    def test_idle_expiry_does_not_interrupt_active_pairing(self):
        manager = self.manager
        class SlowPair(self.factory):
            def pair(self):
                for _ in range(40):
                    manager.clock.advance(1); manager.guard()
                super().pair()
        manager.factory = SlowPair
        manager.copy(request(idle=30))
        self.assertEqual(len(self.copies), 1)

    def test_invalid_request_never_creates_session(self):
        for changed in ({'mode': 'save'}, {'domain': '--eval'}, {'idle_seconds': 0}, {'clear_after': True}, {'script': 'bad'}):
            bad = dict(request(), **changed)
            with self.assertRaises(BridgeError): self.manager.copy(bad)
        self.assertEqual(self.instances, [])


@unittest.skipUnless(sys.platform == 'darwin', 'Darwin peer credentials required')
class BrokerIPCTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='pws-ipc-')
        self.root = Path(self.directory.name)
        self.ready, self.copies, self.instances = threading.Event(), [], []
        self.factory = type('FixtureSession', (FakeSession,), {'instances': self.instances})
        self.thread = threading.Thread(target=broker.serve, kwargs={
            'root': self.root, 'monitor_factory': Monitor, 'session_factory': self.factory,
            'copier': lambda value, seconds: self.copies.append(value), 'ready': self.ready})
        self.thread.start()
        self.assertTrue(self.ready.wait(5))
    def tearDown(self):
        try:
            if self.thread.is_alive():
                try: client.rpc({'op': 'stop'}, self.root)
                except client.Unavailable: pass  # listener closes before teardown finishes
        finally:
            self.thread.join(timeout=5)
            self.assertFalse(self.thread.is_alive())
            self.directory.cleanup()

    def test_real_socket_reuse_permissions_and_no_plaintext_reply(self):
        first = client.rpc(request(), self.root)
        second = client.rpc(request('otp'), self.root)
        self.assertTrue(first['ok'] and second['ok'])
        self.assertEqual(len(self.instances), 1)
        self.assertEqual(self.instances[0].pairs, 1)
        self.assertNotIn('FAKE-SECRET', json.dumps([first, second]))
        self.assertNotIn('FAKE-SECRET', (self.root / 'session.log').read_text())
        for name in ('session.sock', 'session.json'):
            self.assertEqual(stat.S_IMODE((self.root / name).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.root.stat().st_mode), 0o700)

    def test_wrong_token_and_foreign_uid_rejected(self):
        info = client.metadata(self.root)
        packet = dict(request(), v=broker.PROTOCOL, token='0' * 64)
        with socket.socket(socket.AF_UNIX) as connection:
            connection.connect(str(self.root / 'session.sock'))
            connection.sendall(json.dumps(packet).encode() + b'\n')
            response = json.loads(connection.recv(4096))
        self.assertFalse(response['ok'])
        self.assertEqual(self.copies, [])
        with self.assertRaises(BridgeError):
            broker.authorize(dict(request(), v=broker.PROTOCOL, token=info['token']), info['token'], os.geteuid() + 1)

    def test_no_raw_read_or_eval_endpoint(self):
        for op in ('read', 'eval', 'save'):
            response = client.rpc({'op': op}, self.root)
            self.assertFalse(response['ok'])
        self.assertEqual(self.copies, [])

    def test_manual_stop_removes_runtime_metadata(self):
        client.rpc(request(), self.root)
        client.rpc({'op': 'stop'}, self.root)
        self.thread.join(timeout=5)
        self.assertFalse(self.thread.is_alive())
        self.assertTrue(self.instances[0].closed)
        self.assertFalse((self.root / 'session.sock').exists())
        self.assertFalse((self.root / 'session.json').exists())

    def test_stop_is_accepted_during_an_inflight_copy(self):
        client.rpc(request(), self.root)
        entered = threading.Event()
        def wait_until_cancelled():
            entered.set()
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                self.instances[0].guard()
                time.sleep(.02)
            raise AssertionError('copy was not cancelled')
        self.instances[0].on_read = wait_until_cancelled
        results = []
        caller = threading.Thread(target=lambda: results.append(client.rpc(request(), self.root)))
        caller.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertTrue(client.rpc({'op': 'stop'}, self.root)['ok'])
        finally:
            caller.join(timeout=5)
        self.assertFalse(caller.is_alive())
        self.assertFalse(results[0]['ok'])
        self.assertEqual(len(self.copies), 1)


if __name__ == '__main__': unittest.main()
