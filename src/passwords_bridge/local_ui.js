// Native UI only. Payloads arrive over stdin; results contain actions/indices only.
ObjC.import('AppKit');
ObjC.import('Foundation');

let picker = null;
ObjC.registerSubclass({
    name: 'PWUISmokeCloser', superclass: 'NSObject',
    methods: {'finish:': {types: ['void', ['id']], implementation: function () {
        picker.search.stringValue = $('work'); filterRows();
        picker.source.choose($());
    }}}
});
ObjC.registerSubclass({
    name: 'PWAccountTableSource', superclass: 'NSObject',
    protocols: ['NSTableViewDataSource', 'NSTextFieldDelegate', 'NSTableViewDelegate'],
    methods: {
        'numberOfRowsInTableView:': {types: ['long', ['id']], implementation: function () {return picker.rows.length;}},
        'tableView:objectValueForTableColumn:row:': {
            types: ['id', ['id', 'id', 'long']],
            implementation: function (table, column, row) {
                const item = picker.rows[Number(row)];
                const key = ObjC.unwrap(column.identifier);
                return $(key === 'account' ? item.title + ' · ' + item.username : item.sites);
            }
        },
        'controlTextDidChange:': {types: ['void', ['id']], implementation: function () {filterRows();}},
        'choose:': {types: ['void', ['id']], implementation: function () {
            if (Number(picker.table.selectedRow) >= 0) $.NSApplication.sharedApplication.stopModalWithCode(1000);
        }}
    }
});

function filterRows() {
    const query = ObjC.unwrap(picker.search.stringValue).toLocaleLowerCase();
    picker.rows = picker.all.filter(item => [item.title, item.username, item.sites].join(' ').toLocaleLowerCase().includes(query));
    picker.table.reloadData;
    picker.button.enabled = picker.rows.length > 0;
    if (picker.rows.length) picker.table.selectRowIndexesByExtendingSelection($.NSIndexSet.indexSetWithIndex(0), false);
}

function accountPicker(alert, items) {
    const view = $.NSView.alloc.initWithFrame($.NSMakeRect(0, 0, 650, 330));
    const search = $.NSSearchField.alloc.initWithFrame($.NSMakeRect(0, 298, 650, 28));
    search.placeholderString = $('搜索用户名、网站或条目名称');
    view.addSubview(search);
    const scroll = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(0, 0, 650, 286));
    scroll.hasVerticalScroller = true;
    scroll.hasHorizontalScroller = true;
    scroll.borderType = 2;
    const table = $.NSTableView.alloc.initWithFrame($.NSMakeRect(0, 0, 650, 286));
    table.rowHeight = 28;
    table.usesAlternatingRowBackgroundColors = true;
    table.allowsMultipleSelection = false;
    for (const spec of [['account', '名称 / 用户名', 350], ['sites', '网站', 450]]) {
        const column = $.NSTableColumn.alloc.initWithIdentifier($(spec[0]));
        column.title = $(spec[1]); column.width = spec[2];
        table.addTableColumn(column);
    }
    const source = $.PWAccountTableSource.alloc.init;
    picker = {all: items, rows: items, search, table, source, button: alert.buttons.objectAtIndex(0)};
    table.dataSource = source; table.delegate = source;
    table.target = source; table.doubleAction = 'choose:';
    search.delegate = source;
    scroll.documentView = table;
    view.addSubview(scroll);
    alert.accessoryView = view;
    filterRows();
}

function secretView(alert, value) {
    const scroll = $.NSScrollView.alloc.initWithFrame($.NSMakeRect(0, 0, 560, 115));
    scroll.hasVerticalScroller = true;
    scroll.borderType = 2;
    const text = $.NSTextView.alloc.initWithFrame($.NSMakeRect(0, 0, 540, 115));
    text.editable = false; text.selectable = false;
    text.verticallyResizable = true; text.horizontallyResizable = false;
    text.autoresizingMask = 2;
    text.font = $.NSFont.monospacedSystemFontOfSizeWeight(24, 0);
    text.textColor = $.NSColor.labelColor;
    text.textContainer.containerSize = $.NSMakeSize(540, 100000);
    text.textContainer.widthTracksTextView = true;
    text.string = $(value);
    scroll.documentView = text;
    alert.accessoryView = scroll;
    return text;
}

