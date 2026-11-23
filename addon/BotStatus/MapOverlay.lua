--[[
BotStatus v0.17 + v0.18（v0.18：修“什么都不显示”—— 图层抬到地图所有框架上面；加 /wgmap 诊断、自动重画）：在大地图（世界地图）上画 WowGrinder 的
  · 刷怪中心（黄点 + 编号）和刷怪半径（黄圈）
  · 跑尸路线（蓝点 + 连线，从墓地开始）
  · 修理路线（绿点 + 连线）和修理商点（紫色大点“修”）
数据在 MapData.lua（WowGrinder 每次保存/录点时写入），改完要 /reload。
只画大地图；v0.19 起小地图上也画中心和半径圈（另一个文件，和这里共用开关：M.after / M.statusExtra）。
地图右上角有“WG 图层”按钮可以显示/隐藏（记在 BotStatusMapDB，大小地图一起开关）。
坐标：WowGrinder 存的是 0-100 的地图坐标 + uiMapID；点所在地图和大地图正在显示的地图不一样时，
用 C_Map.GetWorldPosFromMapPos / GetMapPosFromWorldPos 换算（换算不了就不画这个点）。
]]
local COL = {
  center = { 1, 0.85, 0, 1 },
  circle = { 1, 0.85, 0, 0.85 },
  corpse = { 0.45, 0.65, 1, 1 },
  repair = { 0.2, 1, 0.35, 1 },
  vendor = { 1, 0.3, 1, 1 },
}
local CIRCLE_SEGS = 48

local M = { tex = {}, lines = {}, fonts = {}, nt = 0, nl = 0, nf = 0 }
BotStatusMapOverlay = M

local function hidden() return BotStatusMapDB and BotStatusMapDB.hidden end

-- (x, y)：0-1；src → dst 地图。返回 0-1 坐标或 nil
function M.Convert(x, y, src, dst)
  if not src or src == 0 or not dst or src == dst then return x, y end
  local rx, ry
  pcall(function()
    local cont, wpos = C_Map.GetWorldPosFromMapPos(src, CreateVector2D(x, y))
    if not cont or not wpos then return end
    local _, mpos = C_Map.GetMapPosFromWorldPos(cont, wpos, dst)
    if mpos then rx, ry = mpos:GetXY() end
  end)
  if rx and ry and rx > -1 and rx < 2 and ry > -1 and ry < 2 then return rx, ry end
  return nil
end

local function getTex()
  M.nt = M.nt + 1
  local t = M.tex[M.nt]
  if not t then t = M.frame:CreateTexture(nil, "OVERLAY"); M.tex[M.nt] = t end
  t:Show()
  return t
end
local function getLine()
  M.nl = M.nl + 1
  local l = M.lines[M.nl]
  if not l then l = M.frame:CreateLine(nil, "OVERLAY"); M.lines[M.nl] = l end
  l:Show()
  return l
end
local function getFont()
  M.nf = M.nf + 1
  local f = M.fonts[M.nf]
  if not f then f = M.frame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall"); M.fonts[M.nf] = f end
  f:Show()
  return f
end

local W, H = 1, 1
local function dot(x, y, c, size)
  local t = getTex()
  t:SetColorTexture(c[1], c[2], c[3], c[4])
  t:SetSize(size, size)
  t:ClearAllPoints()
  t:SetPoint("CENTER", M.frame, "TOPLEFT", x * W, -y * H)
end
local function seg(x1, y1, x2, y2, c, th)
  local l = getLine()
  l:SetColorTexture(c[1], c[2], c[3], c[4])
  l:SetThickness(th)
  l:SetStartPoint("TOPLEFT", M.frame, x1 * W, -y1 * H)
  l:SetEndPoint("TOPLEFT", M.frame, x2 * W, -y2 * H)
end
local function label(x, y, text, c)
  local f = getFont()
  f:SetText(text)
  f:SetTextColor(c[1], c[2], c[3], 1)
  f:ClearAllPoints()
  f:SetPoint("BOTTOM", M.frame, "TOPLEFT", x * W, -y * H + 4)
end

-- 一串点 {x(0-100), y, map} → 当前地图的 0-1 坐标（换算不了的是 false）
local function project(pts, dst)
  local out = {}
  for i, p in ipairs(pts or {}) do
    local x, y = M.Convert(p[1] / 100, p[2] / 100, p[3], dst)
    out[i] = x and { x, y } or false
  end
  return out
