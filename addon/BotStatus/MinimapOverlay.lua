--[[
BotStatus v0.19（给 WowGrinder v8.64，t243u）：小地图上也画 刷怪中心（黄点 + 编号）和刷怪半径（黄圈）。
  · 只画中心和半径圈（跑尸/修理路线还是只画在大地图上）。
  · 和大地图共用一个开关：大地图右上角“WG 图层”按钮 / /wgmap on|off（BotStatusMapDB.hidden）。
  · 数据同样来自 MapData.lua（WowGrinder 保存/录点时写入，改完要 /reload）。
  · 换算方法和 HereBeDragons-Pins（ForeverMaps/Questie 用的库）一样：
      人和点都换成世界坐标（码），差值 ÷ 小地图视野半径（C_Minimap.GetViewRadius；没有就按缩放档位查表，
      分室内/室外），乘小地图半宽；“旋转小地图”打开时按人物朝向旋转；方形/圆形/切角小地图（GetMinimapShape）都支持。
  · 圈 = 地图坐标下的圆（和 WowGrinder 算距离的方法一样），每段线只画小地图里面的部分（边上截断）。
  · 人走动/转身/缩放时每 0.05 秒重算一次；在副本里/读不到坐标就不画。
  · 只读 CVar（rotateMinimap / minimapZoom），不改任何设置。
]]
local MM = { lines = {}, tex = {}, fonts = {}, nl = 0, nt = 0, nf = 0, drawn = 0 }
BotStatusMinimapOverlay = MM

local COL = { 1, 0.85, 0, 1 }
local RING = { 1, 0.85, 0, 0.9 }
local SEGS = 64
local CLIP = 0.98            -- 线画到小地图半径的 98%（不压到边框）
local DOT_IN = 0.92          -- 中心点在 92% 以内才画

local issecret = _G.issecretvalue or function() return false end
local function plain(v) if v == nil or issecret(v) then return nil end return v end
local function hidden() return BotStatusMapDB and BotStatusMapDB.hidden end

-- HereBeDragons-Pins 的表：缩放档位 → 小地图直径（码）
local SIZE = {
  indoor = { [0] = 300, [1] = 240, [2] = 180, [3] = 120, [4] = 80, [5] = 50 },
  outdoor = { [0] = 466 + 2 / 3, [1] = 400, [2] = 333 + 1 / 3, [3] = 266 + 2 / 6, [4] = 200, [5] = 133 + 1 / 3 },
}
-- { 左上, 左下, 右上, 右下 } 是不是圆角
local SHAPES = {
  ["SQUARE"] = { false, false, false, false },
  ["CORNER-TOPLEFT"] = { true, false, false, false },
  ["CORNER-TOPRIGHT"] = { false, false, true, false },
  ["CORNER-BOTTOMLEFT"] = { false, true, false, false },
  ["CORNER-BOTTOMRIGHT"] = { false, false, false, true },
  ["SIDE-LEFT"] = { true, true, false, false },
  ["SIDE-RIGHT"] = { false, false, true, true },
  ["SIDE-TOP"] = { true, false, true, false },
  ["SIDE-BOTTOM"] = { false, true, false, true },
  ["TRICORNER-TOPLEFT"] = { true, true, true, false },
  ["TRICORNER-TOPRIGHT"] = { true, false, true, true },
  ["TRICORNER-BOTTOMLEFT"] = { true, true, false, true },
  ["TRICORNER-BOTTOMRIGHT"] = { false, true, true, true },
}

-- 地图坐标(0-1) → 世界坐标：返回 instance, x(=西向), y(=北向)（和 HereBeDragons 一样的约定）
local function world(m, x, y)
  if not m or not C_Map or not C_Map.GetWorldPosFromMapPos then return nil end
  local ok, cont, w = pcall(C_Map.GetWorldPosFromMapPos, m, CreateVector2D(x, y))
  if not ok or not cont or not w or not w.GetXY then return nil end
  local n, wst = w:GetXY()
  n, wst = plain(n), plain(wst)
  if not n or not wst then return nil end
  return plain(cont), wst, n
