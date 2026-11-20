--[[
BotStatus：把角色状态画成屏幕左上角的纯色方块，给 WowGrinder（Python）截图读取。
兼容《魔兽世界：无限》1.60.1（TOC 16001，带 Midnight 的"机密值/secret values"限制）。

v0.13：给 WowGrinder v8.40“Tab 找怪”用：目标的 玩家/精英/稀有 和 等级差。
      块 36 = R=目标是玩家，G=精英（elite / rareelite / worldboss），B=稀有（rare，非精英）；
      块 37 = R=“是不是玩家”读得到，G=“精英/稀有”读得到，B=1（v0.13 签名）；
      二进制 C 行第 23 块洋红 = v0.13 签名；
      块 38+39 = 等级差 6 位（38 是高 3 位）：0=没目标/读不到，63=骷髅（??），其余 = 目标等级 - 自己等级 + 32（限制在 1..62）。
v0.12：自动跑尸 + 姓名板显示距离调到最大。
      块 34 = 死亡信息（R=“释放灵魂”框开着，G=“复活”框开着/能在尸体旁复活，B=知道尸体坐标），
      块 35 = R=姓名板距离 CVar 设置成功，G=灵魂状态时标记改成“会打我的怪”，B=1（v0.12 签名）；
      二进制 C 行 y=50..59：尸体 X(7 块) 尸体 Y(7 块) 复活还要等几秒(4 块) 姓名板显示距离(4 块)。
      灵魂状态下尸体不在当前小地图上 → 往上找父地图，A/B 行的 坐标/地图 也改用同一张地图（Python 用同一坐标系走）。
      登录 / /reload 时把 nameplateMaxDistance 调到客户端允许的最大值（一般最大 60 码），聊天框打印实际值。
v0.11：每个能打的怪（活的、敌对、不是玩家、没被别人抢）的姓名板正中间画一个 黄|蓝 小标记（目标自己的还是 青|洋红）；
      WowGrinder 只点这些标记，不再靠截图模板认姓名板。块 28 绿 = v0.11 签名；
      块 32 = 标记状态（R=1 签名，G=姓名板接口能用，B=有怪的状态读不到/机密值），块 33 = 屏幕上姓名板个数（3 位，最多 7）。
v0.10：目标姓名板标记改到姓名板正中间（WowGrinder 面向错误时右键点它，角色自动转向）；块 28 改成白色 = v0.10 签名（v0.8/0.9 是黄）。
v0.9：块 29 = 连击点（3 位 0-7），块 30 = 能量档位（0-7，×100/7 ≈ 能量%），块 31 = 有效位（R=连击点可读 G=能量可读 B=1 签名）。
v0.8：块 25 = 荆棘术，块 26 = 野性印记（绿=身上有，黄=不到 60 秒就消失，黑=没有，蓝=没学会，洋红=读不到）；
      块 27 青 + 块 28 黄 = v0.8 签名（版本块 3 位已用满，仍为白）。
      检测顺序（全部 pcall，机密值当读不到）：C_UnitAuras.GetPlayerAuraBySpellID(各等级 ID) →
      C_UnitAuras.GetAuraDataByIndex/GetBuffDataByIndex 逐个比 spellId/名字 → 旧 UnitBuff。
v0.7.1：块 24 = 游戏菜单（Esc 菜单）开着=白，Python 看到会按一次 Esc 关掉。
v0.7：块 22 = 目标姓名板标记状态，块 23 = 面向错误计数器（0-7 循环，颜色=3 位）；
      在目标姓名板上方画一个 青|洋红 小标记（锚在 C_NamePlate.GetNamePlateForUnit('target')，pcall），
      Python 在屏幕上找它，算出怪在左/右/身后多少度；版本块改为白。
v0.6：给“原地 Tab 打怪”用：编码不变，版本块改为黄；块 13（目标在打我）、块 16（普通攻击够不够得到）全部 pcall。
v0.5：普通攻击距离（动作条上的“攻击”格子 + IsActionInRange）、面向/太远/视线 3 个独立报错块、
      距上次打出伤害的秒数；进度条改为优先用 当前值/最大值（机密值安全），修复战斗中读不到血量。
v0.4：抗伽马/亮度。所有颜色只用 0 或 255 的通道值（8 种纯色），数值用二进制编码。
布局（物理像素，从屏幕左上角 (0,0) 开始；默认每块 10x10）：
  第 1 行 y=0..9   ：24 个状态色块，第 i 块 x = i*10 .. i*10+9
  进度条 y=10..25  ：4 条 100x4（绿色填充、黑底）自己血量 / 目标血量 / 怒气 / 法力
  二进制 A 行 y=30..39：坐标 X(7 块) 坐标 Y(7 块) 朝向(7 块) 距上次伤害秒数(4 块)
  二进制 B 行 y=40..49：地图 ID(7) 自己血量%(4) 目标血量%(4) 怒气%(4) 法力%(4) 版本(1) 校准灰(1)
  每块 3 位（R,G,B 各 1 位），一个数值 = [有效位][数据位][4 位校验]，校验 = (各 4 位段之和 + 5) % 16
编码表见 addon/README_WA.md。

这段代码也可以整段粘进 WeakAuras 的"初始化时(On Init)"自定义代码里运行。
不要同时启用插件版和 WA 版。
]]
if _G.BotStatusFrame then return end

-------------------------------------------------------------------- 配置
local CFG = {
  x = 0, y = 0,        -- 左上角偏移（物理像素）
  block = 10,          -- 方块边长（物理像素）
  barWidth = 100,      -- 进度条宽（物理像素），1 像素 = 1%
  barHeight = 4,
  binTop = 30,         -- 二进制行的 y
  interval = 0.05,     -- 刷新间隔（秒），20 次/秒
  errorHold = 1.5,     -- 红字报错保持多少秒
  -- 近战范围判定用的技能（ID 或名字都行，按顺序取第一个能判断的）
  meleeSpells  = { 6807, 779, 5211, 6795, "槌击", "挥击", "重击", "低吼", "Maul", "Swipe", "Bash", "Growl" },
  -- 远程（约 30 码）判定用的技能
  rangedSpells = { 8921, 5176, "月火术", "愤怒", "Moonfire", "Wrath" },
  -- v0.8 buff 检测（各等级法术 ID + 中英文名字；客户端语言的名字会用 ID 再查一次）
  buffs = {
    { block = 25, ids = { 467, 782, 1075, 8914, 9756, 9910, 26992, 53307, 305497 }, names = { "荆棘术", "Thorns" } },
    { block = 26, ids = { 1126, 5232, 6756, 5234, 8907, 9884, 9885, 26990, 48469, 21849, 21850, 26991, 48470, 289318 },
      names = { "野性印记", "野性赐福", "Mark of the Wild", "Gift of the Wild" } },
  },
  buffLowSec = 60,     -- 剩余不到 N 秒算“快没了”（黄）
}