end

local function path(pts, dst, c, th, size, vendor)
  local pp = project(pts, dst)
  local n = 0
  for i = 1, #pp do
    local a, b = pp[i], pp[i + 1]
    if a and b then seg(a[1], a[2], b[1], b[2], c, th) end
    if a then
      n = n + 1
      if vendor and i == vendor then
        dot(a[1], a[2], COL.vendor, size * 2)
        label(a[1], a[2], "修", COL.vendor)
      else
        dot(a[1], a[2], c, size)
      end
    end
  end
  return n
end

local function raiseAbove(canvas)
  -- v0.18：地图底图/图钉也是画布的子框架，层级可能比我们高 → 每次画之前把图层抬到所有子框架上面
  -- （固定 +500 会被区域底图盖住 —— v0.17 什么都看不见的原因；ForeverMaps 也是这样处理的：比画布上最高的子/孙框架高 1 级）
  local top = canvas:GetFrameLevel() or 0
  for _, k in ipairs({ canvas:GetChildren() }) do
    if k ~= M.frame and k.GetFrameLevel then
      top = math.max(top, k:GetFrameLevel() or 0)
      if k.GetChildren then
        for _, g in ipairs({ k:GetChildren() }) do
          if g.GetFrameLevel then top = math.max(top, g:GetFrameLevel() or 0) end
        end
      end
    end
  end
  M.topLevel = top
  local want = math.min(top + 1, 10000)
  if (M.frame:GetFrameLevel() or 0) ~= want then M.frame:SetFrameLevel(want) end
end
M.RaiseAbove = function() if M.frame and M.canvas then pcall(raiseAbove, M.canvas) end end

-- 画布会按缩放比例缩小/放大，线宽、点大小按比例换算，屏幕上看起来一样粗
local function canvasScale()
  local sc = WorldMapFrame and WorldMapFrame.GetCanvasScale and WorldMapFrame:GetCanvasScale()
  return (sc and sc > 0) and sc or 1
end

local function draw()
  if not M.frame then M.why = "图层还没初始化"; return end
  for i = 1, M.nt do M.tex[i]:Hide() end
  for i = 1, M.nl do M.lines[i]:Hide() end
  for i = 1, M.nf do M.fonts[i]:Hide() end
  M.nt, M.nl, M.nf, M.drawn = 0, 0, 0, 0
  local D = BotStatusMapData
  if hidden() then M.why = "图层已关（点按钮或 /wgmap on 打开）"; return end
  if type(D) ~= "table" then M.why = "没有地图数据（MapData.lua 是空的）"; return end
  if not WorldMapFrame or not WorldMapFrame:IsShown() then M.why = "大地图没打开"; return end
  local dst = WorldMapFrame:GetMapID()
  M.mapID = dst
  if not dst then M.why = "取不到当前地图编号"; return end
  W, H = M.frame:GetWidth(), M.frame:GetHeight()
  M.size = { W or 0, H or 0 }
  if not W or W <= 0 or not H or H <= 0 then M.why = "画布大小是 0（稍后自动重画）"; return end
  if M.canvas then pcall(raiseAbove, M.canvas) end
  local sc = canvasScale()
  M.scale = sc
  local th = 2 / sc
  local size = 7 / sc
  local drawn = 0
  -- 刷怪中心 + 半径圈（圈是 地图坐标 下的圆，和 WowGrinder 算距离的方法一样）
  local r = tonumber(D.radius) or 0
  for i, c in ipairs(D.centers or {}) do
    local cx, cy = M.Convert(c[1] / 100, c[2] / 100, c[3], dst)
    if cx then
      drawn = drawn + 1
      dot(cx, cy, COL.center, size * 1.6)
      label(cx, cy, tostring(i), COL.center)
      if r > 0 then
        local prev, first
        for k = 0, CIRCLE_SEGS do
          local ang = k / CIRCLE_SEGS * 2 * math.pi
          local px, py = M.Convert((c[1] + r * math.cos(ang)) / 100, (c[2] + r * math.sin(ang)) / 100, c[3], dst)
          local cur = px and { px, py } or nil
          if cur and prev then seg(prev[1], prev[2], cur[1], cur[2], COL.circle, th) end
          prev = cur
        end
      end
    end
  end
  drawn = drawn + path(D.corpse, dst, COL.corpse, th, size)
  drawn = drawn + path(D.repair, dst, COL.repair, th, size, tonumber(D.vendor) or 0)
  M.drawn = drawn
  M.why = drawn > 0 and "" or ("这张地图（" .. tostring(dst) .. "）上没有能画的点")
