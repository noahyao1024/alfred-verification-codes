# Third-party notices

`passwords_bridge/apw_bridge.js` adapts the Apple native-message and pairing
bridge from **apwlib 0.1.2**, https://github.com/michel-tricot/apwcli/tree/v0.1.2.
Changes replace the chauffeur transport with a private CDP-pipe caller and limit
operations to pairing and reads for the requested domain/username. Apple's SRP
and credential encryption remain in Apple's extension; they were not rewritten.

The CRX container layout and browser-loading approach were referenced from
**chauffeur 0.14.1**, https://github.com/michel-tricot/chauffeur. Neither chauffeur
nor apwcli is imported or required at runtime by this edition.

Both projects use the following MIT license:

Copyright (c) 2026 Michel Tricot

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

Apple's iCloud Passwords extension is not included in this repository or workflow
archive. Setup downloads version 3.4.0 from the Chrome Web Store and accepts only
SHA-256 `610ddd342cab1f66c317da9a24206839829a7ba10376afbb21c94781c3d84f26`.
This pin identifies bytes retrieved over HTTPS; it is not an independent audit
of Apple's extension or its CRX signing chain. Store updates require an explicit
workflow update; runtime never substitutes a different extension automatically.