-------------------------------------------------------------------- 工具
local issecret = _G.issecretvalue or function() return false end
-- 机密值返回 nil，避免比较/运算时报错
local function plain(v)
  if issecret(v) then return nil end
  return v
end

local C = {
  black   = {0, 0, 0},
  white   = {1, 1, 1},
  red     = {1, 0, 0},
  green   = {0, 1, 0},
  blue    = {0, 0, 1},
  yellow  = {1, 1, 0},
  cyan    = {0, 1, 1},
  magenta = {1, 0, 1},   -- 统一表示"未知/读不到"
  -- v0.4 起只用 0/1 通道（抗伽马）；不再使用橙色/灰色
}

-------------------------------------------------------------------- 画布
local frame = CreateFrame("Frame", "BotStatusFrame", nil) -- 不挂 UIParent：Alt+Z 隐藏界面时也照样显示
frame:SetFrameStrata("TOOLTIP")
frame:SetFrameLevel(9999)

local NUM_BLOCKS = 40   -- v0.13：36-39 = 目标 玩家/精英/等级差
local NUM_BARS = 4
local BIN_A, BIN_B, BIN_C = 25, 25, 25
local blocks, bars, binA, binB, binC = {}, {}, {}, {}, {}

local function PixelScale()
  local _, h = GetPhysicalScreenSize()
  if not h or h <= 0 then return 1 end
  return 768 / h   -- 无父框体时，此缩放下 1 个单位 = 1 个物理像素
end

local function Layout()
  frame:SetScale(PixelScale())
  local B = CFG.block
  frame:SetSize(math.max(BIN_B * B, NUM_BLOCKS * B, CFG.barWidth), CFG.binTop + 3 * B)
  -- 无父框体：锚在屏幕左上角，偏移单位 = 本框体单位 = 物理像素
  frame:ClearAllPoints()
  frame:SetPoint("TOPLEFT", nil, "TOPLEFT", CFG.x, -CFG.y)
  for i = 0, NUM_BLOCKS - 1 do
    local t = blocks[i]
    t:ClearAllPoints()
    t:SetSize(B, B)
    t:SetPoint("TOPLEFT", frame, "TOPLEFT", i * B, 0)
  end
  for i = 0, BIN_A - 1 do
    binA[i]:ClearAllPoints(); binA[i]:SetSize(B, B)
    binA[i]:SetPoint("TOPLEFT", frame, "TOPLEFT", i * B, -CFG.binTop)
  end
  for i = 0, BIN_B - 1 do
    binB[i]:ClearAllPoints(); binB[i]:SetSize(B, B)
    binB[i]:SetPoint("TOPLEFT", frame, "TOPLEFT", i * B, -(CFG.binTop + B))
  end
  for i = 0, BIN_C - 1 do
    binC[i]:ClearAllPoints(); binC[i]:SetSize(B, B)
    binC[i]:SetPoint("TOPLEFT", frame, "TOPLEFT", i * B, -(CFG.binTop + 2 * B))
  end
  for i = 1, NUM_BARS do
    local b = bars[i]
    b:ClearAllPoints()
    b:SetSize(CFG.barWidth, CFG.barHeight)
    b:SetPoint("TOPLEFT", frame, "TOPLEFT", 0, -(B + (i - 1) * CFG.barHeight))
  end
end

local function NewTex()
  local t = frame:CreateTexture(nil, "OVERLAY")
  t:SetColorTexture(1, 1, 1, 1)   -- 白底 × 顶点色 = 精确颜色
  t:SetVertexColor(0, 0, 0, 1)
  return t
end
for i = 0, NUM_BLOCKS - 1 do blocks[i] = NewTex() end
for i = 0, BIN_A - 1 do binA[i] = NewTex() end
for i = 0, BIN_B - 1 do binB[i] = NewTex() end
for i = 0, BIN_C - 1 do binC[i] = NewTex() end
for i = 1, NUM_BARS do
  local b = CreateFrame("StatusBar", nil, frame)
  b:SetStatusBarTexture("Interface\\Buttons\\WHITE8X8")
  b:SetStatusBarColor(0, 1, 0, 1)
  b:SetMinMaxValues(0, 1)
  b:SetValue(0)
  local bg = b:CreateTexture(nil, "BACKGROUND")
  bg:SetAllPoints()
  bg:SetColorTexture(0, 0, 0, 1)
  bars[i] = b
end
Layout()

local function Paint(i, c)
  blocks[i]:SetVertexColor(c[1], c[2], c[3], 1)
end

-------------------------------------------------------------------- 百分比（机密值安全）
-- 线性曲线：0% → 黑(0,0,0)，100% → 红(255,0,0)。R 通道 = 百分比 × 255
local RED_CURVE
pcall(function()
  local curve = C_CurveUtil.CreateColorCurve()
  curve:SetType(Enum.LuaCurveType.Linear)
  curve:AddPoint(0.0, CreateColor(0, 0, 0, 1))
  curve:AddPoint(1.0, CreateColor(1, 0, 0, 1))
  RED_CURVE = curve
end)
local ZERO_TO_ONE = _G.CurveConstants and _G.CurveConstants.ZeroToOne

local RAGE = (Enum and Enum.PowerType and Enum.PowerType.Rage) or 1
local MANA = (Enum and Enum.PowerType and Enum.PowerType.Mana) or 0

-- getPct(curve) 返回曲线结果；getRaw() 返回 cur, max。返回 true = 进度条画成功了
-- v0.5：进度条优先用 SetMinMaxValues(0, max) + SetValue(cur)：StatusBar 原生接受机密值，战斗中也能画。
-- （v0.4 先用 ZeroToOne 曲线，部分客户端没有这个常量 → 进度条可能一直满格，战斗中读不到掉血）
local function PaintPercent(i, bar, getPct, getRaw)
  local tex = blocks[i]
  local okBar = (bar == nil) or pcall(function()
    local cur, max = getRaw()
    bar:SetMinMaxValues(0, max)
    bar:SetValue(cur)
  end)
  if not okBar and ZERO_TO_ONE then
    okBar = pcall(function()
      local v = getPct(ZERO_TO_ONE)
      bar:SetMinMaxValues(0, 1)
      bar:SetValue(v)
    end)
  end
  -- 状态块只供目视（Python 不读）
  local okBlock = RED_CURVE and pcall(function()
    local c = getPct(RED_CURVE)
    tex:SetVertexColor(c:GetRGB())
  end)
  if not okBlock then
    local ok = pcall(function()
      local cur, max = getRaw()
      local c, m = plain(cur), plain(max)
      if c and m and m > 0 then tex:SetVertexColor(c / m, 0, 0, 1) else tex:SetVertexColor(1, 0, 1, 1) end
    end)
    if not ok then tex:SetVertexColor(1, 0, 1, 1) end
  end
  if bar and not okBar then
    pcall(function() bar:SetMinMaxValues(0, 1); bar:SetValue(0) end)
  end
  return okBar and true or false
