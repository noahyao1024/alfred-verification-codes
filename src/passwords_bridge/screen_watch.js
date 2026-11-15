// Observe macOS lock/session/sleep events. No credentials or browser access.
ObjC.import('AppKit');
ObjC.import('Foundation');
ObjC.bindFunction('getppid', ['int', []]);

function emit(value) {
    $.NSFileHandle.fileHandleWithStandardOutput.writeData($(value + '\n').dataUsingEncoding($.NSUTF8StringEncoding));
}

function run(args) {
    const parent = Number(args[0]);
    ObjC.registerSubclass({
        name: 'PWSessionLockObserver', superclass: 'NSObject',
        methods: {'locked:': {types: ['void', ['id']], implementation: function () {emit('lock');}}}
    });
    const observer = $.PWSessionLockObserver.alloc.init;
    const distributed = $.NSDistributedNotificationCenter.defaultCenter;
    distributed.addObserverSelectorNameObject(observer, 'locked:', $('com.apple.screenIsLocked'), $());
    // Extra test notification never replaces the real lock observer.
    if (args[1] && args[1].startsWith('com.noah.passwords.test.'))
        distributed.addObserverSelectorNameObject(observer, 'locked:', $(args[1]), $());
    const workspace = $.NSWorkspace.sharedWorkspace.notificationCenter;
    for (const name of ['NSWorkspaceSessionDidResignActiveNotification', 'NSWorkspaceWillSleepNotification'])
        workspace.addObserverSelectorNameObject(observer, 'locked:', $(name), $());
    emit('ready');
    while ($.getppid() === parent)
        $.NSRunLoop.currentRunLoop.runUntilDate($.NSDate.dateWithTimeIntervalSinceNow(0.25));
}