end

function M.Refresh()
  local ok, err = pcall(draw)
  if not ok then
    M.err = tostring(err)
    if not M.errShown then
      M.errShown = true
      print("|cffff4040BotStatus 大地图图层出错：|r" .. M.err .. "（输入 /wgmap 看详情，截图发给 WowGrinder）")
    end
  end
  local okk, key = pcall(function()
    return tostring(WorldMapFrame:GetMapID()) .. ":" .. tostring(M.frame and M.frame:GetWidth()) .. ":" .. tostring(canvasScale())
  end)
  M.lastKey = okk and key or nil
  for _, fn in ipairs(M.after or {}) do pcall(fn) end     -- v0.19：小地图图层一起刷新（共用开关）
end

local function count(t) return type(t) == "table" and #t or 0 end

function M.Status()
  local D = BotStatusMapData
  local p = function(s) print("|cff40ff40[WG 图层]|r " .. s) end
  p("插件 v0.19；图层：" .. (hidden() and "关" or "开") .. "；初始化：" .. (M.frame and "成功" or ("没有（" .. tostring(M.setupErr or "找不到大地图") .. "）")))
  if type(D) == "table" then
    p(string.format("数据：%s 写入；中心 %d 个（半径 %s）、跑尸 %d 点、修理 %d 点、修理商 #%s",
      tostring(D.written), count(D.centers), tostring(D.radius), count(D.corpse), count(D.repair), tostring(D.vendor)))
    local c = D.centers and D.centers[1]
    if c then p(string.format("第 1 个中心：%.2f, %.2f，地图 %s", c[1], c[2], tostring(c[3]))) end
  else
    p("数据：没有（在 WowGrinder 里点“保存设置”，再 /reload）")
  end
  local shown = WorldMapFrame and WorldMapFrame:IsShown()
  local mid = WorldMapFrame and WorldMapFrame.GetMapID and WorldMapFrame:GetMapID()
  local best = C_Map and C_Map.GetBestMapForUnit and C_Map.GetBestMapForUnit("player")
  p(string.format("大地图：%s；显示的地图 %s；你所在的地图 %s", shown and "开着" or "关着", tostring(mid), tostring(best)))
  if M.frame then
    p(string.format("画布 %.0f x %.0f，缩放 %.2f，图层层级 %s（地图最高 %s）；上次画了 %d 个点（线 %d 条）%s",
      M.frame:GetWidth() or 0, M.frame:GetHeight() or 0, M.scale or 0, tostring(M.frame:GetFrameLevel()), tostring(M.topLevel),
      M.drawn or 0, M.nl or 0, (M.why and M.why ~= "") and ("；" .. M.why) or ""))
  end
  if M.err then p("|cffff4040出错：|r" .. M.err) end
  for _, fn in ipairs(M.statusExtra or {}) do
    local ok, s = pcall(fn)
    if ok and s then p(s) end
  end
  if not shown then p("先按 M 打开大地图再输入 /wgmap，结果更准") end
end
local function updateButton()
  if not M.button then return end
  M.button:SetText(hidden() and "WG 图层：关" or "WG 图层：开")
end

function M.Toggle()
  BotStatusMapDB = BotStatusMapDB or {}
  BotStatusMapDB.hidden = not BotStatusMapDB.hidden
  updateButton()
  M.Refresh()
end