function makeAlert(payload) {
    const alert = $.NSAlert.alloc.init;
    alert.messageText = $(payload.title || 'Passwords Quick Copy');
    alert.informativeText = $(payload.message || '');
    const buttons = payload.buttons || [{id: 'done', label: '完成'}];
    buttons.forEach(button => alert.addButtonWithTitle($(button.label)));
    let field = null;
    if (payload.kind === 'picker') accountPicker(alert, payload.items);
    if (payload.kind === 'secret' && payload.show) secretView(alert, payload.value);
    if (payload.kind === 'secret') {
        alert.window.level = 3; // floating: remain readable while typing in another app
        alert.window.collectionBehavior = 1 | 256; // all spaces + fullscreen auxiliary
        alert.window.movableByWindowBackground = true;
        if (alert.window.respondsToSelector('setHidesOnDeactivate:')) alert.window.hidesOnDeactivate = false;
    }
    if (payload.kind === 'text') {
        field = $.NSTextField.alloc.initWithFrame($.NSMakeRect(0, 0, 560, 28));
        field.stringValue = $(payload.initial || '');
        field.placeholderString = $(payload.placeholder || '');
        alert.accessoryView = field;
        // Best-effort focus for legacy text prompts. Alias creation and rename
        // now use Alfred's own input field instead of this independent window.
        alert.window.initialFirstResponder = field;
    }
    return {alert, buttons, field};
}

function run(args) {
    if (args[0] === '--selftest') {
        const example = [{index: 0, title: '工作', username: 'work@example.com', sites: 'login.example.com'},
                         {index: 1, title: '个人', username: 'personal@example.com', sites: 'example.com'}];
        const built = makeAlert({kind: 'picker', items: example, buttons: [{id: 'select', label: '选择'}, {id: 'cancel', label: '取消'}]});
        if (Number(picker.table.numberOfRows) !== 2) throw new Error('Table rows missing');
        picker.search.stringValue = $('personal'); filterRows();
        if (picker.rows.length !== 1 || picker.rows[0].index !== 1) throw new Error('Account search failed');
        picker.search.stringValue = $('not-found'); filterRows();
        if (picker.rows.length || picker.button.enabled) throw new Error('Empty search selectable');
        const text = secretView(built.alert, 'SYNTHETIC " 中文 012345');
        if (ObjC.unwrap(text.string) !== 'SYNTHETIC " 中文 012345') throw new Error('Secret display changed text');
        const floating = makeAlert({kind: 'secret', value: 'SYNTHETIC', show: true});
        if (Number(floating.alert.window.level) !== 3) throw new Error('Manual-entry window is not floating');
        const input = makeAlert({kind: 'text', initial: 'alias'});
        if (!input.alert.window.initialFirstResponder.isEqual(input.field)) throw new Error('Text field is not first responder');
        return 'Native account picker and secret display selftest passed';
    }
    let payload;
    if (args[0] === '--smoke') {
        payload = {kind: 'picker', title: '界面自检（模拟账号）', items: [
            {index: 0, title: 'Demo personal', username: 'personal@example.invalid', sites: 'example.invalid'},
            {index: 1, title: 'Demo work', username: 'work@example.invalid', sites: 'work.example.invalid'}
        ], buttons: [{id: 'select', label: '选择'}, {id: 'cancel', label: '取消'}]};
    } else {
        const input = $.NSFileHandle.fileHandleWithStandardInput.readDataToEndOfFile;
        payload = JSON.parse(ObjC.unwrap($.NSString.alloc.initWithDataEncoding(input, $.NSUTF8StringEncoding)));
    }
    const built = makeAlert(payload);
    if (args[0] === '--smoke') {
        const closer = $.PWUISmokeCloser.alloc.init;
        const timer = $.NSTimer.timerWithTimeIntervalTargetSelectorUserInfoRepeats(.3, closer, 'finish:', $(), false);
        $.NSRunLoop.currentRunLoop.addTimerForMode(timer, $('NSModalPanelRunLoopMode'));
    }
    $.NSApplication.sharedApplication.activateIgnoringOtherApps(true);
    if (built.field) {
        built.alert.window.makeKeyAndOrderFront($());
        built.alert.window.makeFirstResponder(built.field);
        built.field.selectText($());
    }
    const response = Number(built.alert.runModal) - 1000;
    const button = built.buttons[response];
    const result = {action: button ? button.id : 'cancel'};
    if (result.action === 'select') {
        const row = Number(picker.table.selectedRow);
        result.index = row >= 0 && picker.rows[row] ? picker.rows[row].index : -1;
    }
    if (payload.kind === 'text' && result.action === 'accept') result.text = ObjC.unwrap(built.field.stringValue);
    built.alert.window.orderOut($());
    return JSON.stringify(result);
}
