--[[
BotStatus v0.17：在大地图（世界地图）上画 WowGrinder 的
  · 刷怪中心（黄点 + 编号）和刷怪半径（黄圈）
  · 跑尸路线（蓝点 + 连线，从墓地开始）
  · 修理路线（绿点 + 连线）和修理商点（紫色大点“修”）
数据在 MapData.lua（WowGrinder 每次保存/录点时写入），改完要 /reload。
只画大地图，不画小地图。地图右上角有“WG 图层”按钮可以显示/隐藏（记在 BotStatusMapDB）。
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

function M.Refresh()
  if not M.frame then return end
  for i = 1, M.nt do M.tex[i]:Hide() end
  for i = 1, M.nl do M.lines[i]:Hide() end
  for i = 1, M.nf do M.fonts[i]:Hide() end
  M.nt, M.nl, M.nf, M.drawn = 0, 0, 0, 0
  local D = BotStatusMapData
  if hidden() or type(D) ~= "table" or not WorldMapFrame or not WorldMapFrame:IsShown() then return end
  local dst = WorldMapFrame:GetMapID()
  if not dst then return end
  W, H = M.frame:GetWidth(), M.frame:GetHeight()
  if not W or W <= 0 or not H or H <= 0 then return end
  local th = math.max(1.5, W / 600)
  local size = math.max(4, W / 160)
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
  f:SetFrameLevel(math.min((canvas:GetFrameLevel() or 0) + 500, 9000))   -- 盖在地图图钉上面，但仍在地图框里（会被地图裁剪）
  M.frame = f
  local b = CreateFrame("Button", "BotStatusMapToggle", WorldMapFrame, "UIPanelButtonTemplate")
  b:SetSize(110, 22)
  local anchor = WorldMapFrame.ScrollContainer or WorldMapFrame
  b:SetPoint("TOPRIGHT", anchor, "TOPRIGHT", -6, -6)
  b:SetFrameLevel(math.min((anchor:GetFrameLevel() or 0) + 1000, 9500))
  b:SetScript("OnClick", M.Toggle)
  b:SetScript("OnEnter", function(self)
    if not GameTooltip then return end
    GameTooltip:SetOwner(self, "ANCHOR_LEFT")
    GameTooltip:AddLine("WowGrinder 图层")
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
  return true
end

local ev = CreateFrame("Frame")
ev:RegisterEvent("ADDON_LOADED")
ev:RegisterEvent("PLAYER_LOGIN")
ev:SetScript("OnEvent", function(_, event, name)
  if event == "ADDON_LOADED" and name == "BotStatus" then
    BotStatusMapDB = BotStatusMapDB or {}
  end
  if not M.frame then pcall(M.Setup) end
end)

SLASH_BOTSTATUSMAP1 = "/wgmap"
SlashCmdList["BOTSTATUSMAP"] = function() M.Toggle() end
