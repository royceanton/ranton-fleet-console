import ctypes
import errno
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('bridge', ROOT/'console_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class BridgeTests(unittest.TestCase):
    def test_key_permissions_empty_and_symlinked_ancestors(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            directory = root/'private'
            directory.mkdir()
            key = directory/'key'
            key.write_text('fixture-only')
            key.chmod(0o600)
            self.assertEqual(bridge.private_key(key), 'fixture-only')
            key.write_text('')
            with self.assertRaises(RuntimeError):
                bridge.private_key(key)
            key.write_text('fixture-only')
            key.chmod(0o644)
            with self.assertRaises(RuntimeError):
                bridge.private_key(key)
            key.chmod(0o600)
            (root/'link').symlink_to(directory)
            with self.assertRaises(OSError) as denied:
                bridge.private_key(root/'link/key')
            self.assertIn(denied.exception.errno, (errno.ELOOP, errno.ENOTDIR))

    def test_summary_exports_only_whitelisted_numeric_counters(self):
        raw = {'files':[{'provider':'codex','success':2,'failed':1,'email':'PRIVATE','id_token':{'PRIVATE':'PRIVATE'},'path':'PRIVATE'}]}
        summary = bridge.usage_summary(raw)
        self.assertEqual(summary, {'available':True,'requests':3,'success':2,'failed':1})
        self.assertNotIn('PRIVATE', json.dumps(summary))
        self.assertEqual(bridge.usage_summary({}), {'available':False})


@unittest.skipUnless(os.uname().sysname == 'Darwin', 'macOS native plugin')
class NativePluginTests(unittest.TestCase):
    def test_actual_abi_registration_and_fail_closed_bad_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            library = Path(temporary)/'router.dylib'
            subprocess.run(['clang','-fobjc-arc','-dynamiclib','-framework','Foundation',str(ROOT/'scheduler.m'),'-o',str(library)],check=True,capture_output=True)
            class Buffer(ctypes.Structure):
                _fields_ = [('ptr',ctypes.c_void_p),('length',ctypes.c_size_t)]
            class Host(ctypes.Structure):
                _fields_ = [('abi',ctypes.c_uint32),('context',ctypes.c_void_p),('call',ctypes.c_void_p),('free',ctypes.c_void_p)]
            Call = ctypes.CFUNCTYPE(ctypes.c_int,ctypes.c_char_p,ctypes.c_void_p,ctypes.c_size_t,ctypes.POINTER(Buffer))
            Free = ctypes.CFUNCTYPE(None,ctypes.c_void_p,ctypes.c_size_t)
            class Plugin(ctypes.Structure):
                _fields_ = [('abi',ctypes.c_uint32),('call',Call),('free',Free),('shutdown',ctypes.c_void_p)]
            native = ctypes.CDLL(str(library))
            host, plugin = Host(1,None,None,None), Plugin()
            self.assertEqual(native.cliproxy_plugin_init(ctypes.byref(host),ctypes.byref(plugin)),0)
            def call(method, payload):
                encoded = json.dumps(payload).encode()
                output = Buffer()
                self.assertEqual(plugin.call(method.encode(),encoded,len(encoded),ctypes.byref(output)),0)
                try:
                    return json.loads(ctypes.string_at(output.ptr,output.length))
                finally:
                    plugin.free(output.ptr,output.length)
            registration = call('plugin.register', {})
            self.assertEqual(registration['result']['metadata']['GitHubRepository'], 'https://github.com/royceanton/ranton-fleet-console')
            self.assertTrue(registration['result']['capabilities']['scheduler'])
            self.assertTrue(registration['result']['capabilities']['request_lifecycle_plugin'])
            refused = call('scheduler.pick', {'Candidates': []})
            self.assertFalse(refused['ok'])
            self.assertEqual(refused['error']['http_status'],503)
