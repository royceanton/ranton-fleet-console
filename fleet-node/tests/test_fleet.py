import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error

from fleet.router import assess, choose
from fleet.server import Fleet, Handler, ThreadingHTTPServer
from fleet.store import Store
from fleet.worker import prepare_workspace, safe_project, permission_arguments, sessions


NOW = 2000000000


def observation(alias, reset=NOW + 3600):
    return {"authentication": "chatgpt", "identityFingerprint": alias, "observedAt": datetime.fromtimestamp(NOW, timezone.utc).isoformat(),
            "quotaStatus": "observed", "quotaWindows": [{"primary": {"remainingPercent": 80, "resetsAt": reset}, "secondary": None}],
            "models": [{"model": "test-model", "isDefault": True}]}


class RouterTests(unittest.TestCase):
    def test_earlier_reset_and_account_affinity(self):
        accounts = {"harith": observation("harith"), "jill": observation("jill", NOW+7200)}
        self.assertEqual(choose(accounts, {}, {}, NOW)[0], "harith")
        self.assertEqual(choose(accounts, {"account": "jill"}, {}, NOW)[0], "jill")
        self.assertIsNone(choose(accounts, {"account": "jill"}, {"jill": 1}, NOW)[0])

    def test_short_window_unknown_stale_model_and_cooldown(self):
        sample = observation("harith")
        sample["quotaWindows"][0]["secondary"] = {"remainingPercent": 0, "resetsAt": NOW+30}
        self.assertFalse(assess(sample, NOW)["eligible"])
        self.assertFalse(assess(observation("harith"), NOW+301)["eligible"])
        self.assertFalse(assess(observation("harith"), NOW, model="missing")["eligible"])
        self.assertFalse(assess(observation("harith"), NOW, cooldown=NOW+30)["eligible"])
        sample = observation("harith"); sample["quotaWindows"][0]["primary"]["remainingPercent"] = None
        self.assertFalse(assess(sample, NOW)["eligible"])

    def test_all_accounts_wait_and_reserve(self):
        accounts={key:observation(key) for key in ("harith","jill")}
        for sample in accounts.values(): sample["quotaWindows"][0]["primary"]["remainingPercent"]=10
        self.assertIsNone(choose(accounts, {}, {}, NOW)[0])


class DurableTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name).resolve()
        self.store=Store(self.path/"test.sqlite3")
        self.project=self.store.add_project("Test",str(self.path),False,False)

    def tearDown(self): self.tmp.cleanup()

    def submit(self, key="same"):
        return self.store.submit({"title":"One task","goal":"Read project","project":self.project["id"],"mode":"read-only","priority":10,"timeout":60,"idempotency":key})

    def test_idempotent_submission_and_exclusive_account_claim(self):
        first=self.submit();self.assertEqual(first["id"],self.submit()["id"])
        second=self.submit("second");results=[]
        threads=[threading.Thread(target=lambda t=t:results.append(self.store.claim(t["id"],"harith","test"))) for t in (first,second)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(sorted(results),[False,True])
        self.assertEqual(len(Store(self.path/"test.sqlite3").tasks()),2)

    def test_identity_binding_survives_signout(self):
        self.store.observe("harith",observation("original"))
        self.store.observe("harith",{"authentication":"signed-out"})
        self.store.observe("harith",observation("different"))
        self.assertEqual(self.store.observations()["harith"]["authentication"],"identity-changed")

    def test_git_worktree_preserves_dirty_source_and_hooks(self):
        repo=self.path/"repo";repo.mkdir();spaces=self.path/"worktrees";spaces.mkdir()
        def git(*args):return subprocess.run(["git","-C",str(repo),*args],check=True,capture_output=True,text=True)
        git("init","-q");(repo/"a.txt").write_text("committed\n");git("add","a.txt");git("-c","user.name=Fleet Test","-c","user.email=fleet-test@localhost","commit","-qm","base")
        (repo/"a.txt").write_text("owner's local changes\n")
        hook=repo/".git/hooks/post-checkout";hook.write_text("#!/bin/sh\ntouch '"+str(self.path/"hook-ran")+"'\n");hook.chmod(0o700)
        task={"id":"test-worktree","mode":"code"}
        workspace,branch,revision=prepare_workspace(task,{"path":str(repo),"git":True,"writable":True},spaces)
        self.assertEqual((repo/"a.txt").read_text(),"owner's local changes\n")
        self.assertEqual((workspace/"a.txt").read_text(),"committed\n")
        self.assertFalse((self.path/"hook-ran").exists())
        self.assertTrue(branch.startswith("fleet/"));self.assertTrue(revision)

    def test_project_boundaries_and_permissions(self):
        for target in (Path.home(),Path.home()/".codex",Path.home()/".local/share/codex-fleet",Path.home()/".local/share/ranton-proxy"):
            with self.assertRaises(ValueError):safe_project(target)
        args=permission_arguments({"mode":"code","workspace":str(self.path)}, {"git":False})
        self.assertIn("permissions.fleet_task.network.enabled=false",args)
        self.assertFalse(any("danger-full-access" in arg for arg in args))
        self.assertTrue(any(str(Path.home()/".ssh") in arg and '"deny"' in arg for arg in args))

    def test_native_trust_record_without_policy_changes(self):
        accounts=sessions.accounts
        root=str(Path(__file__).resolve().parent.parent)
        valid=accounts.CONFIG+'\n[projects.'+json.dumps(root)+']\ntrust_level = "trusted"\n'
        self.assertTrue(accounts.managed_config(valid))
        self.assertFalse(accounts.managed_config(valid.replace('sandbox_mode = "read-only"','sandbox_mode = "danger-full-access"')))
        self.assertFalse(accounts.managed_config(valid+'model_provider = "unknown"\n'))
        self.assertFalse(accounts.managed_config(accounts.CONFIG+'\n[projects."/unregistered"]\ntrust_level = "trusted"\n'))


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.old_umask=os.umask(0o077);self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name).resolve()
        self.fleet=Fleet(self.path/"runtime",self.path/"workspaces")
        self.server=ThreadingHTTPServer(("127.0.0.1",0),Handler);self.server.fleet=self.fleet
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.base="http://127.0.0.1:"+str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.fleet.close();self.tmp.cleanup();os.umask(self.old_umask)

    def call(self, path, data=None, headers=None):
        body=json.dumps(data).encode() if data is not None else None
        req=urllib.request.Request(self.base+path,data=body,headers={"Content-Type":"application/json",**(headers or {})})
        try:
            with urllib.request.urlopen(req,timeout=3) as response:return response.status,json.load(response),response.headers
        except urllib.error.HTTPError as e:return e.code,json.load(e),e.headers

    def test_auth_origin_and_single_use_pairing(self):
        self.assertEqual(self.call("/api/status")[0],401)
        bearer={"Authorization":"Bearer "+self.fleet.key}
        self.assertEqual(self.call("/api/status",headers=bearer)[0],200)
        self.assertEqual(self.call("/api/dispatch",{"enabled":False},headers={**bearer,"Origin":"https://evil.test"})[0],403)
        pair=self.call("/api/pair",{},bearer)[1]
        browser={"Origin":self.base,"X-Fleet-Intent":"dashboard"}
        status,_,headers=self.call("/api/login",{"key":pair["code"]},browser)
        self.assertEqual(status,200);self.assertIn("HttpOnly",headers["Set-Cookie"]);self.assertIn("SameSite=Strict",headers["Set-Cookie"])
        self.assertEqual(self.call("/api/login",{"key":pair["code"]},browser)[0],401)

    def test_restart_requires_explicit_resume(self):
        project=self.fleet.store.projects()[0]
        task=self.fleet.submit({"project":project["id"],"title":"Interrupted","goal":"Read docs"})
        self.fleet.store.claim(task["id"],"harith","test")
        self.fleet.close()
        restored=Fleet(self.path/"runtime",self.path/"workspaces")
        try:
            self.assertEqual(restored.store.task(task["id"])["state"],"needs_input")
            self.assertEqual(restored.store.task(task["id"])["account"],"harith")
        finally:restored.close()


if __name__=="__main__":unittest.main()
