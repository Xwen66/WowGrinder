"""v8.28（t150u-t152u）：插件 v0.11 黄|蓝 标记、点前确认/点后验证/拉黑、顶部不点、巡逻连续 2 帧、远处红名走过去、先停下再点、点中后接近途中不再点。"""
import copy
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.addon_reader import AddonReader, paint_test_frame  # noqa: E402
from bot.config import DEFAULT_CONFIG  # noqa: E402
from bot.detector import Detector  # noqa: E402
from bot.marker import find_enemy_markers, find_red_names  # noqa: E402
from bot.state_machine import State  # noqa: E402
from test_state_machine import Base, obs  # noqa: E402

try:
    import lupa
except ImportError:
    lupa = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LUA = os.path.join(ROOT, "addon", "BotStatus", "BotStatus.lua")


def draw_mark(img, cx, cy):
    """画一个 v0.11 黄|蓝 标记（20x12，中心 cx,cy）。"""
    x, y = int(cx) - 10, int(cy) - 6
    img[y:y + 12, x:x + 20] = (0, 0, 0)
    img[y + 2:y + 10, x + 2:x + 10] = (0, 255, 255)      # 黄 (BGR)
    img[y + 2:y + 10, x + 10:x + 18] = (255, 0, 0)       # 蓝 (BGR)


def mplate(cx, cy):
    return {"x": int(cx) - 10, "y": int(cy) - 6, "w": 20, "h": 12, "score": 1.0, "adj": 1.0,
            "kind": "unselected", "from_marker": True}


class TestFindMarkers(unittest.TestCase):
    def test_finds_all_marks_and_skips_status_area(self):
        img = np.zeros((1080, 1920, 3), np.uint8)
        for c in ((700, 400), (1200, 500), (960, 300)):
            draw_mark(img, *c)
        draw_mark(img, 200, 20)                                       # 状态色块区域里 → 不算
        img[600:610, 100:110] = (0, 255, 255)                        # 单独黄块、远处一块蓝 → 不算
        img[600:610, 300:310] = (255, 0, 0)
        pts = sorted(find_enemy_markers(img))
        self.assertEqual(len(pts), 3)
        for (x, y), (ex, ey) in zip(pts, sorted(((700, 400), (1200, 500), (960, 300)))):
            self.assertLess(abs(x - ex), 3)
            self.assertLess(abs(y - ey), 3)

    def test_cyan_magenta_target_marker_not_counted(self):
        img = np.zeros((600, 800, 3), np.uint8)
        img[300:308, 400:408] = (255, 255, 0)
        img[300:308, 408:416] = (255, 0, 255)
        self.assertEqual(find_enemy_markers(img), [])


class TestRedNames(unittest.TestCase):
    def text(self, img, x, y, n=5, col=(40, 40, 200)):
        for i in range(n):                                            # 一行“字”：竖笔画 + 横笔画
            gx = x + i * 14
            img[y:y + 12, gx:gx + 2] = col
            img[y + 5:y + 7, gx:gx + 9] = col
            img[y:y + 2, gx + 6:gx + 9] = col

    def test_text_found_block_rejected(self):
        img = np.full((1080, 1920, 3), 60, np.uint8)
        self.text(img, 900, 200)
        img[700:720, 300:500] = (30, 30, 220)                        # 实心红块（血条/按钮）
        img[400:404, 1300:1302] = (30, 30, 220)                      # 一点点红
        names = find_red_names(img, [156, 93, 1405, 770])
        self.assertEqual(len(names), 1)
        self.assertTrue(880 <= names[0]["x"] <= 905)

    def test_dim_far_name(self):
        img = np.full((600, 800, 3), (85, 90, 100), np.uint8)       # 棕灰地面
        self.text(img, 300, 100, col=(49, 53, 110))                  # 远处变淡的红字（实测约 110,53,49）
        self.assertEqual(len(find_red_names(img)), 1)

    def test_brownish_ground_not_text(self):
        img = np.full((600, 800, 3), (70, 90, 140), np.uint8)       # 偏红的岩石地面 R/G≈1.55
        self.assertEqual(find_red_names(img), [])