end
MM.World = world

local function playerWorld()
  local m
  pcall(function() m = plain(C_Map.GetBestMapForUnit("player")) end)
  if m then
    local ok, pos = pcall(C_Map.GetPlayerMapPosition, m, "player")
    if ok and pos and pos.GetXY then
      local px, py = pos:GetXY()
      px, py = plain(px), plain(py)
      if px and py and (px ~= 0 or py ~= 0) then
        local inst, x, y = world(m, px, py)
        if inst then return x, y, inst end
      end
    end
  end
  if UnitPosition then   -- 备用（副本里两个都读不到 → 不画）
    local ok, a, b, _, inst = pcall(UnitPosition, "player")
    a, b, inst = plain(a), plain(b), plain(inst)
    if ok and a and b then return b, a, inst end
  end
  return nil
end

-- 数据 → 世界坐标缓存（只在载入/数据变了时算一次）
function MM.Build()
  local D = BotStatusMapData
  MM.cache, MM.built = {}, D
  if type(D) ~= "table" then return end
  local r = tonumber(D.radius) or 0
  for i, c in ipairs(D.centers or {}) do
    local inst, x, y = world(c[3], c[1] / 100, c[2] / 100)
    if inst then
      local ring, far = {}, 0
      if r > 0 then
        for k = 0, SEGS do
          local a = k / SEGS * 2 * math.pi
          local ri, rx, ry = world(c[3], (c[1] + r * math.cos(a)) / 100, (c[2] + r * math.sin(a)) / 100)
          if ri == inst then
            ring[k] = { rx, ry }
            far = math.max(far, math.sqrt((rx - x) ^ 2 + (ry - y) ^ 2))
          else
            ring[k] = false
          end
        end
      end
      MM.cache[#MM.cache + 1] = { n = i, inst = inst, x = x, y = y, ring = ring, far = far }
    end
  end
end

local function viewRadius(zoom)
  if C_Minimap and C_Minimap.GetViewRadius then
    local ok, r = pcall(C_Minimap.GetViewRadius)
    r = ok and plain(r) or nil
    if type(r) == "number" and r > 0 then MM.rsrc = "C_Minimap"; return r end
  end
  local outdoor = (tonumber(GetCVar and GetCVar("minimapZoom")) or -1) == zoom
  MM.rsrc = outdoor and "室外表" or "室内表"
  return (SIZE[outdoor and "outdoor" or "indoor"][zoom] or SIZE.outdoor[0]) / 2
end

local function getLine()
  MM.nl = MM.nl + 1
  local l = MM.lines[MM.nl]
  if not l then l = MM.frame:CreateLine(nil, "OVERLAY"); MM.lines[MM.nl] = l end
  l:Show()
  return l
end
local function getTex()
  MM.nt = MM.nt + 1
  local t = MM.tex[MM.nt]
  if not t then t = MM.frame:CreateTexture(nil, "OVERLAY"); MM.tex[MM.nt] = t end
  t:Show()
  return t
end
local function getFont()
  MM.nf = MM.nf + 1
  local f = MM.fonts[MM.nf]
  if not f then f = MM.frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall"); MM.fonts[MM.nf] = f end
  f:Show()
  return f
end

-- 视图参数（每次画之前填）
local V = {}

-- 世界坐标 → 小地图上的相对位置（单位 = 视野半径；dx 向右，dy 向上）
local function rel(wx, wy)
  local xd, yd = V.px - wx, V.py - wy
  if V.rot then xd, yd = xd * V.cos - yd * V.sin, xd * V.sin + yd * V.cos end
  return xd / V.R, -yd / V.R
end
MM.Rel = function(wx, wy) return rel(wx, wy) end

local function inside(dx, dy, lim)
  local round = true
  if V.shape and dx ~= 0 and dy ~= 0 then
    local idx = (dx < 0) and 1 or 3
    if dy > 0 then round = V.shape[idx] else round = V.shape[idx + 1] end
  end
  if round then return dx * dx + dy * dy <= lim * lim end
  return math.max(math.abs(dx), math.abs(dy)) <= lim
end
MM.Inside = function(dx, dy, lim, shape) local s = V.shape; V.shape = shape; local r = inside(dx, dy, lim); V.shape = s; return r end

local function seg(ax, ay, bx, by)
  local ia, ib = inside(ax, ay, CLIP), inside(bx, by, CLIP)
  if not ia and not ib then return end
  if ia ~= ib then          -- 一头在外面：二分找边界，截断
    local inx, iny, outx, outy = ax, ay, bx, by
    if ib then inx, iny, outx, outy = bx, by, ax, ay end
    local lo, hi = 0, 1
    for _ = 1, 10 do
      local mid = (lo + hi) / 2
      if inside(inx + (outx - inx) * mid, iny + (outy - iny) * mid, CLIP) then lo = mid else hi = mid end
    end
    bx, by = inx + (outx - inx) * lo, iny + (outy - iny) * lo
    ax, ay = inx, iny
  end
  local l = getLine()
  l:SetColorTexture(RING[1], RING[2], RING[3], RING[4])
  l:SetThickness(1.5)
  l:SetStartPoint("CENTER", MM.map, ax * V.hw, ay * V.hh)
  l:SetEndPoint("CENTER", MM.map, bx * V.hw, by * V.hh)
end

local function clear()
  for i = 1, MM.nl do MM.lines[i]:Hide() end
  for i = 1, MM.nt do MM.tex[i]:Hide() end
  for i = 1, MM.nf do MM.fonts[i]:Hide() end
  MM.nl, MM.nt, MM.nf, MM.drawn, MM.segs = 0, 0, 0, 0, 0
end

local function draw()
  if not MM.frame then MM.why = "小地图图层还没初始化"; return end
  if MM.built ~= BotStatusMapData then MM.Build() end
  clear()
  if hidden() then MM.why = "图层已关"; return end
  if type(BotStatusMapData) ~= "table" or not MM.cache or #MM.cache == 0 then MM.why = "没有中心数据"; return end
  local px, py, inst = playerWorld()
  if not px then MM.why = "读不到你的坐标（副本里不画）"; return end
  local map = MM.map
  local zoom = (map.GetZoom and map:GetZoom()) or 0
  V.px, V.py, V.inst = px, py, inst
  V.R = viewRadius(zoom)
  V.hw, V.hh = (map:GetWidth() or 0) / 2, (map:GetHeight() or 0) / 2
  if V.hw <= 0 or V.hh <= 0 then MM.why = "小地图大小是 0"; return end
  V.rot = GetCVar and GetCVar("rotateMinimap") == "1"
  if V.rot then
    local f
    pcall(function() f = plain(GetPlayerFacing()) end)
    if not f then MM.why = "旋转小地图开着但读不到朝向"; return end
    V.sin, V.cos = math.sin(f), math.cos(f)
  end
  V.shape = GetMinimapShape and SHAPES[GetMinimapShape() or "ROUND"] or nil
  MM.R, MM.zoom, MM.rot, MM.shapeName = V.R, zoom, V.rot, (GetMinimapShape and GetMinimapShape()) or "ROUND"
  local drawn = 0
  for _, c in ipairs(MM.cache) do
    if c.inst == inst then
      local d = math.sqrt((c.x - px) ^ 2 + (c.y - py) ^ 2)
      if d <= V.R * 1.5 + c.far then             -- 远的不算
        local cx, cy = rel(c.x, c.y)
        local any = false
        if inside(cx, cy, DOT_IN) then
          local t = getTex()
          t:SetColorTexture(COL[1], COL[2], COL[3], COL[4])
          t:SetSize(5, 5)
          t:ClearAllPoints()
          t:SetPoint("CENTER", map, "CENTER", cx * V.hw, cy * V.hh)
          local f = getFont()
          f:SetText(tostring(c.n))
          f:SetTextColor(COL[1], COL[2], COL[3], 1)
          f:ClearAllPoints()
          f:SetPoint("BOTTOM", map, "CENTER", cx * V.hw, cy * V.hh + 3)
          any = true
        end
        local before = MM.nl
        local prev
        for k = 0, SEGS do
          local p = c.ring[k]
          local cur = p and { rel(p[1], p[2]) } or nil
          if cur and prev then seg(prev[1], prev[2], cur[1], cur[2]) end
          prev = cur
        end
        if MM.nl > before then any = true end
        if any then drawn = drawn + 1 end
      end
    end
  end
  for i = MM.nl + 1, #MM.lines do MM.lines[i]:Hide() end
  MM.drawn, MM.segs = drawn, MM.nl
  MM.why = drawn > 0 and "" or "附近（小地图范围里）没有中心"
end

function MM.Refresh()
  local ok, err = pcall(draw)
  if not ok then
    MM.err = tostring(err)
    pcall(clear)
    if not MM.errShown then
      MM.errShown = true
      print("|cffff4040BotStatus 小地图图层出错：|r" .. MM.err .. "（输入 /wgmap 看详情）")
    end
  end
end

function MM.StatusLine()
  if not MM.frame then return "小地图：没初始化（" .. tostring(MM.setupErr or "找不到小地图") .. "）" end
  return string.format("小地图：画了 %d 个中心（线 %d 段）；视野半径 %.0f 码（%s，缩放 %s）；形状 %s；旋转 %s%s%s",
    MM.drawn or 0, MM.segs or 0, MM.R or 0, tostring(MM.rsrc or "?"), tostring(MM.zoom), tostring(MM.shapeName or "?"),
    MM.rot and "开" or "关", (MM.why and MM.why ~= "") and ("；" .. MM.why) or "", MM.err and ("；出错：" .. MM.err) or "")
end

function MM.Setup()
  local map = _G.Minimap
  if MM.frame or not map then return MM.frame ~= nil end
  local f = CreateFrame("Frame", "BotStatusMinimapOverlayFrame", map)
  f:SetAllPoints(map)
  f:SetFrameLevel(math.min((map:GetFrameLevel() or 0) + 5, 10000))
  MM.frame, MM.map = f, map
  local acc, lastKey = 0, nil
  f:SetScript("OnUpdate", function(_, el)
    acc = acc + (el or 0)
    if acc < 0.05 then return end
    acc = 0
    local key
    pcall(function()
      local px, py = playerWorld()
      local fc = (GetCVar and GetCVar("rotateMinimap") == "1") and plain(GetPlayerFacing()) or 0
      key = table.concat({ tostring(px), tostring(py), tostring(fc), tostring(map:GetZoom()), tostring(map:GetWidth()),
        tostring(hidden()), tostring(BotStatusMapData ~= nil) }, ":")
    end)
    if key == nil or key ~= lastKey then lastKey = key; MM.Refresh() end
  end)
  MM.Refresh()
  return true
end

-- 大地图那边 刷新/开关/诊断 时一起处理（共用一个开关）
local function hook()
  local W = BotStatusMapOverlay
  if not W then return end
  W.after = W.after or {}
  W.after[#W.after + 1] = MM.Refresh
  W.statusExtra = W.statusExtra or {}
  W.statusExtra[#W.statusExtra + 1] = MM.StatusLine
end

local ev = CreateFrame("Frame")
ev:RegisterEvent("PLAYER_LOGIN")
ev:RegisterEvent("PLAYER_ENTERING_WORLD")
ev:RegisterEvent("MINIMAP_UPDATE_ZOOM")
ev:SetScript("OnEvent", function()
  if not MM.frame then
    local ok, err = pcall(MM.Setup)
    if not ok then
      MM.setupErr = tostring(err)
      if not MM.setupShown then MM.setupShown = true; print("|cffff4040BotStatus 小地图图层初始化失败：|r" .. MM.setupErr) end
    end
  else
    MM.Refresh()
  end
end)
hook()