end

-------------------------------------------------------------------- 普通攻击格子（v0.5）
-- 在动作条 1-120 格里找“攻击”(spellID 6603)，用 IsActionInRange(格子) 判断是否在近战范围。
local ATTACK_ID = 6603
local attackSlot = nil
local function FindAttackSlot()
  local found
  pcall(function()
    if C_ActionBar and C_ActionBar.FindSpellActionButtons then
      local list = C_ActionBar.FindSpellActionButtons(ATTACK_ID)
      if type(list) == "table" and list[1] then found = plain(list[1]) end
    end
  end)
  if not found and _G.GetActionInfo then
    for slot = 1, 120 do
      local ok, kind, id = pcall(GetActionInfo, slot)
      if ok then
        kind, id = plain(kind), plain(id)
        if kind == "spell" and id == ATTACK_ID then found = slot; break end
      end
    end
  end
  attackSlot = found
end

-- 返回 true（在范围内）/ false（太远）/ nil（判断不了）
local function AttackInRange()
  if not attackSlot then return nil end
  local r
  local ok = pcall(function()
    local f = (C_ActionBar and C_ActionBar.IsActionInRange) or _G.IsActionInRange
    r = plain(f(attackSlot, "target"))
  end)
  if not ok then return nil end
  if r == 1 then r = true elseif r == 0 then r = false end
  if r == true or r == false then return r end
  return nil
end

-------------------------------------------------------------------- 距上次打出伤害（v0.5）
-- COMBAT_LOG_EVENT_UNFILTERED 在 Midnight 规则下可能不给插件用 → 全部 pcall；
-- cleuOK = 收到过能读的战斗日志 → 二进制有效位 1；否则 0（Python 改看目标血条有没有掉）。
local cleuOK, lastDamage = false, nil
local DAMAGE_EVENTS = { SWING_DAMAGE = true, RANGE_DAMAGE = true, SPELL_DAMAGE = true, SPELL_PERIODIC_DAMAGE = true }
local function OnCombatLog()
  pcall(function()
    local _, sub, _, srcGUID, _, _, _, dstGUID = CombatLogGetCurrentEventInfo()
    if sub == nil or issecret(sub) or issecret(srcGUID) or issecret(dstGUID) then return end
    cleuOK = true
    if DAMAGE_EVENTS[sub] and srcGUID == UnitGUID("player") then
      local tg = UnitGUID("target")
      if tg and not issecret(tg) and dstGUID == tg then lastDamage = GetTime() end
    end
  end)
end