class TestReaderV011(unittest.TestCase):
    def test_v011_blocks(self):
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        st = AddonReader(cfg).read(paint_test_frame({}, enemy=(True, False, 3)))
        self.assertTrue(st.ok, st.reason)
        self.assertTrue(st.enemy_marks)
        self.assertTrue(st.enemy_api_ok)
        self.assertFalse(st.enemy_unknown)
        self.assertEqual(st.plate_count, 3)
        self.assertTrue(st.marker_center)
        st = AddonReader(cfg).read(paint_test_frame({}, enemy=(True, True, 7)))
        self.assertTrue(st.enemy_unknown)
        self.assertEqual(st.plate_count, 7)

    def test_v010_has_no_enemy_marks(self):
        st = AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(
            paint_test_frame({}, v08=True, combo=0, energy_lvl=0, marker_center=True))
        self.assertTrue(st.ok)
        self.assertTrue(st.marker_center)
        self.assertFalse(st.enemy_marks)


class TestDetectorV011(unittest.TestCase):
    def frame(self, enemy):
        img = np.zeros((1080, 1920, 3), np.uint8)
        top = paint_test_frame({}, enemy=enemy) if enemy else paint_test_frame({}, v08=True, combo=0, energy_lvl=0,
                                                                                  marker_center=True)
        img[:200, :400] = top
        draw_mark(img, 800, 450)
        draw_mark(img, 1100, 380)
        return img

    def test_marker_source(self):
        d = Detector(copy.deepcopy(DEFAULT_CONFIG))
        o = d.observe(self.frame((True, False, 2)))
        self.assertEqual(o.plate_source, "marker")
        self.assertEqual(len([p for p in o.nameplates if p["kind"] == "unselected"]), 2)
        self.assertTrue(all(p.get("from_marker") for p in o.nameplates))
        plates, src = d.fresh_plates(self.frame((True, False, 2)))
        self.assertEqual((len(plates), src), (2, "marker"))

    def test_fallback_to_templates(self):
        d = Detector(copy.deepcopy(DEFAULT_CONFIG))
        o = d.observe(self.frame(None))
        self.assertEqual(o.plate_source, "template")
        self.assertIn("v0.11", o.plate_note)
        o = d.observe(self.frame((True, True, 2)))                   # 有怪的状态读不到 → 模板
        self.assertEqual(o.plate_source, "template")
        self.assertIn("机密值", o.plate_note)
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        cfg["nameplate"]["use_enemy_markers"] = False
        self.assertEqual(Detector(cfg).observe(self.frame((True, False, 2))).plate_source, "template")


@unittest.skipIf(lupa is None, "没有 lupa")
class TestLuaV011(unittest.TestCase):
    def run_lua(self, units):
        from test_addon_lua import STUBS, TestAddonLua
        L = lupa.LuaRuntime()
        L.execute(STUBS)
        L.execute("""
FRAMES = {}
local CF = CreateFrame
function CreateFrame(kind, name, parent)
  local f = CF(kind, name, parent)
  f.Show = function(self) self.shown = true end
  f.Hide = function(self) self.shown = false end
  if kind ~= "StatusBar" then f.SetPoint = function(self, a, rel, b, x, y) self.anchor = rel end end
  FRAMES[#FRAMES + 1] = f
  return f
end
UNITS = {}
local UE = UnitExists
function UnitExists(u) if UNITS[u] then return true end return UE(u) end
function UnitCanAttack(p, u) if UNITS[u] then return UNITS[u].atk end return true end
function UnitIsPlayer(u) return UNITS[u] and UNITS[u].player or false end
local UD = UnitIsDeadOrGhost
function UnitIsDeadOrGhost(u) if UNITS[u] then return UNITS[u].dead end return UD(u) end
function UnitIsTapDenied(u) return UNITS[u] and UNITS[u].tapped or false end
PLATES = {}
C_NamePlate = { GetNamePlateForUnit = function(u)
  if u == "target" then return nil end
  if UNITS[u] then PLATES[u] = PLATES[u] or { name = u }; return PLATES[u] end
  return nil end }
""")
        g = L.globals()
        for u, d in units.items():
            L.execute(f'UNITS["{u}"] = {{ atk = {str(d.get("atk", True)).lower()}, '
                      f'dead = {str(d.get("dead", False)).lower()}, player = {str(d.get("player", False)).lower()}, '
                      f'tapped = {str(d.get("tapped", False)).lower()} }}')
        g.POSX, g.POSY, g.FACING, g.MAPID = 0.5, 0.5, 1.0, 1
        g.HASTARGET, g.TDEAD, g.COMBAT = False, False, False
        with open(LUA, encoding="utf-8") as f:
            L.execute(f.read())
        frame = g.BotStatusFrame
        frame.script_OnEvent(frame, "PLAYER_ENTERING_WORLD")
        g.NOW = 103
        frame.script_OnUpdate(frame, 1.0)
        shown = [f for f in g.FRAMES.values() if f.shown is True and f.anchor is not None
                 and not isinstance(f.anchor, (int, float)) and f.anchor != g.BotStatusFrame]
        img = TestAddonLua.paint(g)
        return len(shown), AddonReader(copy.deepcopy(DEFAULT_CONFIG)).read(img)

    def test_marks_only_attackable(self):
        n, st = self.run_lua({"nameplate1": {}, "nameplate2": {"dead": True}, "nameplate3": {"atk": False},
                              "nameplate4": {"tapped": True}, "nameplate5": {"player": True}, "nameplate6": {}})
        self.assertTrue(st.ok, st.reason)
        self.assertEqual(n, 2)
        self.assertTrue(st.enemy_marks)
        self.assertTrue(st.enemy_api_ok)
        self.assertEqual(st.plate_count, 6)
        n, st = self.run_lua({})
        self.assertEqual((n, st.plate_count), (0, 0))

    def test_toc_version(self):
        with open(os.path.join(ROOT, "addon", "BotStatus", "BotStatus.toc"), encoding="utf-8") as f:
            toc = f.read()
        self.assertRegex(toc, r"## Version: 0\.1[2-9]\.0")   # 插件版本往上走


