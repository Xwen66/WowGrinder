"""v8.39（t171u）：跑尸路线。从复活点开始按顺序记坐标点（F6 / GUI 按钮），释放灵魂后先逐个走完，再朝尸体走；
途中离尸体够近就直接去尸体；路线点卡住一直绕路直到走到（不跳过、不停止）；路线为空 = 跟 v8.38 一样。"""
import math
import re
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.config import DEFAULT_CONFIG, _merge, _migrate  # noqa: E402
from bot.navigation import distance, forward_step  # noqa: E402
from bot.state_machine import State  # noqa: E402
import test_v831 as V831  # noqa: E402

dobs = V831.dobs
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class RouteBase(V831.CorpseBase):
    def setUp(self):
        super().setUp()
        self.turn_hook()
        self.cr = self.cfg["corpse"]
        self.path = []

    def die(self):
        self.g.tick(dobs(dead=True))
        self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face))

    def walk(self, steps=600, map_id=None):
        for _ in range(steps):
            res = distance(self.pos, self.corpse) < 0.5
            o = dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face, res=res)
            if map_id is not None:
                o.map_id = map_id
            self.g.tick(o)
            self.clock.t += 0.2
            if "w" in self.inp.held:
                self.pos = forward_step(self.pos, self.face, 0.05)
                self.path.append(self.pos)
            if self.g.cr is None or self.g.cr["phase"] != "run":
                return

    def closest(self, p):
        return min(distance(q, p) for q in self.path)