-------------------------------------------------------------------- 距离
-- 战斗中可能查不了技能名，未解析前直接用配置里的 ID/名字
local resolved = { melee = CFG.meleeSpells, ranged = CFG.rangedSpells }
local function Resolve()
  if InCombatLockdown() then return end
  for key, list in pairs({ melee = CFG.meleeSpells, ranged = CFG.rangedSpells }) do
    local out, seen = {}, {}
    for _, s in ipairs(list) do
      local id
      if C_Spell and C_Spell.GetSpellInfo then
        local ok, info = pcall(C_Spell.GetSpellInfo, s)
        if ok and info and info.spellID then id = info.spellID end
      end
      id = id or s
      if not seen[id] then seen[id] = true; out[#out + 1] = id end
    end
    resolved[key] = out
  end
end

local function InRange(list)
  for _, s in ipairs(list) do
    local ok, r
    if C_Spell and C_Spell.IsSpellInRange then
      ok, r = pcall(C_Spell.IsSpellInRange, s, "target")
    elseif _G.IsSpellInRange then
      ok, r = pcall(_G.IsSpellInRange, s, "target")
      if ok and r ~= nil and not issecret(r) then r = (r == 1 or r == true) end
    end
    if ok and r ~= nil and not issecret(r) then return r end
  end
  return nil
end

-------------------------------------------------------------------- 红字报错
local ERR_MAP_SRC = {
  red     = { "ERR_BADATTACKFACING", "SPELL_FAILED_UNIT_NOT_INFRONT", "ERR_LOOT_BAD_FACING", "SPELL_FAILED_NOT_INFRONT" },
  yellow  = { "ERR_BADATTACKPOS", "ERR_OUT_OF_RANGE", "SPELL_FAILED_OUT_OF_RANGE", "ERR_SPELL_OUT_OF_RANGE", "ERR_TOO_FAR_TO_INTERACT",
              "ERR_LOOT_TOO_FAR", "ERR_USE_TOO_FAR", "SPELL_FAILED_TOO_CLOSE" },
  blue    = { "ERR_GENERIC_NO_TARGET", "ERR_NO_ATTACK_TARGET", "ERR_INVALID_ATTACK_TARGET", "SPELL_FAILED_BAD_TARGETS",
              "SPELL_FAILED_TARGETS_DEAD", "SPELL_FAILED_BAD_IMPLICIT_TARGETS" },
  magenta = { "SPELL_FAILED_LINE_OF_SIGHT" },
  cyan    = { "SPELL_FAILED_MOVING", "ERR_NOT_WHILE_MOVING" },
  green   = { "SPELL_FAILED_NOT_SHAPESHIFT", "ERR_CANT_DO_THAT_WHILE_SHAPESHIFTED", "SPELL_FAILED_ONLY_SHAPESHIFT" },
}
local ERR_MAP = {}
for color, names in pairs(ERR_MAP_SRC) do
  for _, n in ipairs(names) do
    local s = _G[n]
    if type(s) == "string" and s ~= "" then ERR_MAP[s] = C[color] end
  end
end
local errColor, errUntil = C.black, 0
-- v0.5：面向错误 / 太远 / 视线 各自单独一个块（17/18/19），亮 errorHold 秒
local function Names(list)
  local out = {}
  for _, n in ipairs(list) do
    local v = _G[n]
    if type(v) == "string" and v ~= "" then out[v] = true end
  end
  return out
end
local FACING_MSG = Names({ "ERR_BADATTACKFACING", "SPELL_FAILED_UNIT_NOT_INFRONT", "SPELL_FAILED_NOT_INFRONT" })
local FAR_MSG = Names({ "ERR_BADATTACKPOS", "SPELL_FAILED_OUT_OF_RANGE", "ERR_OUT_OF_RANGE", "ERR_SPELL_OUT_OF_RANGE" })
local LOS_MSG = Names({ "SPELL_FAILED_LINE_OF_SIGHT" })
local facingUntil, farUntil, losUntil = 0, 0, 0
local facingCount = 0   -- v0.7：每次“面向错误”+1（0-7 循环），Python 看变化次数，不用猜 1.5 秒

local function OnUIError(message)
  if message == nil or issecret(message) then return end
  errColor = ERR_MAP[message] or C.white   -- 白色 = 其他报错
  local t = GetTime() + CFG.errorHold
  errUntil = t
  if FACING_MSG[message] then facingUntil = t; facingCount = (facingCount + 1) % 8 end
  if FAR_MSG[message] then farUntil = t end
  if LOS_MSG[message] then losUntil = t end
end

-------------------------------------------------------------------- 二进制数值（v0.4，抗伽马）
-- 一个数值 = [有效位 1][数据 nbits，高位在前][校验 4 位]，按 3 位一块画成纯色（每通道 0 或 1）。
-- 读不到（nil：副本内/机密值/没有地图）→ 有效位 = 0。
local function Bits(value, nbits)
  local bits = {}
  local v = 0
  if value ~= nil then
    v = math.floor(value + 0.5)
    local maxv = 2 ^ nbits - 1
    if v < 0 then v = 0 elseif v > maxv then v = maxv end
  end
  bits[1] = (value ~= nil) and 1 or 0
  for k = nbits - 1, 0, -1 do bits[#bits + 1] = math.floor(v / 2 ^ k) % 2 end
  local cs, t = 5, v
  while t > 0 do cs = cs + t % 16; t = math.floor(t / 16) end
  cs = cs % 16
  for k = 3, 0, -1 do bits[#bits + 1] = math.floor(cs / 2 ^ k) % 2 end
  while #bits % 3 ~= 0 do bits[#bits + 1] = 0 end
  return bits
end

local function PaintBits(row, start, value, nbits)
  local bits = Bits(value, nbits)
  for j = 0, #bits / 3 - 1 do
    row[start + j]:SetVertexColor(bits[3 * j + 1], bits[3 * j + 2], bits[3 * j + 3], 1)
  end
end

-- 只在不是机密值时算百分比（脱战时），战斗中返回 nil → Python 改读进度条
local function PlainPct(getRaw)
  local pct
  pcall(function()
    local cur, max = getRaw()
    cur, max = plain(cur), plain(max)
    if cur and max and max > 0 then pct = cur / max * 100 end
  end)
  return pct
end

-- 坐标/朝向/地图：不是战斗机密值；在副本/战场/竞技场里返回 nil
local TWO_PI = 2 * math.pi
-- v0.12 跑尸：尸体坐标（和自己坐标用同一张地图）
local corpseX, corpseY, corpseKnown = nil, nil, false
local npDist, npOK, npShownOnce = nil, false, false   -- v0.12 姓名板显示距离（实际值）
local relShown, resShown = false, false             -- v0.12 “释放灵魂”/“复活”框开着？
local function MapXY(getter, mapID)
  local v
  pcall(function()
    local pos = getter(mapID)
    if pos then
      local px, py = pos:GetXY()
      px, py = plain(px), plain(py)
      if px and py and (px ~= 0 or py ~= 0) then v = { px, py } end
    end
  end)
  return v
end
local function PlayerXY(m) return C_Map.GetPlayerMapPosition(m, "player") end
local function CorpseXY(m) return C_DeathInfo.GetCorpseMapPosition(m) end

local function PaintBinary()
  local mapID, x, y, facing
  pcall(function()
    mapID = plain(C_Map.GetBestMapForUnit("player"))
    if mapID then
      local pos = C_Map.GetPlayerMapPosition(mapID, "player")
      if pos then
        local px, py = pos:GetXY()
        x, y = plain(px), plain(py)
      end
    end
  end)
  -- v0.12：死了/灵魂 → 找一张 自己和尸体 都有坐标的地图（当前地图 → 父地图 → ...）
  corpseX, corpseY, corpseKnown = nil, nil, false
  local deadOrGhost = plain(UnitIsDeadOrGhost("player")) == true
  if deadOrGhost and mapID and _G.C_DeathInfo and C_DeathInfo.GetCorpseMapPosition then
    local m, hops = mapID, 0
    while m and m > 0 and hops < 6 do
      local c, p = MapXY(CorpseXY, m), MapXY(PlayerXY, m)
      if c and p then
        mapID, x, y = m, p[1], p[2]
        corpseX, corpseY, corpseKnown = c[1], c[2], true
        break
      end
      local parent
      pcall(function() parent = plain(C_Map.GetMapInfo(m).parentMapID) end)
      m, hops = parent, hops + 1
    end
  end
  pcall(function() facing = plain(GetPlayerFacing()) end)
  if not (x and y and (x ~= 0 or y ~= 0)) then x, y = nil, nil end
  PaintBits(binA, 0,  x and x * 65535, 16)
  PaintBits(binA, 7,  y and y * 65535, 16)
  PaintBits(binA, 14, facing and (facing % TWO_PI) / TWO_PI * 65535, 16)
  -- 距上次对当前目标打出伤害的秒数：0-126，127 = 这个目标还没打出过伤害；战斗日志不可用 → 无效
  local since
  if cleuOK then
    since = lastDamage and math.min(126, GetTime() - lastDamage) or 127
  end
  PaintBits(binA, 21, since, 7)
  PaintBits(binB, 0,  mapID and (mapID % 65536), 16)
  PaintBits(binB, 7,  PlainPct(function() return UnitHealth("player"), UnitHealthMax("player") end), 7)
  local tpct
  if plain(UnitExists("target")) then
    tpct = PlainPct(function() return UnitHealth("target"), UnitHealthMax("target") end)
  end
  PaintBits(binB, 11, tpct, 7)
  PaintBits(binB, 15, PlainPct(function() return UnitPower("player", RAGE), UnitPowerMax("player", RAGE) end), 7)
  PaintBits(binB, 19, PlainPct(function() return UnitPower("player", MANA), UnitPowerMax("player", MANA) end), 7)
  -- v0.12 C 行：尸体坐标 / 复活还要等几秒 / 姓名板显示距离
  PaintBits(binC, 0, corpseX and corpseX * 65535, 16)
  PaintBits(binC, 7, corpseY and corpseY * 65535, 16)
  local delay
  pcall(function() if deadOrGhost then delay = math.min(126, math.max(0, plain(GetCorpseRecoveryDelay()) or 0)) end end)
  PaintBits(binC, 14, delay, 7)
  PaintBits(binC, 18, npDist and math.min(126, npDist), 7)
  binC[24]:SetVertexColor(0, 1, 1, 1)        -- v0.12 签名：C 行最后一块青
  binC[23]:SetVertexColor(1, 0, 1, 1)        -- v0.13 签名：C 行倒数第二块洋红（块 36-39 目标信息有效）
  Paint(34, { relShown and 1 or 0, resShown and 1 or 0, corpseKnown and 1 or 0 })   -- v0.12 死亡信息
  binB[23]:SetVertexColor(1, 1, 1, 1)        -- 版本 7 = 二进制 111 = 白（v0.6 黄，v0.5 洋红，v0.4 红）
  binB[24]:SetVertexColor(0.5, 0.5, 0.5, 1)  -- 校准灰：默认伽马下应读到约 (128,128,128)
end

-------------------------------------------------------------------- 目标姓名板标记（v0.7）
-- 读姓名板坐标（GetCenter 等）会被暴雪拦；但把自己的小框锚到姓名板上是允许的（同 TargetNameplateIndicator）。
-- 标记：黑底 20x12，左 8x8 青、右 8x8 洋红（物理像素）。Plater 也保留姓名板底框，照样能锚。
local marker = CreateFrame("Frame", "BotStatusMarker", nil)
marker:SetFrameStrata("TOOLTIP")
marker:SetFrameLevel(9998)
marker:SetSize(20, 12)
do
  local bg = marker:CreateTexture(nil, "BACKGROUND"); bg:SetAllPoints(); bg:SetColorTexture(0, 0, 0, 1)
  local l = marker:CreateTexture(nil, "OVERLAY"); l:SetSize(8, 8); l:SetPoint("TOPLEFT", 2, -2); l:SetColorTexture(0, 1, 1, 1)
  local r = marker:CreateTexture(nil, "OVERLAY"); r:SetSize(8, 8); r:SetPoint("TOPLEFT", 10, -2); r:SetColorTexture(1, 0, 1, 1)
end
marker:Hide()
local markerPlate = nil
-- 返回状态色：绿=标记已挂在目标姓名板上；红=有目标但没有姓名板；洋红=出错（被拦）；黑=没目标
-------------------------------------------------------------------- v0.8 buff
local buffSets = {}
local function BuffSet(bd)
  local s = buffSets[bd]
  if s then return s end
  s = { ids = {}, names = {} }
  for _, id in ipairs(bd.ids) do
    s.ids[id] = true
    local nm
    if C_Spell and C_Spell.GetSpellInfo then
      local ok, info = pcall(C_Spell.GetSpellInfo, id)
      if ok and type(info) == "table" then nm = plain(info.name) end
    elseif _G.GetSpellInfo then
      local ok, n = pcall(_G.GetSpellInfo, id)
      if ok then nm = plain(n) end
    end
    if type(nm) == "string" then s.names[nm] = true end
  end
  for _, n in ipairs(bd.names) do s.names[n] = true end
  buffSets[bd] = s
  return s
end

-- 返回 剩余秒数（0 = 永久/不知道多久）；没有 = false；读不到 = nil
local function AuraRemain(aura)
  local exp = plain(aura.expirationTime)
  if type(exp) ~= "number" or exp <= 0 then return 0 end
  return math.max(0.01, exp - GetTime())
end

local function FindBuff(bd)
  local s = BuffSet(bd)
  local readable = false
  -- 1) 按法术 ID 直接查
  if C_UnitAuras and C_UnitAuras.GetPlayerAuraBySpellID then
    for _, id in ipairs(bd.ids) do
      local ok, a = pcall(C_UnitAuras.GetPlayerAuraBySpellID, id)
      if ok then
        readable = true
        if a ~= nil and not issecret(a) and type(a) == "table" then return AuraRemain(a) end
      end
    end
  end
  -- 2) 逐个 buff 比 spellId / 名字
  local getter = C_UnitAuras and (C_UnitAuras.GetBuffDataByIndex or C_UnitAuras.GetAuraDataByIndex)
  if getter then
    for i = 1, 40 do
      local ok, a
      if C_UnitAuras.GetBuffDataByIndex then
        ok, a = pcall(C_UnitAuras.GetBuffDataByIndex, "player", i)
      else
        ok, a = pcall(C_UnitAuras.GetAuraDataByIndex, "player", i, "HELPFUL")
      end
      if not ok then break end
      if a == nil then readable = true; break end
      if type(a) == "table" then
        local id, nm = plain(a.spellId), plain(a.name)
        if id ~= nil or nm ~= nil then readable = true end
        if (id and s.ids[id]) or (nm and s.names[nm]) then return AuraRemain(a) end
      end
    end
  end
  -- 3) 旧 API
  if _G.UnitBuff then
    for i = 1, 40 do
      local ok, nm, _, _, _, _, exp, _, _, _, id = pcall(_G.UnitBuff, "player", i)
      if not ok or issecret(nm) or issecret(id) then break end
      exp = plain(exp)
      if nm == nil and id == nil then readable = readable or ok; break end
      readable = true
      if (id and s.ids[id]) or (nm and s.names[nm]) then
        if type(exp) ~= "number" or exp <= 0 then return 0 end
        return math.max(0.01, exp - GetTime())
      end
    end
  end
  if readable then return false end
  return nil