class Click828(Base):
    """新默认：点后验证 0.6 秒、拉黑 8 秒、顶部 23% 不点、巡逻连续 2 帧、远处红名、先停稳 0.3 秒、右键间隔 3 秒。"""
    MODE = "click"
    LEGACY = False
    RGAP = 3.0

    def setUp(self):
        super().setUp()
        self.cfg["nameplate"].update(copy.deepcopy(DEFAULT_CONFIG["nameplate"]))
        self.cfg["buffs"]["enabled"] = False
        self.k.update(target_attacker="x")
        self.peeks = []
        self.fresh = None

        def peek():
            self.peeks.append(1)
            return (self.fresh if self.fresh is not None else self.cur), "marker"
        self.g.peek_plates = peek
        self.cur = []

    def o(self, plates=(), names=(), moving=False, **kw):
        self.cur = list(plates)
        x = obs(plates=plates, **kw)
        x.frame_size = (1920, 1080)
        x.plate_source = "marker"
        x.red_names = list(names)
        x.moving = moving
        return x

    def tick(self, dt=0.15, **kw):
        self.clock.t += dt
        self.g.tick(self.o(**kw))

    def clicks_at(self):
        return [k for k in self.keys() if k.startswith("click(")]


class TestClick828(Click828):
    def test_top_strip_not_clicked(self):
        self.cfg["nameplate"]["far_names"] = False
        self.tick(plates=[mplate(960, 150)])                         # y=150 < 23%×1080=248
        self.assertEqual(self.clicks_at(), [])
        self.tick(dt=3.5, plates=[mplate(960, 300)])
        self.assertEqual(self.clicks_at(), ["click(960,300)"])

    def test_confirm_moved_no_click_then_click_fresh_position(self):
        self.fresh = [mplate(1100, 500)]                               # 再截图：标记跑了 140 像素
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), [])
        self.fresh = [mplate(965, 504)]                                # 还在原位附近 → 点新位置
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), ["click(965,504)"])

    def test_verify_miss_blacklists_spot(self):
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(len(self.clicks_at()), 1)
        self.tick(dt=0.3, plates=[mplate(960, 500)])                  # 0.3 秒：还在等
        self.assertIsNotNone(self.g.click_pending)
        self.tick(dt=0.4, plates=[mplate(960, 500)])                  # 0.7 秒没选上 → 点空
        self.assertIsNone(self.g.click_pending)
        self.assertTrue(any("算点空" in m for m in self.logs))
        self.tick(dt=3.0, plates=[mplate(960, 500)])                  # 8 秒内不点那里
        self.assertEqual(len(self.clicks_at()), 1)
        self.tick(dt=0.2, plates=[mplate(960, 500), mplate(1300, 600)])   # 别的标记可以点
        self.assertEqual(self.clicks_at()[-1], "click(1300,600)")
        self.g.click_pending = None
        self.clock.t += 9
        self.g.last_rclick = -1e9
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at()[-1], "click(960,500)")       # 8 秒后又可以点

    def test_tapped_after_click_counts_as_miss(self):
        self.tick(plates=[mplate(960, 500)])
        self.tick(dt=0.2, thp=100, kind="tapped", plates=[mplate(960, 500)])
        self.assertTrue(any("选上的目标不能打（tapped）" in m for m in self.logs))

    def test_halt_first_then_click(self):
        self.g._start_walk(self.o())
        self.inp.history.clear()
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), [])                        # 先松开 w
        self.assertFalse(self.g.walking)
        self.assertTrue(any("先停下" in m for m in self.logs))
        self.tick(dt=0.1, plates=[mplate(960, 500)])                  # 停了 0.1 秒还没稳
        self.assertEqual(self.clicks_at(), [])
        self.tick(dt=0.3, plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), ["click(960,500)"])

    def test_click_to_move_drift_is_stopped_with_back_tap(self):
        self.tick(plates=[mplate(960, 500)], moving=True)            # 没按 w 却在动（点地面后自动走）
        self.assertEqual(self.clicks_at(), [])
        self.assertIn("s", self.keys())
        self.tick(dt=0.4, plates=[mplate(960, 500)])
        self.assertEqual(self.clicks_at(), ["click(960,500)"])

    def test_hit_then_no_more_clicks_while_approaching(self):
        self.tick(plates=[mplate(960, 500)])
        self.assertEqual(len(self.clicks_at()), 1)
        self.tick(dt=0.3, thp=100, moving=True, rng="far")
        self.assertEqual(self.g.state, State.APPROACHING)
        for _ in range(20):                                           # 接近途中（在动）：不再点
            self.tick(dt=0.5, thp=100, moving=True, rng="ranged", err_far=True)
        self.assertEqual(len(self.clicks_at()), 1)
        self.assertNotIn("`", self.keys())                            # 不按互动键

    def test_reclick_only_when_stopped_and_problem(self):
        self.tick(plates=[mplate(960, 500)])
        self.tick(dt=0.3, thp=100, moving=True, rng="far")
        sel = {"x": 950, "y": 400, "w": 20, "h": 12, "kind": "selected", "score": 1.0, "adj": 1.0}
        for _ in range(4):
            self.clock.t += 1.0
            x = self.o(thp=100, rng="far", plates=[sel])
            x.pos = (50.0, 50.0)
            self.g.tick(x)                                            # 停住了、坐标不变、距离没变近
        self.assertEqual(len(self.clicks_at()), 2)
        self.assertTrue(any("角色已停下，再右键点一次" in m for m in self.logs))