class TestRoute(RouteBase):
    def test_defaults(self):
        self.assertEqual(DEFAULT_CONFIG["keys"]["hotkey_add_route"], "f6")
        hk = [v for n, v in DEFAULT_CONFIG["keys"].items() if n.startswith("hotkey_")]
        self.assertEqual(len(hk), len(set(hk)))                 # 热键不重复
        c = DEFAULT_CONFIG["corpse"]
        self.assertEqual((c["route"], c["route_arrive_dist"], c["route_shortcut_dist"]), ([], 0.15, 1.0))
        cfg = _migrate(_merge(DEFAULT_CONFIG, {"corpse": {"enabled": True}}), {"corpse": {"enabled": True}})
        self.assertEqual(cfg["corpse"]["route"], [])
        self.assertEqual(cfg["keys"]["hotkey_add_route"], "f6")

    def test_empty_route_same_as_before(self):
        self.die()
        self.walk()
        self.assertLess(distance(self.pos, self.corpse), 0.6)
        self.assertFalse(any("🗺️" in m for m in self.logs))

    def test_walks_waypoints_in_order(self):
        # 尸体在正北 3；路线先往东 1、再往北
        self.cr["route"] = [[50.0, 50.0, 0], [51.0, 50.0, 0], [51.0, 48.0, 0]]
        self.die()
        self.walk()
        self.assertLess(distance(self.pos, self.corpse), 0.6)
        for i in (1, 2, 3):
            self.assertTrue(any(f"走到路线点 #{i}/3" in m for m in self.logs), i)
        self.assertLess(self.closest((51.0, 50.0)), 0.2)
        self.assertLess(self.closest((51.0, 48.0)), 0.2)
        self.assertTrue(any("路线走完了，朝尸体走" in m for m in self.logs))
        self.assertTrue(any("从 #1 开始" in m for m in self.logs))

    def test_start_from_nearest_ahead(self):
        self.cr["route"] = [[49.0, 52.0, 0], [51.0, 50.0, 0], [51.0, 48.0, 0]]
        self.pos = (51.0, 49.2)                                  # 已经过了 #2，离 #3 比 #2→#3 还近
        self.die()
        self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face))
        self.assertEqual(self.g.cr["wi"], 2)
        self.assertTrue(any("从 #3 开始" in m for m in self.logs))
        self.walk()
        self.assertLess(distance(self.pos, self.corpse), 0.6)
        self.assertFalse(any("走到路线点 #1/" in m for m in self.logs))

    def test_shortcut_when_corpse_close(self):
        self.corpse = (50.0, 48.8)
        self.cr["route"] = [[50.0, 49.5, 0], [56.0, 49.5, 0]]
        self.die()
        self.walk()
        self.assertLess(distance(self.pos, self.corpse), 0.6)
        self.assertTrue(any("走到路线点 #1/2" in m for m in self.logs))
        self.assertTrue(any("直接去尸体" in m and "不走剩下的 1 个点" in m for m in self.logs))
        self.assertGreater(self.closest((56.0, 49.5)), 4.0)       # 没去 #2

    def test_other_map_ignored(self):
        self.cr["route"] = [[51.0, 50.0, 9999]]
        self.die()
        self.walk(map_id=1413)
        self.assertLess(distance(self.pos, self.corpse), 0.6)
        self.assertTrue(any("都不在当前地图" in m for m in self.logs))

    def test_stuck_on_waypoint_never_skips(self):
        self.cr["route"] = [[52.0, 50.0, 0], [52.0, 47.0, 0]]
        self.die()
        for _ in range(1500):                                   # 位置一直不变（卡死）
            self.clock.t += 0.5
            self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face))
        self.assertFalse(self.g.stopped)
        self.assertEqual(self.g.state, State.CORPSE)
        self.assertEqual(self.g.cr["wi"], 0)                    # 一直在去 #1，没跳过
        self.assertEqual(self.g.cr["tgt"], (52.0, 50.0))
        self.assertGreater(self.g.cr["stuck"], 50)
        self.assertTrue(any("一点都没变近" in m for m in self.logs))
        self.assertTrue(any(k == "corpse_frozen" for k, _ in self.g.events))

    def test_record_hotkey_alive_and_ghost(self):
        saved = []
        self.g.on_route = saved.append
        self.g.request_area("route")
        o = dobs(dead=False, pos=(45.12, 63.4))
        o.map_id = 1413
        self.g.tick(o)
        self.assertEqual(self.cfg["corpse"]["route"], [[45.12, 63.4, 1413]])
        self.assertEqual(saved[-1], [[45.12, 63.4, 1413]])
        self.assertTrue(any("已添加跑尸路线点 #1" in m and "复活点" in m for m in self.logs))
        self.g.tick(dobs(dead=True))                            # 死了 / 灵魂状态也能记
        self.g.request_area("route")
        o = dobs(ghost=True, pos=(46.0, 62.0), corpse=self.corpse)
        o.map_id = 1413
        self.g.tick(o)
        self.assertEqual(len(self.cfg["corpse"]["route"]), 2)
        self.assertEqual(self.g.area_cmds, [])

    def test_record_no_pos_retries_then_fails(self):
        self.g.request_area("route")
        self.g.tick(dobs(dead=False, pos=None))
        self.assertEqual(self.g.route_cmds, ["route"])           # 1.5 秒内还在等坐标
        self.clock.t += 2
        self.g.tick(dobs(dead=False, pos=None))
        self.assertEqual(self.g.route_cmds, [])
        self.assertTrue(any("添加跑尸路线点失败" in m for m in self.logs))
        self.assertEqual(self.cfg["corpse"]["route"], [])

    def test_set_route(self):
        self.g.set_route([[1, 2, 3]])
        self.assertEqual(self.cfg["corpse"]["route"], [[1, 2, 3]])

    def test_detour_relative_to_waypoint(self):
        self.cr["route"] = [[52.0, 50.0, 0]]
        self.die()
        self.g.tick(dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=self.face))
        from bot.navigation import bearing_to
        o = dobs(ghost=True, pos=self.pos, corpse=self.corpse, facing=0.0)
        self.g.cr["detour_i"], self.g.cr["blocked"] = 0, [(bearing_to(self.pos, (52.0, 50.0)) + math.radians(60)) % (2 * math.pi)]
        self.assertEqual(self.g._cr_detour(o), -60)

    def test_gui_readme_runner(self):
        with open(os.path.join(ROOT, "gui", "app.py"), encoding="utf-8") as f:
            src = f.read()
        self.assertTrue(re.search(r"熊德自动打怪 v8\.(39|[4-9]\d)", src))   # 版本号往上走
        for s in ('"hotkey_add_route"', "route_lb", "_route_delete", "_route_clear", '_hotkey_area("route")',
                  '"route_shortcut_dist"', '"route_arrive_dist"', "on_route="):
            self.assertIn(s, src)
        with open(os.path.join(ROOT, "README_中文.md"), encoding="utf-8") as f:
            self.assertIn("v8.39", f.read())
        with open(os.path.join(ROOT, "bot", "runner.py"), encoding="utf-8") as f:
            r = f.read()
        self.assertIn("g._route_tick(", r)
        self.assertIn("g.on_route = self.on_route", r)


if __name__ == "__main__":
    unittest.main()
