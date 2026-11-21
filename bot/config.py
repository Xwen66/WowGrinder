"""配置的默认值、读取与保存。

区域格式统一为 [left, top, width, height]（屏幕绝对坐标，1920x1080 下）。
宽或高为 0 表示“未设置”。HSV 使用 OpenCV 取值范围：H 0-179, S 0-255, V 0-255。
"""
from __future__ import annotations

import copy
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
IMAGES_DIR = os.path.join(BASE_DIR, "images")
SKILLS_DIR = os.path.join(IMAGES_DIR, "skills")

DEFAULT_CONFIG: dict = {
    "keys": {
        "target": "tab",          # 选取最近敌人
        "attack": "1",            # 开怪技能（例如 裂伤/月火术）
        "bear_form": "f1",        # 熊形态
        "cancel_form": "f1",      # 取消变形（退出熊形态）。默认=熊形态键（再按一次就退出）；
                                  # 按了退不出熊 → 做个 /cancelform 宏放到别的键上，填那个键
        "healing_touch": "5",     # 治疗之触（唯一的治疗技能，人形施放）
        "thorns": "r",            # v8.4 荆棘术（人形施放）
        "motw": "t",              # v8.4 野性印记（人形施放）
        "rejuvenation": "g",      # v8.27 回春术（宏：自带取消变形）：战斗结束血量 70%-99% 时按
        "loot": "`",              # 拾取键（没填“与目标互动键”时用它）
        "interact": "`",          # v8.27 默认 `（Esc 下面那个键）。与目标互动键（Interact with Target）：拾取 + 剥皮都按它；找不到选中姓名板时补救
        "scan_turn": "right",     # 屏幕里没有姓名板时转视角：小键盘右(关闭数字锁定时)=方向键右=right；
                                  # 如果在游戏里把“右转”绑到了小键盘6（数字锁定开），改成 num6
        "target_attacker": "x",   # v8.9 选择攻击我的怪键：游戏里做宏 /cleartarget + /targetenemy 放到 X；留空=不用（只用 Tab）
        "clear_target": "f3",     # 清除目标键：建议在游戏里做 /cleartarget 宏放到 F3（Esc 没目标时会打开游戏菜单）；留空=不按
        "turn": "d",              # 找不到怪时转身（默认关闭）
        "forward": "w",           # 前进（区域刷怪回中心用）
        "turn_left": "left",      # 左转
        "turn_right": "right",    # 右转
        "jump": "space",          # 卡住时跳
        "strafe": "q",            # 卡住时平移（左平移）
        "back": "s",              # 后退（战斗补救：退半步再按互动）
        "hotkey_stop": "f12",     # 紧急停止
        "hotkey_toggle": "f10",   # 开始/暂停
        "hotkey_area_check": "f9",  # v8.17 检查是否在范围内（写日志 + 状态栏）
        "hotkey_set_home": "f8",    # v8.17 把当前位置设为范围中心（自动保存）；v8.23 有多中心时：清空列表，当前位置 = 中心 #1
        "hotkey_add_center": "f7",  # v8.23 把当前位置添加为下一个中心（多中心巡逻 1→2→3→2→1 来回走）
        "hotkey_add_route": "f6",   # v8.39 把当前位置添加为下一个跑尸路线点（从复活点开始按顺序记）
        # v8.15 猎豹形态（“战斗形态”选 猎豹 时才用；留空 = 不按）
        "cat_form": "",           # 猎豹形态键（必填）
        "cat_builder": "",        # 攒连击点技能：爪击（必填）
        "cat_rake": "",           # 斜掠（可选，每只怪开头放一次，之后每 N 秒补一次）
        "cat_finisher": "",       # 终结技：凶猛撕咬 或 撕扯（必填）
        "cat_prowl": "",          # 潜行（可选）
        "cat_tigers_fury": "",    # 猛虎之怒（可选）
        "spirit": "]",            # v8.31 释放灵魂/复活键：游戏里做宏放到这个键（见说明）：
                                  # /run if UnitIsGhost("player") then RetrieveCorpse() elseif UnitIsDead("player") then RepopMe() end
    },
    "corpse": {                   # v8.31 自动跑尸（需要插件 v0.12）
        "enabled": True,          # 关 = 死了就停止（跟以前一样）
        "release_delay_s": 5.0,   # 死后等几秒再释放灵魂
        "retry_s": 3.0,           # 按了释放/复活没反应，隔几秒再按
        "arrive_dist": 0.3,       # 没出现“复活”框时：离尸体这么近（地图坐标）也算到了
        "safe_check": True,       # 复活前转一圈看附近有没有会打我的怪
        "near_pct": 35,           # 标记在屏幕上方 N% 以下 = 离得近
        "safe_wait_s": 30.0,      # 附近有怪最多等几秒（到时间照样复活）
        "safe_rescan_s": 5.0,     # 有怪时隔几秒再看一圈
        "run_timeout_s": 600.0,   # v8.38：跑尸每隔这么多秒写一次提醒（不再停止，一直试）
        "stuck_s": 2.5,           # 按着前进这么久位置没变 = 卡住（v8.38：跳/平移 → 后退 + 绕 60/90/120/150°，越绕越远）
        "rest_hp_pct": 90,        # 复活后血量回到多少再打怪
        "rest_mana_pct": 80,      # 复活后法力回到多少再打怪
        "rest_timeout_s": 120.0,  # 复活后最多回多久
        # v8.39 跑尸路线：从复活点（墓地）开始按顺序记的坐标点 [x, y, 地图]；释放灵魂后先逐个走完，再朝尸体走。空 = 直接朝尸体走
        "route": [],
        "route_arrive_dist": 0.15,   # 离路线点这么近（地图坐标）算走到了
        "route_shortcut_dist": 1.0,  # 走路线途中：离尸体 ≤ 这个距离、并且比离下一个路线点还近 → 不走剩下的点，直接去尸体
        "frozen_s": 60.0,            # 跑尸时位置这么多秒一点没变 → 截图 + 提示看一眼游戏画面
    },
    "heal": {
        # 只用 治疗之触：取消熊形态 → 治疗之触（站着不动读条）→ 变回熊
        "heal_pct": 40,           # 战斗中低于此血量 → 紧急治疗（v8.46：30 → 40，28% 才读条常常读不完就死）
        "heal_to_pct": 90,        # v8.11 战斗中治疗回复线：开始战斗治疗后，连续放治疗之触直到血量 ≥ 此值再变回熊
        "heal_chain_max": 6,      # v8.11 一次战斗治疗最多连放几个治疗之触（防止无限循环）
        "heal_min_mana": 10,      # v8.11 插件读到法力低于此值 → 不再连放，变回熊
        "critical_pct": 15,       # 治疗过后仍低于此血量 → 停止脚本 + 提示音
        "rest_below_pct": 60,     # v8.2：脱战后血量低于此值才变人形回血（高于就保持熊形态直接找下一只）
        "rest_to_pct": 90,        # 开始回血后：人形回血到此血量以上再变回熊
        "rest_timeout_s": 90,     # 回血超过 N 秒还没到 → 不等了，变回熊继续（v8.6 起不停止），N 秒内不再脱战回血
        "rest_min_mana": 10,      # 插件读到法力低于此值时不施法，等回蓝
        "heal_cooldown_s": 4.0,   # 两次战斗治疗之间至少间隔
        "cast_s": 3.0,            # 治疗之触读条时间（秒，含一点余量）
        "cancel_form_first": True, # 先取消熊形态再施放治疗
        "recent_hit_s": 2.5,      # v8.17：这么多秒内掉过血 = 可能还有别的怪在打 → 杀完一只先不变人形回血
        "post_fight_grace_s": 8.0, # v8：杀完怪后“战斗中”状态还会残留几秒，这段时间里照样回血（只要没在掉血）
        "ht_retry_s": 0.7,        # v8：按了治疗之触 N 秒还没开始读条 → 再按（变形后的公共冷却）
        "ht_max_presses": 5,      # v8：最多按几次还没读条 → 放弃这次治疗（日志里会写原因）
        "shift_timeout_s": 2.5,   # v8：按取消形态键 N 秒还在熊形态 → 报警
        "hp_unknown_warn_s": 3.0, # v8：战斗中读不到血量超过 N 秒 → 大声报警
        "beep": True,             # 危险停止时响提示音
        # v8.17 预留变身法力：治疗（战斗中/战后）和补 buff 都不会把法力用到低于这条线，留着变回猎豹/熊
        "reserve_cat_pct": 30,    # 变猎豹预留法力 %（日志里实测变猎豹约 27-28%）；0 = 不预留
        "reserve_bear_pct": 30,   # 变熊预留法力 %；0 = 不预留
        "reserve_learn": True,    # 自动测变身实际耗蓝；实测比设置高 → 用 实测+2
        "reserve_margin_pct": 3,  # v8.42：治疗前算“治疗后法力 ≥ 变身预留 + 这个余量”才放（治疗之触耗蓝按最近实测最贵的算）
        # v8.27 战后治疗：每次战斗结束治疗一次（代替旧的“低于 60% 才人形回血到 90%”）
        "post_enabled": True,     # 开 = 血量 < post_ht_below_pct 按生命之触（治疗之触键），否则 < 100% 按回春术键；满血不治疗
        "post_rejuv_enabled": True,  # v8.36：关 = 战后不放回春术（血量 ≥ post_ht_below_pct 就直接继续打；低于照样生命之触）
        "post_ht_below_pct": 60,  # 低于此血量用生命之触（读条，挨打也读完），高于用回春术（v8.47：70 → 60）
        "post_rejuv_below_pct": 85,  # v8.47：战后 生命之触线 ~ 这个之间用回春术；≥ 这个 → 战后不治疗
        "rejuv_cost_pct": 12,     # 回春术大约耗多少 %（实测后自动更新）
        "rejuv_no_repeat_s": 12,  # v8.30：回春术放完 N 秒内（持续时间）战后不再放回春术，也不变形；0 = 不限制
        "heal_cost_pct": 20,      # 一个治疗之触大约耗多少 %（实测后自动更新，日志里实测约 18-28%）
        "buff_cost_pct": 10,      # 一个 buff 大约耗多少 %（实测后自动更新）
        # v8.46 先回蓝再开怪：脱战后 血量 < mana_rest_hp_below 且 法力 < 变身预留 + 治疗之触耗蓝 + mana_rest_margin_pct
        # → 不拉新怪，原地等回蓝（填了喝水键就变人形喝水），到线或血量 ≥ mana_rest_hp_ok 再找怪；被打照常迎战
        "mana_rest_enabled": True,
        "mana_rest_hp_below": 60,
        "mana_rest_margin_pct": 10,
        "mana_rest_hp_ok": 90,
        "mana_rest_timeout_s": 120,
        "drink_key": "",          # 喝水键（动作条上放水/食物的键）；空 = 不喝，原地等
        "pull_min_mana_pct": 50,  # v8.47：脱战时法力低于这个 → 不开新怪（Tab/看到的怪/远处的怪都不开），先回蓝到这个；被打照常迎战
        "reserve_learn_cap_pct": 8,  # v8.47：实测变身耗蓝取最近 3 次中位数，最多 = 变身预留设置 + 这个
    },
    "cat": {
        # v8.15 猎豹形态循环（插件 v0.9 能读到连击点/能量就用读数；读不到就按次数轮转）
        "combat_form": "熊",      # 战斗形态：熊 / 猎豹
        "finisher_cp": 4,         # 连击点 ≥ 此值放终结技
        "finisher_after_builders": 4,  # 读不到连击点时：每按 N 次爪击放一次终结技
        "builder_energy": 40,     # 读得到能量时：能量 ≥ 此值才按爪击（爪击约 40-45 能量）
        "finisher_energy": 35,    # 终结技需要的能量
        "rake_energy": 35,        # 斜掠需要的能量
        "rake_every_s": 9.0,      # 斜掠：每只怪开头一次，之后每 N 秒补一次（0 = 只放一次）
        "tf_every_s": 0.0,        # 猛虎之怒：开打时放一次；>0 = 之后每 N 秒再放
        "press_interval_s": 1.0,  # 两次按技能的最短间隔（公共冷却）
        "use_prowl": False,       # 目标在近战距离、还没进战斗时：先潜行，再按开怪键（f）
    },
    "multi_bear": {
        # v8.38 战斗形态=猎豹时：判断同时被多只怪打 → 变熊抗；只剩 1 只（杀了一只后一段时间没再判断到）或脱战 → 变回猎豹
        "enabled": False,         # 开关（GUI“技能循环”页）；v8.47 默认关：来回变身太费蓝
        "fast_drop_pct": 20,      # 判断 1：最近 fast_window_s 秒内自己掉血 ≥ 这么多 %（单只同级怪一般 5 秒掉不到 10%）
        "fast_window_s": 5.0,
        "other_hits": 2,          # 判断 2：选中的怪没在打我（目标的目标不是我），但我还在掉血，fast_window_s 秒内 ≥ N 次
        "calm_s": 8.0,            # 变熊后杀了一只、并且这么多秒没再判断到多只 → 变回猎豹
        "min_bear_s": 10.0,       # 变熊后至少待这么久（省蓝，免得来回变）
        "cooldown_s": 10.0,       # 变回猎豹后这么久内不再变熊
        "min_mana_pct": 40,       # 法力低于这个 % 不变熊（变熊 + 之后变回猎豹/治疗都要蓝）
        "maul_key": "1",          # 熊形态动作条上 重殴 的键（熊形态会换一页动作条，默认 1）
        "swipe_key": "",          # 熊形态 横扫 的键（可选，留空 = 不用）
        "swipe_every_s": 6.0,     # 横扫间隔
        "press_interval_s": 1.5,  # 熊形态按技能最短间隔
    },
    "reconnect": {
        # v8.44 断线自动重连：截图认出断线弹窗/登录界面/选角色界面 → 每 interval_s 秒按一次 Enter，直到回到游戏（插件色块读得到）
        "enabled": True,
        "interval_s": 10.0,
        "confirm_s": 2.0,         # 连续认出这么久才开始按（防误判）
        "ok_s": 3.0,              # 插件色块连续读得到这么久（且没再看到登录界面）才算回到游戏
        "check_every_s": 1.0,     # 每隔几秒截图认一次（一次约 50ms）
        "blind_after_s": 30.0,    # v8.45 重连中：界面没认出来、插件也读不到 这么久 → 照样每 interval_s 按 Enter
        "lost_shot_s": 30.0,      # v8.45 平时插件读不到这么久又没认出断线界面 → 存截图+分数（10 分钟一次）
        "key": "enter",
    },
    "tab_search": {
        # v8.40 Tab 找怪（点击模式）：找怪时先按选怪键（keys.target，默认 Tab），插件 v0.13 检查目标合格才过去打
        "enabled": True,          # GUI“Tab 找怪”
        "skip_tapped": True,      # 灰名（被别人抢了）不打
        "skip_elite": True,       # 精英（含稀有精英/世界首领）不打；稀有（非精英）照打
        "skip_high": True,        # 比我高 level_diff 级以上不打（骷髅 ?? 也不打）
        "level_diff": 3,
        "skip_player": True,      # 玩家不打
        "max_per_cycle": 2,       # 一轮找怪最多按几次 Tab
        "cooldown_s": 4.0,        # 一轮没找到合格的 → 这么久内找怪不再按 Tab
        "verify_s": 0.6,          # 按完 Tab 等多久看有没有选上
        "memory_s": 30.0,         # 不合格的目标记多久
        "while_moving": True,     # GUI“移动中也按 Tab”（巡逻/回中心/朝远处走）
        "move_interval_s": 1.8,   # 移动中每隔几秒按一次
        "move_jitter_s": 0.4,     # 随机 ±
    },
    "humanize": {
        # v8.10 随机小动作 + 随机休息（只在脱战、两只怪之间做；战斗中绝不做）
        "move_enabled": True,     # 随机小幅移动：前进一下再后退回原位（或反过来）
        "move_min_ms": 150,       # 前进按住 最短(ms)
        "move_max_ms": 400,       # 前进按住 最长(ms)
        "back_ratio": 1.55,       # 后退按住时间 = 前进 × 这个数（后退比跑步慢：7 / 4.5 ≈ 1.55，这样能回到原位）
        "move_kills_min": 1,      # 每隔 1~4 只怪动一次
        "move_kills_max": 4,
        "move_idle_min_s": 30,    # 或者一直没怪打，每 30~120 秒动一次
        "move_idle_max_s": 120,
        "break_enabled": True,    # 随机休息：打 12~18 分钟，停 2~3 分钟（休息时被打照样打）
        "hunt_min_min": 12.0,
        "hunt_max_min": 18.0,
        "break_min_min": 2.0,
        "break_max_min": 3.0,
    },
    "stationary": {
        # 原地模式：不按 W/S、不点击移动；只按 Tab、攻击键、方向键原地转身
        "face_small_steps": "15,25,40",  # v8.13 标记在中间仍报面向错误：每次报错依次转这么多度，再不行扫一圈
        "face_quiet_s": 5.0,      # v8.13 转身后这么多秒没有新的面向错误 → 算面向修好了
        "plate_select_wait_s": 0.7,  # v8.14 点姓名板后等多久看有没有选上
        "max_tabs": 6,            # 连续按 N 次 Tab 没找到能打的怪 → 随机等一会儿
        "attacker_max_presses": 2,  # v8.9 战斗中先按“选择攻击我的怪键”最多几次，再改用 Tab（每轮 Tab 等待后重来）
        "attacker_interval_s": 0.8, # v8.9 两次按“选择攻击我的怪键”的间隔
        "pause_min_s": 2.0,       # 随机等待 最短
        "pause_max_s": 4.0,       # 随机等待 最长
        "tab_interval_s": 0.6,    # 两次 Tab 最短间隔
        "turn_search": True,      # 一轮 Tab 没找到 → 原地按方向键转一点视角（不走路）
        "turn_after_rounds": 1,   # 第几轮没找到开始转
        "wait_in_range_s": 5.0,   # 选中但够不到/判断不了：最多等怪过来 N 秒，再换目标
        "face_interval_s": 0.5,   # 两次转身间隔（v8.6：0.8 → 0.5）
        "face_max_attempts": 6,   # 转这么多次还不行 → Tab 换目标
        # v8 面向
        "no_damage_s": 4.5,       # 原地模式：N 秒没打出伤害才算“打不出伤害”（熊挥击约 2.5 秒一次，至少 4）
        "use_marker": True,       # 用插件画在目标姓名板上的标记判断怪在左/右/身后
        "face_ok_deg": 60,        # 标记显示怪偏离正前方超过 N 度 → 主动转过去（近战要求在前方 ±90° 内）
        "fov_deg": 90,            # 游戏水平视角（度），用来把标记的屏幕位置换算成角度
        "turn_rate_dps": 180,     # 按住方向键每秒转多少度（会用插件朝向自动校准）
        "face_settle_s": 0.25,    # 转完等 N 秒再看标记（v8.6：0.5 → 0.25，期间 50ms 循环）
        # v8.6 面向
        "face_center_deg": 15,    # 标记在正前方 ±N° 内：2.5 秒内有伤害就当面向错误是误报，不转
        "face_offcenter_deg": 25, # 标记偏离超过 N°（或贴边/身后）→ 用下面的短时间判断“打不出伤害”
        "no_damage_offcenter_s": 2.5,
        # v8.1 拉怪
        "pull_ranged": True,      # 目标在约 30 码内（插件距离块=黄）→ 按开怪键把它拉过来，而不是 Tab 跳过
        "pull_wait_s": 8.0,       # 按了开怪键最多等 N 秒怪过来/进战斗，再换目标
        "pull_retry_s": 2.5,      # N 秒还没进战斗 → 再按一次开怪键
        "pull_max_presses": 2,    # 一只怪最多按几次开怪键
        # v8.3 没目标时左键点姓名板选怪 + 太远清目标
        "plate_click": True,      # 没目标时：屏幕上有“未选中姓名板”模板匹配到的怪 → 点最靠近屏幕中间的那个（没录模板就只用 Tab）
        "plate_click_button": "left",
        "plate_click_interval_s": 0.8,
        "plate_max_fails": 3,     # 连续点 N 次没选上 → 这一轮改按 Tab
        "plate_restore_mouse": True,  # 点完把鼠标移回原来的位置
        "plate_ui_margin": [320, 140],  # 屏幕左上角这么大的区域（头像/插件色块）里的姓名板不点
        "clear_far": True,        # 目标在 30 码外（插件距离块红）→ 按清除目标键，再重新找
    },
    "buffs": {
        # v8.4：脱战、没目标时检查 荆棘术 / 野性印记（插件 v0.8 报告），没有就变人形补上再变回熊
        "enabled": True,
        "thorns": True,
        "motw": True,
        "min_mana": 20,           # 法力低于此值先不补（过 retry_s 再看）
        "retry_s": 30,            # 没补上 / 蓝不够 → 过 N 秒再试
        "max_tries": 3,           # 一个 buff 连续按 N 次都没上 → 放弃 giveup_s 秒
        "giveup_s": 300,
        "cast_wait_s": 2.0,       # 按完等 N 秒看 buff 有没有上（含公共冷却）
        "refresh_low": True,      # 剩不到 60 秒也补
        "fallback_min_thorns": 9, # 插件读不到 buff 时：按时间补（分钟）；0 = 读不到就不补
        "fallback_min_motw": 29,
    },
    "meta": {
        "migrations": [],         # 已经做过的一次性配置升级（别手动改）
    },
    "log": {
        "enabled": True,          # v8：每次运行在 logs/ 下写一个日志文件
        "keep_files": 20,         # 最多保留 N 个日志文件
        "max_mb": 5,              # 单个文件超过 N MB → 换新文件
        "keep_shots": 50,         # v8.25 logs/shots 最多保留 N 张截图（最新的）
    },
    "face_click": {
        # v8.17：战斗中面向错误 → 右键点目标姓名板（插件 v0.10 的标记在姓名板正中间），角色自动转向，不再左右转
        "enabled": True,
        "button": "right",            # 右键（left = 左键只选中不转向）
        "cooldown_s": 0.9,            # 两次点击至少间隔
        "melee_back_ms": 300,         # v8.25 近战内转过身还报面向错误 → 后退半步（按住后退键 N ms；0 = 不后退）
        "melee_backs": 2,             # v8.26 贴脸 + 标记在中间还报面向错误：先后退几次（不盲转），还不行右键点一次标记
        "melee_cycles": 3,            # v8.26 “后退 N 次 + 右键一次”最多循环几轮，还不行 → 放弃（怪在打我就不换目标）
        "in_melee": False,            # v8.24 近战距离内面向错误也右键点（默认关：近战内只转身，不右键）
        "max_clicks": 2,              # 连续点几次还报面向错误 → 改用原来的左右转/扫一圈
        "restore_mouse": True,        # 点完把鼠标移回原位
        "offset_y": 0,                # 点击位置相对标记中心往下偏移（像素）
    },
    "recover": {
        # 战斗补救：面向错误 / 太远 / 普通攻击够不到 / 打不出伤害 → 按互动键(F) 让点击移动转身贴过去
        "enabled": True,
        "first_damage_s": 4.0,    # 开打后 N 秒还没打出伤害 → 视为卡住，开始补救
        "no_damage_s": 2.0,       # 战斗中 N 秒没打出伤害 → 补救
        "check_interval_s": 1.5,  # 每次补救后等 N 秒看效果
        "max_fails": 3,           # 连续 N 次补救无效 → 清目标、跳过这只怪
        "turn_ms": 150,           # 第 2 次补救：转一点视角
        "back_ms": 300,           # 第 3 次补救：后退半步
        "skip_s": 30,             # 跳过的怪 N 秒内不再点它
    },
    "move": {
        "mode": "stationary_tab",     # stationary_tab=原地不动 + Tab 选怪（默认） / click=点击移动走过去（旧）
        "enabled": True,              # 选中怪后按“与目标互动”自动走过去（需开启游戏的点击移动）
        "approach_timeout_s": 12,     # 走过去超过 N 秒还没进战斗 → 放弃这个目标
        "walk_far": True,             # v8.46：选中的怪离得远（插件距离 far/ranged）→ 按住前进朝它走，到近战距离再按开怪键
        "walk_far_max_s": 15.0,       # v8.46：最多走这么久，还没到 → 换目标
        "walk_far_stuck_s": 2.5,      # v8.46：走的时候坐标这么久没变 → 改成右键点它走过去
        "repress_interval_s": 2.0,    # 接近途中/面向错误时，每隔 N 秒再右键一次选中姓名板
        "click_interval_s": 3.0,      # v8.24 任意两次右键至少隔 N 秒；点完先判断（角色没在走近/还报错）才再点
        "opener_first": False,        # 先按开怪键（如月火术）再走过去
        "open_with_attack": True,     # v8.21：点击模式走到怪身边/进战斗时先按一次开怪键（猎豹和熊都用 f）
    },
    "nameplate": {
        "enabled": True,              # 有模板图片时才会生效
        "select_method": "click",     # click=鼠标点最近的未选中姓名板 / tab=只按 Tab
        "click_button": "right",      # 右键：点击移动下会自动转向、走过去并开始攻击
        "tab_fallback": False,        # 点击多次没选上时按 Tab（默认改为转一下视角）
        "click_offset_y": 0,          # 点击位置 = 姓名板中心 + 向下偏移(像素)
        "threshold_selected": 0.8,
        "threshold_unselected": 0.8,
        "multiscale": True,
        "scales": "0.85,1.0,1.2",        # 多尺度：远处姓名板更小
        "downscale": 0.5,             # 搜索前把画面缩小，加快速度（1=不缩；识别不准时调到 0.75 或 1）
        "color_match": True,          # 彩色匹配（区分红/黄姓名板）
        "brightness_weight": 0.6,     # 区分选中/未选中时亮度差的权重
        "nms_iou": 0.3,
        "max_click_fails": 3,         # 连续点击 N 次没选上 → 转一下视角（或按 Tab）
        "turn_when_offscreen": True,  # 有目标但屏幕上看不到选中姓名板 → 转身
        "offscreen_turn_ms": 200,
        "max_offscreen_turns": 8,     # 转这么多次还看不到 → 放弃目标
        "target_confirm": "hp",       # 判断“已选中目标”：hp=目标血条 / nameplate=选中姓名板 / either=任一
        # v8.28：插件 v0.11 给每个能打的怪的姓名板画 黄|蓝 标记 → 只点标记（插件旧/读不到时才用模板）
        "use_enemy_markers": True,
        "skip_top_pct": 23,           # 屏幕最上面这么多 %（远处）的姓名板不点（1080p ≈ 上面 250 像素；0 = 都点）
        "confirm_px": 25,             # 点之前再截一张图：标记还在原位（差不到这么多像素）才点
        "verify_s": 0.6,              # 点完这么多秒内要选上怪，否则算点空
        "bad_s": 8.0,                 # 点空的位置这么多秒内不再点
        "bad_radius_px": 60,          # “那个位置”= 这个半径以内
        "patrol_frames": 2,
        "stop_settle_s": 0.3,         # 看到标记 → 先松开移动键停下，停稳这么多秒再右键点           # 巡逻路上：连续这么多帧都看到标记才停下
        # v8.28：远处的怪只显示红色名字 → 转过去朝它走，出了标记再点（不按互动键）
        "far_names": True,
        "engage_any": True,       # v8.32：不在战斗中看到能打的怪的插件标记（连续 2 帧，最上面一条不算）→ 先停下右键点去打；
                                  # 拾取尸体/补 buff/等一会/回中心 都放到打完再做（回血没到线、读条中、跑尸、范围外不打）
        "far_walk_max_s": 10.0,       # 朝一个名字最多走这么久
        "far_lost_s": 1.5,            # 名字看不到这么久 → 停下
        "far_skip_s": 5.0,            # 走过去没出标记/放弃 → 这么久内不再朝远处走（v8.34：20 → 5）
        # v8.34：红名在整个游戏窗口里找；这些界面区域不找（按窗口比例 [x, y, 宽, 高]，0-1）
        "red_ui_exclude": [[0.80, 0.0, 0.20, 0.25],   # 右上：buff + 小地图
                           [0.86, 0.0, 0.14, 1.0],    # 右边一列：任务追踪 / 右侧动作条
                           [0.74, 0.68, 0.26, 0.32],  # 右下：任务/提示面板
                           [0.0, 0.70, 0.20, 0.30]],  # 左下：聊天框
        "far_turn_deg": 12.0,         # 名字偏离正前方超过这么多度 → 先转身
    },
    "scan": {
        "tap_ms": 200,                # 每次按转向键的时长（约 35°）
        "settle_ms": 350,             # 转完等姓名板出现
        "patrol_look_s": 1.0,         # v8.23 中心之间走的路上：每 N 秒看一次有没有能打的怪（姓名板/选中的怪）
        "patrol_tab_s": 2.5,          # v8.23 原地模式在路上每 N 秒按一次 Tab 找怪
        "patrol_resume_s": 3.0,       # v8.23 停下打怪/没选上后，N 秒内不再因为看到姓名板停下
        "patrol_timeout_s": 300,      # v8.23 去下一个中心最多走 N 秒（被打/打怪会重新计时）
        "spin_home": True,            # v8.19：找怪转视角累计转满 spin_deg 度还没找到能打的怪 → 走回范围中心
        "spin_deg": 360,              # 累计转多少度算“转了一圈”
        "spin_near_ratio": 0.2,       # 已经在中心 半径×此比例 以内 → 不走，等一会儿接着找
        "spin_wait_s": 3.0,           # 在中心附近时等几秒再找
    },
    "addon": {
        "enabled": True,              # 优先读 BotStatus 插件色块；读不到心跳时自动改用截图识别
        "x": 0, "y": 0,               # 色块左上角（与插件 CFG.x/CFG.y 一致）
        "block": 10,                  # 色块边长
        "calib_tolerance": 12,        # 校准灰偏离 128 超过此值 → 提示“伽马/亮度不是默认”（仅提示）
        "stale_s": 3.0,               # 心跳超过 N 秒不变 → 视为插件失联
    },
    "area": {
        "enabled": False,             # 区域刷怪：脱战且离中心超过半径 → 走回中心（需要 BotStatus v0.5）
        "center_x": 50.0,             # 中心坐标（和游戏里显示的坐标一样，0-100）
        "center_y": 50.0,
        "radius": 3.0,                # 半径（地图坐标单位；1 单位 ≈ 10-40 码，视地图大小）
        "arrive_ratio": 0.5,          # 走到 半径×此比例 以内算到达
        "map_id": 0,                  # 中心所在地图 ID（0=不检查）；地图变了就不回中心
        "turn_rate_dps": 180,         # 按住转向键时每秒转多少度（游戏默认约 180）
        "face_tolerance_deg": 25,     # 朝向偏差超过此值：先停下原地转
        "walk_correct_deg": 8,        # 走路中偏差超过此值：边走边微调
        "return_timeout_s": 90,       # 回中心超过 N 秒 → 停止
        "stuck_s": 3.0,               # 按着前进 N 秒位置没变 → 跳/平移脱困，2 次无效 → 停止
        "move_eps": 0.03,             # 位置变化小于此值视为没动
        "home_on_start": True,        # v8.17 开始打怪时把当前位置记为中心（关掉 = 用上面保存的中心坐标）
        "auto_switch_center": True,   # v8.46：离当前中心超出半径、但在另一个中心半径里 → 直接换成那个中心，不走回去
        "leash_ratio": 1.3,           # v8.17 点击模式：脱战追怪时离中心超过 半径×此值 → 放弃这只怪，走回中心
        "centers": [],                # v8.23 多中心巡逻 [[x, y, 地图], ...]；有 2 个以上：转一圈没找到怪 → 直线走去下一个，循环
    },
    "loot": {
        "enabled": True,
        "delay_ms": 700,          # 怪死后等多久再拾取
        "wait_ms": 1200,          # 按拾取键后等待（自动拾取完成）
        "residual_wait_s": 6.0,   # v8.17：杀完怪后战斗状态还在、但没掉血 → 最多等 N 秒再按 x 找怪（多半是残留状态；掉血马上找）
        "pending_expire_s": 180,  # v8.7：战斗中/回血前记下的尸体，超过 N 秒（从回血结束算）就不捡了
        "skin": True,             # v8.2 剥皮：拾取完再按一次交互键剥皮
        "skin_delay_ms": 300,     # 拾取完到按剥皮之间等多久
        "skin_cast_s": 2.0,       # 剥皮读条大概多久（插件读不到施法时按这个时间等）
        "skin_max_s": 5.0,        # 剥皮最多等 N 秒
        "bagfull_detect": True,   # v8.29：连按互动键没反应 → 认定背包满，本次运行关掉拾取/剥皮，只打怪
        "bagfull_presses": 5,     # v8.29：连续几次没反应算背包满（没读条、尸体一直“可拾取”）
        "bagfull_two_corpses": False,  # v8.29：要两具尸体都这样才认定（更保险，默认关）
        "full_bag_money_only": True,   # v8.48：背包满了还拾取（只拿到钱，物品不等），不剥皮，记住捡过的尸体
        "full_bag_wait_ms": 500,       # v8.48：背包满时按完拾取等多久（钱自动拾取）
        "full_bag_close_key": "esc",   # v8.48：关拾取窗口的键（只在选中尸体时按，不会开游戏菜单）
        "full_bag_close_s": 1.5,       # v8.48：按完拾取多久内看到选中的尸体才按关窗口键
        "full_bag_memory_s": 300,      # v8.48：捡过钱的尸体记多久（不再回去捡）
        "full_bag_memory_dist": 0.15,  # v8.48：离捡过的位置这么近（地图坐标 0-100）算同一具尸体
    },
    "regions": {
        "player_hp": [0, 0, 0, 0],
        "target_hp": [0, 0, 0, 0],
        "combat": [0, 0, 0, 0],
        "action_bar": [0, 0, 0, 0],
        "dead": [0, 0, 0, 0],
        "target_frame": [0, 0, 0, 0],
        "nameplates": [160, 60, 1600, 640],   # 姓名板搜索区（默认屏幕中上部）
    },
    "colors": {
        "player_hp_lo": [35, 80, 60],
        "player_hp_hi": [85, 255, 255],
        "target_hp_lo": [35, 80, 60],
        "target_hp_hi": [85, 255, 255],
        "combat_lo": [0, 120, 120],
        "combat_hi": [10, 255, 255],
    },
    "detect": {
        "bar_mode": "rightmost",     # rightmost=最右侧填充列（不受中间数字影响）/ count=填充列占比
        "column_fill_ratio": 0.4,    # 一列中有多少比例像素符合颜色才算“填充”
        "combat_mode": "template",   # template / color / none
        "combat_color_ratio": 0.05,  # color 模式下：区域内符合颜色像素占比阈值
        "template_threshold": 0.8,
        "dead_pct": 1.0,             # 血量 <= 此值视为 0
    },
    "rotation": {
        "mode": "timed",             # timed=按优先级+最短间隔轮转（默认，不用截图标）/ template=识别图标是否亮起
        "brightness_ratio": 0.75,    # 图标亮度低于模板该比例 → 视为冷却/没怒气
        "skills": [
            {"name": "裂伤", "image": "skills/mangle.png", "key": "2", "priority": 1,
             "threshold": 0.8, "cooldown_ms": 1500, "enabled": True},
            {"name": "痛击", "image": "skills/thrash.png", "key": "3", "priority": 2,
             "threshold": 0.8, "cooldown_ms": 1500, "enabled": True},
            {"name": "重殴", "image": "skills/maul.png", "key": "4", "priority": 3,
             "threshold": 0.8, "cooldown_ms": 1500, "enabled": True},
            {"name": "横扫", "image": "skills/swipe.png", "key": "7", "priority": 9,
             "threshold": 0.8, "cooldown_ms": 1500, "enabled": True},
        ],
    },
    "safety": {
        "delay_min_ms": 120,         # 每次按键后的随机间隔
        "delay_max_ms": 320,
        "hold_min_ms": 30,           # 按键按住时长
        "hold_max_ms": 90,
        "tick_ms": 150,              # 主循环间隔
        "stuck_timeout_s": 25,       # 战斗中目标血量 N 秒不下降 → 提醒 + 截图、重新计时（v8.43 起永远不停止）
        "error_retry_max": 5,        # v8.43 起不用了：出错永远重试（连续出错等 2→4→8→16→30 秒）
        "error_retry_window_s": 600, # v8.43 起不用了
        "retarget_s": 6,             # 选中的怪 N 秒没掉血且未进战斗 → 换一个目标
        "stop_on_death": True,       # v8.43 起不用了：死了不停止（跑尸关着就等活过来）
        "death_hp_seconds": 2.0,     # 自己血量为 0 持续 N 秒 → 视为死亡（不停止）
        "failsafe": True,            # v8.43 起不用了：去掉“鼠标甩到左上角急停”（只用 F12 / 停止按钮 / 关窗口）
    },
    "misc": {
        "debug_preview": False,
        "capture_delay_s": 3,        # 框选/截图前的倒计时（用于切到游戏）
        "monitor": 1,
        "window_check": True,        # 只在游戏窗口位于前台时按键
        "window_keywords": "World of Warcraft,魔兽世界,WoW",
        "window_exes": "Wow.exe,WowClassic.exe,WowB.exe,WowT.exe",  # v8.12 按进程判断游戏是否在前台（优先于标题关键字）
    },
}