class TestFar828(Click828):
    def setUp(self):
        super().setUp()
        self.cfg["area"].update(enabled=True, radius=2.0)
        self.cfg["nameplate"]["far_click_frames"] = self.cfg["nameplate"]["patrol_frames"]   # v8.50 前
        self.g.home = (50.0, 50.0, 1442)

    def otick(self, dt=0.15, pos=(50.0, 50.0), **kw):
        self.clock.t += dt
        x = self.o(**kw)
        x.pos, x.facing, x.map_id = pos, 0.0, 1442
        self.g.tick(x)

    def test_walk_toward_red_name_then_click(self):
        name = {"x": 1200, "y": 150, "w": 60, "h": 14, "px": 200}
        self.otick(names=[name])
        self.assertIsNotNone(self.g.far)
        self.assertTrue(self.g.walking)
        self.assertTrue(any(k in ("right",) for k in self.keys()))    # 名字偏右 → 先右转
        self.otick(names=[name], pos=(50.1, 50.0))
        self.otick(names=[name], pos=(50.2, 50.0), plates=[mplate(1000, 400)])   # 出标记 1 帧：继续走
        self.assertTrue(self.g.walking)
        self.otick(pos=(50.3, 50.0), plates=[mplate(1000, 400)])     # 连续 2 帧 → 停下
        self.assertFalse(self.g.walking)
        self.assertIsNone(self.g.far)
        self.otick(dt=0.4, pos=(50.3, 50.0), plates=[mplate(1000, 400)])
        self.assertEqual(self.clicks_at(), ["click(1000,400)"])
        self.assertNotIn("`", self.keys())

    def test_far_timeout_and_skip(self):
        name = {"x": 960, "y": 150, "w": 60, "h": 14, "px": 200}
        self.otick(names=[name])
        x = 50.0
        for _ in range(80):
            x += 0.01
            self.otick(names=[name], pos=(x, 50.0))
            if self.g.far is None:
                break
        self.assertIsNone(self.g.far)
        self.assertFalse(self.g.walking)
        self.assertTrue(any("还没出标记" in m for m in self.logs))
        self.otick(names=[name], pos=(x, 50.0))
        self.assertIsNone(self.g.far)                                 # 20 秒内不再走

    def test_far_stops_at_area_edge(self):
        name = {"x": 960, "y": 150, "w": 60, "h": 14, "px": 200}
        self.otick(names=[name])
        self.otick(names=[name], pos=(52.5, 50.0))
        self.assertIsNone(self.g.far)
        self.assertTrue(any("范围边上" in m for m in self.logs))

    def test_far_lost_name_and_stuck(self):
        name = {"x": 960, "y": 150, "w": 60, "h": 14, "px": 200}
        self.otick(names=[name])
        self.otick(dt=1.0, pos=(50.1, 50.0))
        self.otick(dt=1.0, pos=(50.2, 50.0))
        self.assertIsNone(self.g.far)
        self.assertTrue(any("看不到那个名字" in m for m in self.logs))
        self.g.far_skip_until = -1e9
        self.otick(names=[name], pos=(50.2, 50.0))
        for _ in range(4):
            self.otick(dt=1.0, names=[name], pos=(50.2, 50.0))
        self.assertTrue(any("卡住" in m for m in self.logs))

    def test_combat_ends_far_walk(self):
        name = {"x": 960, "y": 150, "w": 60, "h": 14, "px": 200}
        self.otick(names=[name])
        self.otick(combat=True, hp=95)
        self.assertIsNone(self.g.far)
        self.assertFalse(self.g.walking)

    def test_no_far_walk_with_template_source(self):
        name = {"x": 960, "y": 150, "w": 60, "h": 14, "px": 200}
        self.clock.t += 0.15
        x = self.o(names=[name])
        x.plate_source = "template"
        x.pos, x.facing, x.map_id = (50.0, 50.0), 0.0, 1442
        self.g.tick(x)
        self.assertIsNone(self.g.far)