end

local function KnowsBuff(bd)
  local f = _G.IsPlayerSpell or _G.IsSpellKnown
  if not f then return nil end
  local any = false
  for _, id in ipairs(bd.ids) do
    local ok, r = pcall(f, id)
    if ok and r ~= nil and not issecret(r) then
      any = true
      if r then return true end
    end
  end
  if any then return false end
  return nil
end

local function BuffColor(bd)
  local r = FindBuff(bd)
  if r == nil then return C.magenta end
  if r == false then
    if KnowsBuff(bd) == false then return C.blue end
    return C.black
  end
  if r > 0 and r < CFG.buffLowSec then return C.yellow end
  return C.green
end

local function UpdateMarker(hasTarget)
  if not hasTarget then
    marker:Hide(); markerPlate = nil
    return C.black
  end
  local plate
  local ok = pcall(function()
    plate = C_NamePlate.GetNamePlateForUnit("target")
  end)
  if not ok then marker:Hide(); markerPlate = nil; return C.magenta end
  if not plate then marker:Hide(); markerPlate = nil; return C.red end
  if plate ~= markerPlate then
    ok = pcall(function()
      marker:SetScale(PixelScale())
      marker:ClearAllPoints()
      marker:SetPoint("CENTER", plate, "CENTER", 0, 0)   -- v0.10：正中间（鼠标点击穿透标记，点到的是姓名板）
    end)
    if not ok then marker:Hide(); markerPlate = nil; return C.magenta end
    markerPlate = plate
  end
  marker:Show()
  return C.green
end

