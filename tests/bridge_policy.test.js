const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../src/passwords_bridge/apw_bridge.js'), 'utf8');

function fixture(operation = 'password') {
    let receiver, sent = 0;
    const context = vm.createContext({
        setTimeout, clearTimeout,
        self: {addEventListener() {}},
        __passwordsConfig: {statusOk: 0, statusInvalidParam: 2, statusInvalidSession: 9,
            statusServerError: 100, nativeTimeoutMs: 1000, wireUnpaired: 'unpaired',
            operation, domain: 'example.com', username: 'alice'},
        g_theState: 'SessionKeySet',
        g_secretSession: {createSMSG: value => value, parseSMSG: value => value},
        g_nativeAppPort: {
            onMessage: {addListener(fn) {receiver = fn;}},
            postMessage(message) {
                sent++;
                queueMicrotask(() => receiver({cmd: message.cmd, payload: {SMSG: JSON.stringify({
                    STATUS: 0, Entries: [{USR: 'alice', PWD: 'FAKE-only'}]
                })}}));
            }
        }
    });
    vm.runInContext(source, context);
    return {context, request: context.__passwordsBridge.request, count: () => sent};
}

(async () => {
    const pw = {cmd: 5, qid: 'CmdGetPassword4LoginName', body: {ACT: 2, URL: 'example.com', USR: 'alice'}};
    const otp = {cmd: 17, qid: 'CmdDidFillOneTimeCode', body: {ACT: 2, TYPE: 'oneTimeCodes', frameURLs: ['https://example.com']}};
    const f = fixture();
    assert.equal((await f.request({cmd: 6, qid: 'write', body: {}})).status, 2);
    assert.equal((await f.request({...pw, body: {...pw.body, URL: 'other.example'}})).status, 2);
    assert.equal((await f.request({...pw, body: {...pw.body, USR: 'bob'}})).status, 2);
    assert.equal((await f.request(otp)).status, 2);
    assert.equal(f.count(), 0);
    assert.equal((await f.request(pw)).data.Entries[0].PWD, 'FAKE-only');
    assert.equal(f.count(), 1);
    f.context.__passwordsConfig.domain = '';
    assert.equal((await f.request(pw)).status, 2);
    f.context.__passwordsConfig.domain = 'example.com';
    f.context.__passwordsConfig.operation = 'otp';
    assert.equal((await f.request(pw)).status, 2);
    assert.equal((await f.request(otp)).data.STATUS, 0);
    f.context.__passwordsConfig.operation = 'password';
    f.context.g_theState = 'NotInSession';
    assert.equal((await f.request(pw)).status, 9);
    assert.equal(f.count(), 2);
    const code = fixture('otp');
    assert.equal((await code.request(pw)).status, 2);
    assert.equal((await code.request(otp)).data.STATUS, 0);
    const listing = fixture('otp_list');
    const metadata = {cmd: 16, qid: 'CmdDidFillOneTimeCode', body: {ACT: 5, TYPE: 'oneTimeCodes', frameURLs: ['https://example.com']}};
    assert.equal((await listing.request(otp)).status, 2);
    assert.equal((await listing.request(metadata)).data.STATUS, 0);
    assert.equal((await listing.request({...metadata, body: {...metadata.body, frameURLs: ['https://other.example']}})).status, 2);
    const pwListing = fixture('password_list');
    const pwMetadata = {cmd: 4, qid: 'CmdGetLoginNames4URL', url: 'example.com', body: {ACT: 5, URL: 'example.com'}};
    assert.equal((await pwListing.request(pw)).status, 2);
    assert.equal((await pwListing.request(pwMetadata)).data.STATUS, 0);
    assert.equal((await pwListing.request({...pwMetadata, body: {...pwMetadata.body, URL: 'other.example'}})).status, 2);
    assert.equal((await pwListing.request({...pwMetadata, body: {...pwMetadata.body, USR: 'alice'}})).status, 2);
    console.log('Bridge policy tests passed');
})().catch(error => {console.error(error); process.exitCode = 1;});