class TestPatrol828(Click828):
    def setUp(self):
        super().setUp()
        self.cfg["area"].update(enabled=True, radius=0.5)
        self.g.set_centers([(50.0, 50.0, 1442), (52.0, 50.0, 1442)])
        self.g.home = None

    def ptick(self, pos, dt=0.6, **kw):
        self.clock.t += dt
        x = self.o(**kw)
        x.pos, x.facing, x.map_id = pos, 0.0, 1442
        self.g.tick(x)

    def test_patrol_needs_two_frames(self):
        self.ptick((50.0, 50.0))
        for _ in range(12):
            self.ptick((50.0, 50.0))
            if self.g.state == State.RETURNING:
                break
        self.assertEqual(self.g.state, State.RETURNING)
        self.clock.t += 1.5
        self.ptick((51.0, 50.0), dt=0.15, plates=[mplate(900, 500)])    # 1 帧：不停
        self.assertEqual(self.g.state, State.RETURNING)
        self.ptick((51.0, 50.0), dt=0.15)                                # 断了 → 重新数
        self.ptick((51.0, 50.0), dt=0.15, plates=[mplate(900, 500)])
        self.assertEqual(self.g.state, State.RETURNING)
        self.ptick((51.0, 50.0), dt=0.15, plates=[mplate(900, 500)])    # 连续 2 帧 → 停
        self.assertEqual(self.g.state, State.SEARCHING)
        self.assertFalse(self.g.walking)


if __name__ == "__main__":
    unittest.main()
