"""Latch native lock notifications and check the current console session."""

import ctypes
import os
from pathlib import Path
import select
import subprocess
import time

from .browser import BridgeError


class ConsoleState:
    def __init__(self):
        self.cg = ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        self.cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        self.cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
        self.cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
        self.cf.CFStringCreateWithCString.restype = ctypes.c_void_p
        self.cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.cf.CFDictionaryGetValue.restype = ctypes.c_void_p
        self.cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
        self.cf.CFBooleanGetValue.restype = ctypes.c_bool
        self.cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
        self.cf.CFGetTypeID.restype = ctypes.c_ulong
        self.cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
        self.cf.CFRelease.argtypes = [ctypes.c_void_p]

    def blocked(self):
        snapshot = self.cg.CGSessionCopyCurrentDictionary()
        if not snapshot:
            return True
        values = {}
        try:
            for name in ('kCGSSessionOnConsoleKey', 'kCGSessionLoginDoneKey', 'CGSSessionScreenIsLocked'):
                key = self.cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)
                try:
                    value = self.cf.CFDictionaryGetValue(snapshot, key)
                    if value and self.cf.CFGetTypeID(value) != self.cf.CFBooleanGetTypeID():
                        return True
                    values[name] = self.cf.CFBooleanGetValue(value) if value else None
                finally:
                    self.cf.CFRelease(key)
            return (values['kCGSSessionOnConsoleKey'] is not True or
                    values['kCGSessionLoginDoneKey'] is not True or
                    values['CGSSessionScreenIsLocked'] is True)
        finally:
            self.cf.CFRelease(snapshot)


class LockMonitor:
    def __init__(self, extra_notification=None):
        self.snapshot = ConsoleState()
        self.triggered = False
        self.buffer = b''
        self.proc = subprocess.Popen(
            ['/usr/bin/osascript', '-l', 'JavaScript', str(Path(__file__).with_name('screen_watch.js')),
             str(os.getpid())] + ([extra_notification] if extra_notification else []),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if select.select([self.proc.stdout], [], [], 0.1)[0]:
                    self.buffer += os.read(self.proc.stdout.fileno(), 4096)
                    if b'ready\n' in self.buffer:
                        self.triggered = b'lock\n' in self.buffer
                        self.buffer = b''
                        return
                if self.proc.poll() is not None:
                    break
            raise BridgeError('无法启动锁屏监测，会话未开启。')
        except BaseException:
            self.close()
            raise

    def blocked(self):
        if self.proc.poll() is not None:
            return True  # losing the monitor must not leave a paired browser alive
        while select.select([self.proc.stdout], [], [], 0)[0]:
            data = os.read(self.proc.stdout.fileno(), 4096)
            if not data:
                return True
            self.buffer += data
        if b'lock\n' in self.buffer:
            self.triggered = True
        self.buffer = self.buffer[-16:]
        return self.triggered or self.snapshot.blocked()

    def close(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=3)
        self.proc.stdout.close()