-------------------------------------------------------------------- v0.11 能打的怪的姓名板标记（黄|蓝）
local enemyMarks = {}
local marksAlpha = 1
local function NewEnemyMark()
  local m = CreateFrame("Frame", nil, nil)
  m:SetFrameStrata("TOOLTIP")
  m:SetFrameLevel(9990)          -- 比目标标记（9998）低
  m:SetSize(20, 12)
  local bg = m:CreateTexture(nil, "BACKGROUND"); bg:SetAllPoints(); bg:SetColorTexture(0, 0, 0, 1)
  local l = m:CreateTexture(nil, "OVERLAY"); l:SetSize(8, 8); l:SetPoint("TOPLEFT", 2, -2); l:SetColorTexture(1, 1, 0, 1)
  local r = m:CreateTexture(nil, "OVERLAY"); r:SetSize(8, 8); r:SetPoint("TOPLEFT", 10, -2); r:SetColorTexture(0, 0, 1, 1)
  m:SetAlpha(marksAlpha)
  m:Hide()
  return m
end

-- 返回 true = 能打；false = 不能打；nil = 读不到（机密值）
local ghostMode = false   -- v0.12：自己是灵魂 → 标记“会打我的怪”（敌对/不友好，活的，不是玩家），被抢的也算
local function Attackable(unit)
  if ghostMode then
    local isPlayer = plain(UnitIsPlayer(unit))
    local dead = plain(UnitIsDeadOrGhost(unit))
    if isPlayer == true or dead == true then return false end
    local hostile = plain(UnitCanAttack(unit, "player"))
    local reaction = _G.UnitReaction and plain(UnitReaction(unit, "player"))
    if hostile == true or (type(reaction) == "number" and reaction <= 3) then return true end
    if hostile == nil and reaction == nil then return nil end
    return false
  end
  local isTarget = plain(UnitIsUnit(unit, "target"))
  if isTarget == true then return false end          -- 目标自己画 青|洋红
  local isPlayer = plain(UnitIsPlayer(unit))
  local canAtk = plain(UnitCanAttack("player", unit))
  local dead = plain(UnitIsDeadOrGhost(unit))
  if isPlayer == true or canAtk == false or dead == true then return false end
  if isPlayer == nil or canAtk == nil or dead == nil then return nil end
  if _G.UnitIsTapDenied and plain(UnitIsTapDenied(unit)) == true then return false end
  return true
end

-- 返回 姓名板个数, 接口能用, 有读不到的
local function UpdateEnemyMarks()
  local used, plates, apiOK, unknown = 0, 0, true, false
  local scale = PixelScale()
  if not (C_NamePlate and C_NamePlate.GetNamePlateForUnit) then apiOK = false end
  for i = 1, 40 do
    if not apiOK then break end
    local unit = "nameplate" .. i
    local ok, res, plate = pcall(function()
      if not plain(UnitExists(unit)) then return "none" end
      local p = C_NamePlate.GetNamePlateForUnit(unit)
      if not p then return "none" end
      local a = Attackable(unit)
      if a == nil then return "unknown", p end
      return a and "yes" or "no", p
    end)
    if not ok then apiOK = false
    elseif res ~= "none" then
      plates = plates + 1
      if res == "unknown" then unknown = true
      elseif res == "yes" then
        local ok2 = pcall(function()
          used = used + 1
          local m = enemyMarks[used]
          if not m then m = NewEnemyMark(); enemyMarks[used] = m end
          m:SetScale(scale)
          m:ClearAllPoints()
          m:SetPoint("CENTER", plate, "CENTER", 0, 0)
          m:Show()
        end)
        if not ok2 then apiOK = false end
      end
    end
  end
  for j = used + 1, #enemyMarks do enemyMarks[j]:Hide() end
  if not apiOK then for j = 1, #enemyMarks do enemyMarks[j]:Hide() end end
  return plates, apiOK, unknown
end