function M.Setup()
  if M.frame or not WorldMapFrame then return M.frame ~= nil end
  local canvas = (WorldMapFrame.GetCanvas and WorldMapFrame:GetCanvas())
                 or (WorldMapFrame.ScrollContainer and WorldMapFrame.ScrollContainer.Child)
  if not canvas then return false end
  local f = CreateFrame("Frame", "BotStatusMapOverlayFrame", canvas)
  f:SetAllPoints(canvas)
  f:SetFrameLevel(math.min((canvas:GetFrameLevel() or 0) + 1, 10000))   -- 每次画之前再抬到地图所有框架上面
  M.frame = f
  M.canvas = canvas
  local b = CreateFrame("Button", "BotStatusMapToggle", WorldMapFrame, "UIPanelButtonTemplate")
  b:SetSize(110, 22)
  local anchor = WorldMapFrame.ScrollContainer or WorldMapFrame
  b:SetPoint("TOPRIGHT", anchor, "TOPRIGHT", -6, -6)
  b:SetFrameLevel(math.min((anchor:GetFrameLevel() or 0) + 1000, 9500))
  b:SetScript("OnClick", M.Toggle)
  b:SetScript("OnEnter", function(self)
    if not GameTooltip then return end
    GameTooltip:SetOwner(self, "ANCHOR_LEFT")
    GameTooltip:AddLine("WowGrinder 图层（大地图 + 小地图）")
    local D = BotStatusMapData
    if type(D) ~= "table" then
      GameTooltip:AddLine("还没有地图数据：在 WowGrinder 里点一次“保存设置”，然后 /reload", 1, 0.4, 0.4, true)
    else
      GameTooltip:AddLine("黄 = 刷怪中心 + 半径   蓝 = 跑尸路线", 1, 1, 1)
      GameTooltip:AddLine("绿 = 修理路线   紫“修” = 修理商", 1, 1, 1)
      GameTooltip:AddLine("数据时间：" .. tostring(D.written or "?") .. "（改了要 /reload）", 0.7, 0.7, 0.7)
      GameTooltip:AddLine("当前地图画了 " .. tostring(M.drawn or 0) .. " 个点", 0.7, 0.7, 0.7)
    end
    GameTooltip:Show()
  end)
  b:SetScript("OnLeave", function() if GameTooltip then GameTooltip:Hide() end end)
  M.button = b
  updateButton()
  WorldMapFrame:HookScript("OnShow", M.Refresh)
  if WorldMapFrame.OnMapChanged then hooksecurefunc(WorldMapFrame, "OnMapChanged", M.Refresh) end
  f:SetScript("OnSizeChanged", M.Refresh)
  -- v0.18：保险 —— 地图开着时每 0.5 秒看一下地图/画布大小变了没有，变了就重画（防止钩子没触发）
  local acc = 0
  f:SetScript("OnUpdate", function(_, el)
    acc = acc + (el or 0)
    if acc < 0.5 then return end
    acc = 0
    if not WorldMapFrame:IsShown() then return end
    M.RaiseAbove()                                  -- 地图后来新建的框架也压不住我们
    local okk, key = pcall(function()
      return tostring(WorldMapFrame:GetMapID()) .. ":" .. tostring(f:GetWidth()) .. ":" .. tostring(canvasScale())
    end)
    if not okk or key ~= M.lastKey or (M.drawn == 0 and not hidden()) then M.Refresh() end
  end)
  return true
end

local ev = CreateFrame("Frame")
ev:RegisterEvent("ADDON_LOADED")
ev:RegisterEvent("PLAYER_LOGIN")
ev:SetScript("OnEvent", function(_, event, name)
  if event == "ADDON_LOADED" and name == "BotStatus" then
    BotStatusMapDB = BotStatusMapDB or {}
  end
  if not M.frame then
    local ok, err = pcall(M.Setup)
    if not ok then
      M.setupErr = tostring(err)
      if not M.setupShown then
        M.setupShown = true
        print("|cffff4040BotStatus 大地图图层初始化失败：|r" .. M.setupErr)
      end
    end
  end
  if event == "PLAYER_LOGIN" then
    local D = BotStatusMapData
    print(string.format("|cff40ff40BotStatus 大地图/小地图图层|r：%s（输入 /wgmap 看状态，/wgmap on|off 开关）",
      type(D) == "table" and string.format("中心 %d 个、跑尸 %d 点、修理 %d 点（%s）", count(D.centers), count(D.corpse), count(D.repair), tostring(D.written))
      or "没有数据，在 WowGrinder 里点“保存设置”再 /reload"))
  end
end)

SLASH_BOTSTATUSMAP1 = "/wgmap"
SlashCmdList["BOTSTATUSMAP"] = function(msg)
  msg = string.lower(tostring(msg or ""):gsub("^%s+", ""):gsub("%s+$", ""))
  if msg == "on" or msg == "off" or msg == "toggle" then
    BotStatusMapDB = BotStatusMapDB or {}
    if msg == "toggle" then BotStatusMapDB.hidden = not BotStatusMapDB.hidden else BotStatusMapDB.hidden = (msg == "off") end
    updateButton()
  end
  M.Refresh()
  M.Status()
end