def _merge(defaults: dict, loaded: dict) -> dict:
    out = copy.deepcopy(defaults)
    for k, v in (loaded or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out

# v8.25：不再使用的设置项（运行时从配置里删掉）
REMOVED_KEYS = {
    "keys": ["cat_opener"],
    "move": ["combat_interact_interval_s"],
    "heal": ["gcd_ms"],
    "safety": ["turn_enabled", "turn_every_tabs", "turn_hold_ms", "max_no_target_s", "max_idle_s"],
    "stationary": ["face_turn_ms", "face_recent_dmg_s", "face_small_deg", "face_small_tries"],
    "scan": ["max_turns"],
    "addon": ["tolerance"],
}


def _migrate(cfg: dict, loaded: dict) -> dict:
    """旧配置升级：愈合键 → 治疗之触键；旧默认治疗线 40% → 30%。"""
    lk, lh = loaded.get("keys", {}) or {}, loaded.get("heal", {}) or {}
    if "healing_touch" not in lk and lk.get("regrowth"):
        cfg["keys"]["healing_touch"] = lk["regrowth"]
    lm = loaded.get("move", {}) or {}
    if "mode" not in lm:                              # v6 及以前：改为原地 Tab 模式
        cfg["move"]["mode"] = "stationary_tab"
    if "mode" in lh and "critical_pct" not in lh:   # v5 及以前的配置
        if lh.get("heal_pct") == 40:
            cfg["heal"]["heal_pct"] = DEFAULT_CONFIG["heal"]["heal_pct"]
        if lh.get("heal_cooldown_s") == 8.0:
            cfg["heal"]["heal_cooldown_s"] = DEFAULT_CONFIG["heal"]["heal_cooldown_s"]
        if lh.get("rest_timeout_s") == 60:
            cfg["heal"]["rest_timeout_s"] = DEFAULT_CONFIG["heal"]["rest_timeout_s"]
    if not (lk.get("cancel_form") or "").strip():   # v8：取消形态键留空 → 明确填成熊形态键（再按一次退出熊）
        cfg["keys"]["cancel_form"] = cfg["keys"].get("bear_form") or "f1"
    done = cfg.setdefault("meta", {}).setdefault("migrations", [])
    if "face_v86" not in done:                       # v8.6：转身等待缩短（只改还是旧默认值的）
        ls = loaded.get("stationary", {}) or {}
        if ls.get("face_settle_s") == 0.5:
            cfg["stationary"]["face_settle_s"] = 0.25
        if ls.get("face_interval_s") == 0.8:
            cfg["stationary"]["face_interval_s"] = 0.5
        done.append("face_v86")
    if "clear_target_f3" not in done:                # v8.3：清除目标键 Esc → F3（/cleartarget 宏），只改一次
        if (lk.get("clear_target") or "").strip().lower() == "esc":
            cfg["keys"]["clear_target"] = "f3"
            cfg["meta"]["clear_target_changed"] = True
        done.append("clear_target_f3")
    for old in ("mode", "rest_pct", "regrowth_cast_ms"):
        cfg["heal"].pop(old, None)
    for old in ("regrowth",):
        cfg["keys"].pop(old, None)
    keys = cfg["keys"]
    for name, val in list(keys.items()):             # v8.27：键位统一写法（· → `，全角字母/数字 → 半角）
        if isinstance(val, str):
            keys[name] = norm_key(val)
    cfg.pop("flee", None)                            # v8.37：去掉 v8.35 的“怪逃跑追着打”（旧配置里的 flee 段丢掉）
    if "far_skip_v834" not in done:                  # v8.34：误判后暂停 20 → 5 秒（只改还是旧默认值的）
        if (loaded.get("nameplate", {}) or {}).get("far_skip_s") in (20, 20.0):
            cfg["nameplate"]["far_skip_s"] = 5.0
        done.append("far_skip_v834")
    if "keys_v827" not in done:                      # v8.27：g 改成回春术，互动/拾取键 g / · → `（只改一次）
        for name in ("interact", "loot"):
            if (keys.get(name) or "").strip().lower() in ("g", "`"):
                if keys.get(name) != "`":
                    cfg["meta"]["interact_changed"] = True
                keys[name] = "`"
        done.append("keys_v827")
    for sec, names in REMOVED_KEYS.items():          # v8.25：删掉不再使用的设置项
        for n in names:
            (cfg.get(sec) or {}).pop(n, None)
    return cfg


def load_config(path: str = CONFIG_PATH) -> dict:
    if not os.path.exists(path):
        return copy.deepcopy(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8-sig") as f:
        loaded = json.load(f)
    return _migrate(_merge(DEFAULT_CONFIG, loaded), loaded)


def save_config(cfg: dict, path: str = CONFIG_PATH) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def region_set(region) -> bool:
    return bool(region) and len(region) == 4 and region[2] > 0 and region[3] > 0


_KEY_ALIAS = {"·": "`", "｀": "`", "‘": "`", "grave": "`", "backtick": "`", "backquote": "`", "oem_3": "`",
              "〜": "`", "　": " "}


def norm_key(key: str) -> str:
    """v8.27：键位写法统一。· / ｀ / grave → `（美式键盘 Esc 下面那个键，VK_OEM_3）；全角字母数字 → 半角；小写。"""
    if not isinstance(key, str):
        return key
    out = []
    for ch in key:
        ch = _KEY_ALIAS.get(ch, ch)
        o = ord(ch)
        if 0xFF01 <= o <= 0xFF5E:                    # 全角 ！-～ → 半角
            ch = chr(o - 0xFEE0)
        out.append(ch)
    k = "".join(out).strip()
    k = _KEY_ALIAS.get(k.lower(), k)
    return k.lower() if len(k) > 1 or k.isalpha() else k


def key_conflicts(cfg: dict) -> list[str]:
    """v8.27：回春术键 / 互动键 和别的动作键撞了 → 返回说明文字。"""
    k = cfg.get("keys", {})
    names = {"attack": "开怪", "bear_form": "熊形态", "healing_touch": "治疗之触", "thorns": "荆棘术", "motw": "野性印记",
             "target_attacker": "选攻击我的怪", "clear_target": "清除目标", "cat_form": "猎豹形态", "cat_builder": "爪击",
             "cat_rake": "斜掠", "cat_finisher": "终结技", "cat_prowl": "潜行", "cat_tigers_fury": "猛虎之怒",
             "back": "后退", "forward": "前进", "strafe": "平移", "jump": "跳跃", "target": "选怪", "cancel_form": "取消变形",
             "rejuvenation": "回春术", "interact": "与目标互动", "spirit": "释放灵魂/复活"}
    out = []
    for mine in ("rejuvenation", "interact", "spirit"):
        v = norm_key(k.get(mine) or "")
        if not v:
            continue
        for other, cn in names.items():
            if other == mine or (mine == "interact" and other == "rejuvenation"):
                continue
            if norm_key(k.get(other) or "") == v:
                out.append(f"{names[mine]}键「{v}」和{cn}键一样")
    return out


def is_cat(cfg: dict) -> bool:
    return str((cfg.get("cat") or {}).get("combat_form", "熊")).strip().lower() in ("猎豹", "豹", "cat")


def cat_missing(cfg: dict) -> list[str]:
    """v8.15：战斗形态=猎豹 时必填的键。"""
    if not is_cat(cfg):
        return []
    k = cfg.get("keys", {})
    need = {"cat_form": "猎豹形态键", "cat_builder": "攒连击点键（爪击）", "cat_finisher": "终结技键（凶猛撕咬/撕扯）"}
    out = [label for key, label in need.items() if not str(k.get(key) or "").strip()]
    c = cfg.get("cat") or {}
    if c.get("use_prowl") and not str(k.get("cat_prowl") or "").strip():
        out.append("潜行键（开了“先潜行”）")
    return out


def missing_required(cfg: dict) -> list[str]:
    """开始前必须配置的项目。用 BotStatus 插件时血条/战斗区域只是备用，不强制。"""
    missing = []
    if cfg["rotation"]["mode"] == "template" and not region_set(cfg["regions"].get("action_bar")):
        missing.append("动作条区域（技能识别模式需要）")
    if not cfg.get("addon", {}).get("enabled", True):
        names = {"player_hp": "自己血条区域", "target_hp": "目标血条区域"}
        missing += [label for key, label in names.items() if not region_set(cfg["regions"].get(key))]
        if cfg["detect"]["combat_mode"] != "none" and not region_set(cfg["regions"].get("combat")):
            missing.append("战斗状态区域（或把战斗识别改为 none）")
    return missing


NAMEPLATE_DIRS = {
    "selected": os.path.join(IMAGES_DIR, "nameplates", "selected"),
    "unselected": os.path.join(IMAGES_DIR, "nameplates", "unselected"),
}


def list_nameplate_templates(kind: str) -> list[str]:
    d = NAMEPLATE_DIRS[kind]
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, f) for f in os.listdir(d)
                  if f.lower().endswith((".png", ".jpg", ".jpeg", ".bmp")))


def image_path(rel: str) -> str:
    return rel if os.path.isabs(rel) else os.path.join(IMAGES_DIR, rel)