-------------------------------------------------------------------- 主刷新
local tick = 0
local autoAttacking = nil   -- PLAYER_ENTER_COMBAT / PLAYER_LEAVE_COMBAT（自动攻击开/关）
local scanAge = 0
local function Update()
  -- 每 2 秒重新找一次“攻击”格子（动作条会被拖动）
  scanAge = scanAge + 1
  if scanAge >= 40 or attackSlot == nil and scanAge >= 10 then scanAge = 0; FindAttackSlot() end
  tick = (tick + 1) % 256

  -- 0 心跳：(255,255,tick)
  blocks[0]:SetVertexColor(1, 1, tick / 255, 1)

  -- 1 自己血量%（R）+ 进度条 1
  local hpBarOK = PaintPercent(1, bars[1],
    function(curve) return UnitHealthPercent("player", true, curve) end,
    function() return UnitHealth("player"), UnitHealthMax("player") end)

  -- 2 自己生死
  local ghost = plain(UnitIsGhost("player"))
  local dead = plain(UnitIsDeadOrGhost("player"))
  if ghost then Paint(2, C.yellow)
  elseif dead then Paint(2, C.red)
  elseif dead == false then Paint(2, C.green)
  else Paint(2, C.magenta) end

  -- 3 战斗
  local combat = plain(UnitAffectingCombat("player"))
  if combat == nil then combat = InCombatLockdown() end
  Paint(3, combat and C.green or C.black)

  -- 4 目标类型 / 5 目标血量 / 6 可拾取 / 7 距离 / 13 目标在打我
  local hasTarget = plain(UnitExists("target"))
  local tBarOK = true
  if not hasTarget then
    Paint(16, C.black)
    Paint(4, C.black); Paint(5, C.black); Paint(6, C.black); Paint(7, C.black); Paint(13, C.black)
    bars[2]:SetMinMaxValues(0, 1); bars[2]:SetValue(0)
  else
    local tDead = plain(UnitIsDeadOrGhost("target"))
    local canAtk = plain(UnitCanAttack("player", "target"))
    local tapped = _G.UnitIsTapDenied and plain(UnitIsTapDenied("target"))
    if tDead == nil or canAtk == nil then Paint(4, C.magenta)
    elseif tDead then Paint(4, C.white)
    elseif not canAtk then Paint(4, C.blue)
    elseif tapped == true then Paint(4, C.yellow)
    else Paint(4, C.red) end

    tBarOK = PaintPercent(5, bars[2],
      function(curve) return UnitHealthPercent("target", true, curve) end,
      function() return UnitHealth("target"), UnitHealthMax("target") end)

    local loot = false
    if tDead then
      pcall(function()
        local guid = UnitGUID("target")
        if guid and not issecret(guid) then
          local hasLoot = CanLootUnit(guid)
          loot = plain(hasLoot) == true
        end
      end)
    end
    Paint(6, loot and C.green or C.black)

    -- 16 普通攻击距离：绿=在范围；红=太远；洋红=判断不了；蓝=动作条上没找到“攻击”
    if not attackSlot then Paint(16, C.blue)
    elseif tDead then Paint(16, C.black)
    else
      local r = AttackInRange()
      if r == true then Paint(16, C.green) elseif r == false then Paint(16, C.red) else Paint(16, C.magenta) end
    end

    if tDead then
      Paint(7, C.black)
    else
      local melee = InRange(resolved.melee)
      if melee == true then Paint(7, C.green)
      else
        local ranged = InRange(resolved.ranged)
        if ranged == true then Paint(7, C.yellow)
        elseif ranged == false or melee == false then Paint(7, C.red)
        else Paint(7, C.magenta) end
      end
    end

    local aggro = false
    pcall(function()
      if plain(UnitExists("targettarget")) then
        aggro = plain(UnitIsUnit("targettarget", "player")) == true
      end
    end)
    if not aggro and _G.UnitThreatSituation then   -- 备用：仇恨状态 2/3 = 怪在打我
      pcall(function()
        local t = plain(UnitThreatSituation("player", "target"))
        aggro = (t == 2 or t == 3)
      end)
    end
    Paint(13, aggro and C.green or C.black)
  end

  -- v0.13：36 目标 玩家/精英/稀有，37 有效位 + 签名，38/39 等级差（6 位）
  local isPl, elite, rare, plOK, clOK, lvCode = 0, 0, 0, 0, 0, 0
  if hasTarget then
    pcall(function()
      local v = plain(UnitIsPlayer("target"))
      if v ~= nil then plOK = 1; isPl = v and 1 or 0 end
    end)
    pcall(function()
      local c = plain(UnitClassification("target"))
      if type(c) == "string" then
        clOK = 1
        if c == "elite" or c == "rareelite" or c == "worldboss" then elite = 1 end
        if c == "rare" then rare = 1 end
      end
    end)
    pcall(function()
      local tl, pl = plain(UnitLevel("target")), plain(UnitLevel("player"))
      if type(tl) == "number" and type(pl) == "number" then
        if tl < 0 then lvCode = 63
        else lvCode = math.max(1, math.min(62, tl - pl + 32)) end
      end
    end)
  end
  Paint(36, { isPl, elite, rare })
  Paint(37, { plOK, clOK, 1 })
  Paint(38, { math.floor(lvCode / 32) % 2, math.floor(lvCode / 16) % 2, math.floor(lvCode / 8) % 2 })
  Paint(39, { math.floor(lvCode / 4) % 2, math.floor(lvCode / 2) % 2, lvCode % 2 })

  -- 8 最近 1 秒的红字报错
  local now = GetTime()
  if now < errUntil then Paint(8, errColor) else Paint(8, C.black) end
  -- 17 面向错误 / 18 太远 / 19 视线（v0.5，各亮 1.5 秒）
  Paint(17, now < facingUntil and C.red or C.black)
  Paint(18, now < farUntil and C.yellow or C.black)
  Paint(19, now < losUntil and C.magenta or C.black)
  -- 22 目标姓名板标记 / 23 面向错误计数（3 位：R=4 G=2 B=1）
  local mk = C.magenta
  pcall(function() mk = UpdateMarker(hasTarget) end)
  Paint(22, mk)
  Paint(23, { math.floor(facingCount / 4) % 2, math.floor(facingCount / 2) % 2, facingCount % 2 })
  -- 24 游戏菜单（Esc 菜单）开着？白=开着
  local menu = false
  pcall(function() menu = _G.GameMenuFrame and GameMenuFrame:IsShown() and true or false end)
  Paint(24, menu and C.white or C.black)
  -- 25/26 荆棘术 / 野性印记；27 青 + 28 黄 = v0.8 签名
  for _, bd in ipairs(CFG.buffs) do
    local col = C.magenta
    pcall(function() col = BuffColor(bd) end)
    Paint(bd.block, col)
  end
  Paint(27, C.cyan)
  Paint(28, C.green)    -- v0.11 签名（v0.10 = 白，v0.8/0.9 = 黄）：目标标记在姓名板中间 + 能打的怪都有 黄|蓝 标记
  -- v0.11：32 标记状态 / 33 姓名板个数
  ghostMode = plain(UnitIsGhost("player")) == true
  local np, apiOK, unknown = 0, false, false
  pcall(function() np, apiOK, unknown = UpdateEnemyMarks() end)
  np = math.min(7, np or 0)
  Paint(32, { 1, apiOK and 1 or 0, unknown and 1 or 0 })
  Paint(33, { math.floor(np / 4) % 2, math.floor(np / 2) % 2, np % 2 })
  -- v0.12：34 死亡信息 / 35 签名
  relShown, resShown = false, false
  pcall(function()
    if _G.StaticPopup_Visible then
      relShown = StaticPopup_Visible("DEATH") and true or false
      resShown = (StaticPopup_Visible("RECOVER_CORPSE") or StaticPopup_Visible("RECOVER_CORPSE_INSTANCE")) and true or false
    end
  end)
  Paint(35, { npOK and 1 or 0, ghostMode and 1 or 0, 1 })
  -- v0.9：29 连击点（3 位 0-7）/ 30 能量档位（3 位：能量%×7 四舍五入）/ 31 有效位 R=连击点可读 G=能量可读 B=1（v0.9 签名）
  local cp, en = nil, nil
  pcall(function()
    local v = nil
    if _G.GetComboPoints then v = plain(GetComboPoints("player", "target")) end
    if v == nil and Enum and Enum.PowerType and Enum.PowerType.ComboPoints then
      v = plain(UnitPower("player", Enum.PowerType.ComboPoints))
    end
    if type(v) == "number" then cp = math.max(0, math.min(7, v)) end
  end)
  pcall(function()
    local ET = (Enum and Enum.PowerType and Enum.PowerType.Energy) or 3
    local c, m = plain(UnitPower("player", ET)), plain(UnitPowerMax("player", ET))
    if type(c) == "number" and type(m) == "number" and m > 0 then
      en = math.max(0, math.min(7, math.floor(c / m * 7 + 0.5)))
    end
  end)
  local cpv, env = cp or 0, en or 0
  Paint(29, { math.floor(cpv / 4) % 2, math.floor(cpv / 2) % 2, cpv % 2 })
  Paint(30, { math.floor(env / 4) % 2, math.floor(env / 2) % 2, env % 2 })
  Paint(31, { cp and 1 or 0, en and 1 or 0, 1 })
  -- 20 动作条上有没有“攻击”：绿=找到；蓝=没找到（请把 普通攻击 拖到任意格子）
  Paint(20, attackSlot and C.green or C.blue)
  -- 21 进度条状态：绿=都正常；红=自己血条画不出来；黄=目标血条画不出来
  if not hpBarOK then Paint(21, C.red) elseif not tBarOK then Paint(21, C.yellow) else Paint(21, C.green) end

  -- 9 形态
  local form = plain(GetShapeshiftFormID and GetShapeshiftFormID())
  if form == 5 or form == 8 then Paint(9, C.red)           -- 熊 / 巨熊
  elseif form == 1 then Paint(9, C.yellow)                 -- 猎豹
  elseif form == 3 or form == 4 or form == 27 or form == 29 then Paint(9, C.blue) -- 旅行/水栖/飞行
  elseif form == nil or form == 0 then Paint(9, C.black)   -- 人形
  else Paint(9, C.white) end                               -- 其他（枭兽等）

  -- 10 施法
  local casting = UnitCastingInfo("player")
  local channel = UnitChannelInfo("player")
  if issecret(casting) or casting ~= nil then Paint(10, C.green)
  elseif issecret(channel) or channel ~= nil then Paint(10, C.blue)
  else Paint(10, C.black) end

  -- 11 怒气%（R）+ 进度条 3
  PaintPercent(11, bars[3],
    function(curve) return UnitPowerPercent("player", RAGE, false, curve) end,
    function() return UnitPower("player", RAGE), UnitPowerMax("player", RAGE) end)

  -- 12 移动中
  local speed = plain(GetUnitSpeed("player"))
  Paint(12, (speed and speed > 0) and C.green or C.black)

  -- 14 自动攻击已开启（右键点怪后应变绿）
  local auto
  pcall(function()
    local f = (C_Spell and C_Spell.IsCurrentSpell) or _G.IsCurrentSpell
    auto = plain(f(6603))
  end)
  if auto == nil then auto = autoAttacking end
  if auto == true then Paint(14, C.green)
  elseif auto == false then Paint(14, C.black)
  else Paint(14, C.magenta) end

  -- 二进制行：坐标/朝向/地图/各百分比/版本/校准（v0.4）
  PaintBinary()

  -- 15 法力%（R）+ 进度条 4。熊形态下也有法力，战后回血时用来判断蓝够不够
  PaintPercent(15, bars[4],
    function(curve) return UnitPowerPercent("player", MANA, false, curve) end,
    function() return UnitPower("player", MANA), UnitPowerMax("player", MANA) end)
