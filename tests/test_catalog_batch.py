"""Concurrent metadata timing and failure boundaries without real credentials."""

import json
import shutil
import subprocess
import unittest

from test_login_flow import PASSWORDS, Monitor, broker, catalog_request
from passwords_bridge.browser import BridgeError
from passwords_bridge.session import PasswordSession


class BatchSession(PasswordSession):
    paired = False
    closed = False
    otp_reply = {'data': {'STATUS': 0, 'Entries': []}}
    def __enter__(self): return self
    def __exit__(self, *args): self.closed = True
    def state(self): return {'paired': self.paired, 'nativeReady': True}
    def set_scope(self, *args): pass
    def clear_scope(self): pass
    def pair(self): self.paired = True
    def catalog_queries(self):
        return {'password': {'data': {'STATUS': 0, 'Entries': PASSWORDS}},
                'otp': self.otp_reply,
                'timings': {'password_metadata': 7, 'otp_metadata': 45}}
    def catalog_fast(self, domain):
        return dict(self.catalog_queries(), paired=self.paired)


class CatalogBatchTests(unittest.TestCase):
    def make_manager(self, otp_reply):
        logs, copies = [], []
        factory = type('FixtureBatch', (BatchSession,), {'otp_reply': otp_reply})
        manager = broker.SessionManager(Monitor(), factory,
                   lambda value, delay: copies.append(value), log=logs.append)
        self.addCleanup(manager.close_session)
        return manager, logs, copies

    def test_independent_timings_are_not_replaced_with_batch_duration(self):
        manager, logs, copies = self.make_manager({'data': {'STATUS': 0, 'Entries': []}})
        manager.catalog(catalog_request())
        self.assertIn('perf|password_metadata|7', logs)
        self.assertIn('perf|otp_metadata|45', logs)
        self.assertEqual(len([line for line in logs if line.startswith('perf|metadata_batch|')]), 1)
        self.assertEqual(copies, [])

    def test_malformed_optional_otp_reply_keeps_valid_password_results(self):
        for reply in ({'data': []}, {'data': None}, None):
            with self.subTest(reply=reply):
                manager, logs, copies = self.make_manager(reply)
                result = manager.catalog(catalog_request())
                self.assertEqual(len(result['accounts']), 2)
                self.assertTrue(result['has_otp'])  # unknown must not become absent
                self.assertNotIn(('example.com', 'otp'), manager.catalog_cache)
                self.assertIn('otp_metadata_unavailable', logs)
                self.assertEqual(copies, [])

    def test_invalid_native_session_discards_the_entire_batch(self):
        manager, logs, copies = self.make_manager({'status': 9})
        with self.assertRaises(BridgeError): manager.catalog(catalog_request())
        self.assertEqual(manager.catalog_cache, {})
        self.assertIsNone(manager.session)
        self.assertEqual(copies, [])

    @unittest.skipUnless(shutil.which('node'), 'Node is used only for this development check')
    def test_actual_worker_expression_measures_each_overlapping_request(self):
        # Resolve the native promises at different logical times: this catches
        # accidentally assigning Promise.all's total to both phases.
        program = r'''
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const expression = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
    let now = 100;
    const pending = new Map();
    const config = {domain:'example.com', username:'', operation:'catalog'};
    const result = vm.runInNewContext(expression, {
        __passwordsConfig:config, performance:{now:()=>now},
        __passwordsBridge:{state:()=>({paired:true}), request:message=>{
            assert.equal(config.operation, 'catalog');
            return new Promise(resolve=>pending.set(message.cmd, resolve));
        }}
    });
    assert.equal(pending.size, 2);
    now = 107;
    pending.get(4)({data:{STATUS:0,Entries:[]}});
    // Drain cross-context promise adoption before advancing the fixture clock.
    await new Promise(setImmediate);
    now = 145;
    pending.get(16)({data:{STATUS:0,Entries:[]}});
    process.stdout.write(JSON.stringify(await result));
})().catch(error=>{console.error(error);process.exitCode=1});
'''
        session = PasswordSession('example.com', mode='catalog')
        session.evaluate = lambda expression: expression
        for expression in (session.catalog_queries(), session.catalog_fast('example.com')):
            with self.subTest(fast='paired:true' in expression):
                result = subprocess.run(['node', '-e', program], input=json.dumps(expression),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout)['timings'],
                                 {'password_metadata': 7, 'otp_metadata': 45})


if __name__ == '__main__': unittest.main()
