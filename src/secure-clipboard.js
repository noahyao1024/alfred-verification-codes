ObjC.import('AppKit');
ObjC.import('Foundation');

function write(pasteboard, text) {
    const item = $.NSPasteboardItem.alloc.init;
    item.setStringForType(text, $.NSPasteboardTypeString);
    item.setStringForType($(''), $('org.nspasteboard.ConcealedType'));
    item.setStringForType($(''), $('org.nspasteboard.TransientType'));
    const items = $.NSMutableArray.alloc.init;
    items.addObject(item);
    pasteboard.clearContents;
    if (!pasteboard.writeObjects(items)) throw new Error('Clipboard write failed');
}

function run(args) {
    if (args.length === 1 && args[0] === '--selftest') {
        // A private named pasteboard leaves the user's clipboard untouched.
        const board = $.NSPasteboard.pasteboardWithUniqueName;
        try {
            for (const fixture of ['TEST-only: 引号 " $() ` \\ \n', '', '012345']) {
                write(board, $(fixture));
                if (ObjC.unwrap(board.stringForType($.NSPasteboardTypeString)) !== fixture)
                    throw new Error('Clipboard content changed');
                const types = ObjC.deepUnwrap(board.types);
                for (const marker of ['org.nspasteboard.ConcealedType', 'org.nspasteboard.TransientType']) {
                    if (types.indexOf(marker) < 0) throw new Error('Clipboard privacy marker missing');
                }
            }
        } finally {
            board.releaseGlobally;
        }
        return 'Private clipboard selftest passed';
    }
    if (args.length) throw new Error('Unexpected clipboard argument');
    const data = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
    const text = $.NSString.alloc.initWithDataEncoding(data, $.NSUTF8StringEncoding);
    if (!text) throw new Error('Clipboard input must be UTF-8');
    write($.NSPasteboard.generalPasteboard, text);
}