end

-------------------------------------------------------------------- v0.12 姓名板显示距离
-- 从大到小试：设完读回来，取实际生效的最大值（正式服一般最大 60 码；服务器/客户端锁死时读回来还是原值）
local npPending = false
local function ApplyNameplateDistance(verbose)
  if InCombatLockdown and InCombatLockdown() then npPending = true; return end
  npPending = false
  local before = tonumber(GetCVar and GetCVar("nameplateMaxDistance") or "")
  local best = before
  for _, v in ipairs({ 200, 100, 80, 60, 41, 40 }) do
    if best and best >= v then break end
    pcall(SetCVar, "nameplateMaxDistance", v)
    local now = tonumber(GetCVar("nameplateMaxDistance") or "")
    if now and (not best or now > best) then best = now end
    if now and now >= v then break end
  end
  if best and before and best < before then pcall(SetCVar, "nameplateMaxDistance", before); best = before end
  pcall(SetCVar, "nameplateShowEnemies", 1)
  npDist = best and math.floor(best + 0.5) or nil
  npOK = npDist ~= nil
  if verbose then
    print(string.format("|cff40ff40BotStatus v0.13|r 姓名板显示距离：%s 码（原来 %s；客户端允许的最大值，再远的怪只显示名字）",
      tostring(npDist or "读不到"), tostring(before or "?")))
  end
end

-------------------------------------------------------------------- 事件
local elapsed = 0
frame:SetScript("OnUpdate", function(_, dt)
  elapsed = elapsed + dt
  if elapsed >= CFG.interval then
    elapsed = 0
    local ok, err = pcall(Update)
    if not ok then
      blocks[0]:SetVertexColor(1, 0, 0, 1)   -- 心跳块变纯红 = 插件出错
      if not frame.errPrinted then frame.errPrinted = true; print("|cffff4040BotStatus 出错：|r" .. tostring(err)) end
    end
  end
end)

frame:RegisterEvent("PLAYER_LOGIN")
frame:RegisterEvent("PLAYER_ENTERING_WORLD")
frame:RegisterEvent("SPELLS_CHANGED")
frame:RegisterEvent("PLAYER_REGEN_ENABLED")
frame:RegisterEvent("UI_ERROR_MESSAGE")
frame:RegisterEvent("UI_SCALE_CHANGED")
frame:RegisterEvent("DISPLAY_SIZE_CHANGED")
for _, ev in ipairs({ "ACTIONBAR_SLOT_CHANGED", "PLAYER_ENTER_COMBAT", "PLAYER_LEAVE_COMBAT",
                      "PLAYER_TARGET_CHANGED", "COMBAT_LOG_EVENT_UNFILTERED",
                      "NAME_PLATE_UNIT_ADDED", "NAME_PLATE_UNIT_REMOVED" }) do
  pcall(frame.RegisterEvent, frame, ev)   -- 某些事件在无限上可能不让注册
end
frame:SetScript("OnEvent", function(_, event, a1, a2)
  if event == "COMBAT_LOG_EVENT_UNFILTERED" then
    OnCombatLog()
  elseif event == "PLAYER_TARGET_CHANGED" then
    lastDamage = nil
    markerPlate = nil
  elseif event == "NAME_PLATE_UNIT_ADDED" or event == "NAME_PLATE_UNIT_REMOVED" then
    markerPlate = nil   -- 姓名板会被回收重用，下一帧重新锚
  elseif event == "PLAYER_ENTER_COMBAT" then
    autoAttacking = true
  elseif event == "PLAYER_LEAVE_COMBAT" then
    autoAttacking = false
  elseif event == "ACTIONBAR_SLOT_CHANGED" then
    FindAttackSlot()
  elseif event == "UI_ERROR_MESSAGE" then
    OnUIError(a2)
  elseif event == "UI_SCALE_CHANGED" or event == "DISPLAY_SIZE_CHANGED" then
    Layout()
  elseif event == "PLAYER_REGEN_ENABLED" then
    Layout()
    Resolve()
    FindAttackSlot()
    if npPending then pcall(ApplyNameplateDistance, true) end
  else
    Layout()
    Resolve()
    FindAttackSlot()
    if event == "PLAYER_LOGIN" or event == "PLAYER_ENTERING_WORLD" then
      pcall(ApplyNameplateDistance, not npShownOnce)
      npShownOnce = true
    end
  end
end)

SLASH_BOTSTATUS1 = "/botstatus"
SlashCmdList.BOTSTATUS = function(msg)
  msg = (msg or ""):lower()
  if msg == "hide" then
    frame:Hide(); marker:SetAlpha(0); marksAlpha = 0
    for _, m in ipairs(enemyMarks) do m:SetAlpha(0) end
    print("BotStatus 已隐藏")
  elseif msg == "show" then
    frame:Show(); marker:SetAlpha(1); marksAlpha = 1
    for _, m in ipairs(enemyMarks) do m:SetAlpha(1) end
    print("BotStatus 已显示")
  else print("BotStatus：/botstatus show | hide。色块在屏幕左上角，编码见 README_WA.md") end
end
