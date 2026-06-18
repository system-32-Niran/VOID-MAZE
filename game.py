"""

"""

import os
import pygame  
import math
import random
import sys
from collections import deque

import network
import maps.map_dbd

# Place the borderless window at the top-left of the primary display.
# Must be set BEFORE pygame.init() / display.set_mode for SDL to pick it up.
os.environ.setdefault("SDL_VIDEO_WINDOW_POS", "0,0")

pygame.init()

# ── layout ────────────────────────────────────────────────────────────────────
# Maze grid is FIXED across all players (so multiplayer maps are interchangeable).
# CELL size is computed per-machine so the maze scales to each player's screen.
COLS = 51   # must be odd
ROWS = 35   # must be odd (bumped from 27 to use vertical screen space)

PANEL_W = 320

DISPLAY = pygame.display.Info()
SCREEN_W = DISPLAY.current_w
SCREEN_H = DISPLAY.current_h

# total window = whole screen (fullscreen)
W = SCREEN_W
H = SCREEN_H

# cell size to fit the maze inside (SCREEN_W - PANEL_W) x SCREEN_H
CELL = max(8, min((SCREEN_W - PANEL_W) // COLS, SCREEN_H // ROWS))

MAZE_W = COLS * CELL
MAZE_H = ROWS * CELL

# centre the maze within the area to the left of the panel
MAZE_OX = ((SCREEN_W - PANEL_W) - MAZE_W) // 2
MAZE_OY = (SCREEN_H - MAZE_H) // 2

# x coordinate where the panel begins
PANEL_X = SCREEN_W - PANEL_W

FPS = 60
MAZE_CHANGE_INTERVAL = 20.0

GATE_COUNT = 6
GATE_HOLD_TIME    = 0.5   # seconds to hold E to toggle a gate
GATE_COLOR_CLOSED = (180, 120, 0)
GATE_COLOR_OPEN   = (80, 255, 80)

# ── colours ────────────────────────────────────────────────────────────────────
BLACK    = (0, 0, 0)
CYAN     = (0, 255, 200)
WHITE    = (255, 255, 255)
PANEL_BG = (8, 12, 24)

PORTAL_COLORS = [
    (0, 200, 255),
    (255, 160, 0),
    (80, 255, 80),
    (255, 80, 255),
    (255, 220, 0),
    (80, 80, 255),
]

# multiplayer: distinct colour per player slot
PLAYER_COLORS = [
    CYAN,
    (255, 160, 0),
    (80, 255, 80),
    (255, 80, 255),
]

MOVE_DELAY   = 0.11
HUNTER_DELAY = 0.38


# ── maze generation ───────────────────────────────────────────────────────────
def build_maze(rows, cols):
    walls   = [[True]  * cols for _ in range(rows)]
    visited = [[False] * cols for _ in range(rows)]

    stack = [(1, 1)]
    visited[1][1] = True
    walls[1][1]   = False

    dirs = [(0, 2), (2, 0), (0, -2), (-2, 0)]

    while stack:
        r, c = stack[-1]
        random.shuffle(dirs)
        moved = False

        for dr, dc in dirs:
            nr, nc = r + dr, c + dc
            if 0 < nr < rows - 1 and 0 < nc < cols - 1:
                if not visited[nr][nc]:
                    visited[nr][nc] = True
                    walls[nr][nc]   = False
                    walls[r + dr // 2][c + dc // 2] = False
                    stack.append((nr, nc))
                    moved = True
                    break

        if not moved:
            stack.pop()

    return walls


def build_maze_with_rooms(rows, cols, n_rooms=12, room_min=3, room_max=5):
    """DFS perfect maze with N rectangular rooms carved into it.

    The carved rooms break up the 1-cell-wide corridors so the layout has
    real spaces players can maneuver in (closer to the DBD feel) instead of
    single-file passages everywhere. Rooms are carved AFTER the DFS so they
    can overlap or merge with corridors — that's intentional, it gives the
    rooms organic openings."""
    walls = build_maze(rows, cols)
    for _ in range(n_rooms):
        rw = random.randint(room_min, room_max)
        rh = random.randint(room_min, room_max)
        r = random.randint(2, max(2, rows - rh - 2))
        c = random.randint(2, max(2, cols - rw - 2))
        for rr in range(r, r + rh):
            for cc in range(c, c + rw):
                if 0 < rr < rows - 1 and 0 < cc < cols - 1:
                    walls[rr][cc] = False
    return walls


def gate_at(gates, r, c):
    if gates:
        for g in gates:
            if g.r == r and g.c == c:
                return g
    return None


def is_wall_for_path(walls, r, c, gates=None, closed_gates_passable=False):
    """Return True if (r,c) blocks movement/pathing.

    Normal movement treats closed gates as walls. Bot planning can opt into
    treating closed gates as passable waypoints, then hold E when it reaches one.
    """
    rows = len(walls)
    cols = len(walls[0]) if rows else 0
    if r < 0 or r >= rows or c < 0 or c >= cols:
        return True
    if walls[r][c]:
        gate = gate_at(gates, r, c)
        if gate and (gate.is_open or closed_gates_passable):
            return False
        return True
    return False


def is_wall(walls, r, c, gates=None):
    """Return True if (r,c) is a wall.  Open gates are treated as passable.
    Bounds are derived from `walls` so this helper works for any map size."""
    return is_wall_for_path(walls, r, c, gates, False)


# ── nearest free cell ─────────────────────────────────────────────────────────
def nearest_free(walls, r, c, gates=None):
    """BFS from (r,c) to find the nearest non-wall cell."""
    rows = len(walls)
    cols = len(walls[0]) if rows else 0
    if not is_wall(walls, r, c, gates):
        return r, c
    visited = set()
    q = deque([(r, c)])
    visited.add((r, c))
    while q:
        cr, cc = q.popleft()
        for dr, dc in ((0,1),(1,0),(0,-1),(-1,0)):
            nr, nc = cr + dr, cc + dc
            if (nr, nc) not in visited and 0 <= nr < rows and 0 <= nc < cols:
                visited.add((nr, nc))
                if not is_wall(walls, nr, nc, gates):
                    return nr, nc
                q.append((nr, nc))
    return r, c   # fallback (should never happen in a valid maze)


# ── bfs for hunter ────────────────────────────────────────────────────────────

def bfs_adjacent(walls, sr, sc, tr, tc, gates=None, closed_gates_passable=False):
    if abs(sr - tr) + abs(sc - tc) == 1:
        return []
        
    rows = len(walls)
    cols = len(walls[0]) if rows else 0
    dist = [[-1] * cols for _ in range(rows)]
    prev = [[None]  * cols for _ in range(rows)]

    q = deque([(sr, sc)])
    dist[sr][sc] = 0

    target_adj = None
    while q:
        r, c = q.popleft()
        if abs(r - tr) + abs(c - tc) == 1:
            target_adj = (r, c)
            break
        for dr, dc in ((0,1),(1,0),(0,-1),(-1,0)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                if not is_wall_for_path(walls, nr, nc, gates, closed_gates_passable) and dist[nr][nc] == -1:
                    dist[nr][nc] = dist[r][c] + 1
                    prev[nr][nc] = (r, c)
                    q.append((nr, nc))

    if target_adj is None:
        return None

    path = []
    curr = target_adj
    while curr != (sr, sc):
        path.append(curr)
        curr = prev[curr[0]][curr[1]]
    path.reverse()
    return path


def bfs(walls, sr, sc, tr, tc, gates=None, closed_gates_passable=False):
    rows = len(walls)
    cols = len(walls[0]) if rows else 0
    if (sr, sc) == (tr, tc):
        return []

    dist = [[-1] * cols for _ in range(rows)]
    prev = [[None]  * cols for _ in range(rows)]

    q = deque([(sr, sc)])
    dist[sr][sc] = 0

    while q:
        r, c = q.popleft()
        if (r, c) == (tr, tc):
            break
        for dr, dc in ((0,1),(1,0),(0,-1),(-1,0)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                if not is_wall_for_path(walls, nr, nc, gates, closed_gates_passable) and dist[nr][nc] == -1:
                    dist[nr][nc] = dist[r][c] + 1
                    prev[nr][nc] = (r, c)
                    q.append((nr, nc))

    if dist[tr][tc] == -1:
        return None

    path, cur = [], (tr, tc)
    while cur != (sr, sc):
        path.append(cur)
        cur = prev[cur[0]][cur[1]]
    path.reverse()
    return path


# ── helpers ────────────────────────────────────────────────────────────────────
def bfs_distances(walls, sr, sc, gates=None, closed_gates_passable=False):
    rows = len(walls)
    cols = len(walls[0]) if rows else 0
    dist = [[-1] * cols for _ in range(rows)]
    if sr < 0 or sr >= rows or sc < 0 or sc >= cols:
        return dist
    if is_wall_for_path(walls, sr, sc, gates, closed_gates_passable):
        return dist

    q = deque([(sr, sc)])
    dist[sr][sc] = 0
    while q:
        r, c = q.popleft()
        for dr, dc in ((0,1),(1,0),(0,-1),(-1,0)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols:
                if not is_wall_for_path(walls, nr, nc, gates, closed_gates_passable) and dist[nr][nc] == -1:
                    dist[nr][nc] = dist[r][c] + 1
                    q.append((nr, nc))
    return dist


def free_cell(walls, exclude=None):
    """Pick a random free interior cell."""
    rows = len(walls)
    cols = len(walls[0]) if rows else 0
    exclude = set(exclude or [])
    for _ in range(1000):
        r = random.randint(1, rows - 2)
        c = random.randint(1, cols - 2)
        if not walls[r][c] and (r, c) not in exclude:
            return r, c
    return None


def cell_xy(r, c):
    return (c * CELL + CELL // 2 + MAZE_OX, r * CELL + CELL // 2 + MAZE_OY)


# ── gate ──────────────────────────────────────────────────────────────────────
class Gate:
    """A wall cell. Player holds E nearby for GATE_HOLD_TIME to toggle open/closed.

    Once toggled, the gate stays in its new state until the player toggles it
    again. Hunter cannot toggle gates — but Hunter CAN walk through any gate
    the player has left open. This makes gates a strategic tool.
    """

    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.is_open = False
        self._hold = 0.0   # seconds player has been holding E this press
        self._triggered = False   # already toggled during this hold? (must release to re-arm)

    def is_adjacent(self, r, c):
        return abs(r - self.r) + abs(c - self.c) == 1

    def update(self, dt, holding):
        """Opening requires holding E for GATE_HOLD_TIME (with progress arc).
        Closing an already-open gate is INSTANT on a fresh press — the player
        just needs to release E between actions so we don't oscillate.

        holding : bool — at least one player is adjacent AND pressing E
        """
        if not self.is_open:
            # closed → need to hold to open
            if holding and not self._triggered:
                self._hold += dt
                if self._hold >= GATE_HOLD_TIME:
                    self.is_open    = True
                    self._triggered = True
                    self._hold      = 0.0
            elif not holding:
                self._hold      = 0.0
                self._triggered = False
        else:
            # open → fresh press closes it instantly
            if holding and not self._triggered:
                self.is_open    = False
                self._triggered = True
                self._hold      = 0.0
            elif not holding:
                self._triggered = False
                self._hold      = 0.0

    def close(self):
        self.is_open = False
        self._hold = 0.0
        self._triggered = False

    def draw(self, surf, walls):
        x, y = cell_xy(self.r, self.c)
        color = GATE_COLOR_OPEN if self.is_open else GATE_COLOR_CLOSED

        # orient the bar along the wall segment it interrupts:
        #   walls above + below → vertical bar (column-style wall)
        #   walls left + right  → horizontal bar (row-style wall)
        has_above = self.r > 0          and walls[self.r - 1][self.c]
        has_below = self.r < ROWS - 1   and walls[self.r + 1][self.c]
        has_left  = self.c > 0          and walls[self.r][self.c - 1]
        has_right = self.c < COLS - 1   and walls[self.r][self.c + 1]

        thin = max(4, CELL // 5)
        long_ = CELL - 4
        if has_above and has_below and not (has_left and has_right):
            bar_w, bar_h = thin, long_
        elif has_left and has_right and not (has_above and has_below):
            bar_w, bar_h = long_, thin
        elif has_above and has_below:
            bar_w, bar_h = thin, long_
        else:
            bar_w, bar_h = long_, thin

        rect = pygame.Rect(x - bar_w // 2, y - bar_h // 2, bar_w, bar_h)
        pygame.draw.rect(surf, color, rect, border_radius=3)

        # progress arc while player is holding (only relevant while closed)
        if self._hold > 0 and not self._triggered and not self.is_open:
            frac = self._hold / GATE_HOLD_TIME
            arc_rect = pygame.Rect(x - CELL // 2 + 2, y - CELL // 2 + 2,
                                   CELL - 4, CELL - 4)
            pygame.draw.arc(surf, (255, 220, 0), arc_rect,
                            math.pi / 2, math.pi / 2 + math.tau * frac, 3)

        if self.is_open:
            # faint green fill when open
            s = pygame.Surface((CELL, CELL), pygame.SRCALPHA)
            s.fill((80, 255, 80, 40))
            surf.blit(s, (self.c * CELL + MAZE_OX, self.r * CELL + MAZE_OY))


# ── DBD entities ──────────────────────────────────────────────────────────────
GEN_REPAIR_TIME    = 10.0   # seconds of held E to finish a generator
GEN_SKILL_DELAY_MIN = 1.0   # repair seconds before a possible skill check
GEN_SKILL_DELAY_MAX = 2.2
GEN_SKILL_DURATION   = 1.8
GEN_SKILL_ZONE_WIDTH = 0.16
GEN_SKILL_FAIL_PENALTY = 2.0
GEN_SKILL_STUN_TIME  = 1.5
GEN_BOT_SKILL_SUCCESS_RATE = 0.85
FREEZING_POD_IMPRISON = 30.0   # seconds before an imprisoned runner is eliminated
DBD_MATCH_LENGTH   = 7 * 60 # 7 minutes
DBD_GEN_COUNT      = 4
DBD_FREEZING_POD_COUNT = 5
HUNTER_CARRY_TIME   = 10.0   # seconds the hunter can carry a runner before they escape


class Generator:
    """A repairable machine. Runners hold E adjacent to fill its progress bar."""

    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.progress  = 0.0     # 0 .. GEN_REPAIR_TIME
        self.completed = False
        self.skill_active = False
        self.skill_owner_id = None
        self.skill_elapsed = 0.0
        self.skill_duration = GEN_SKILL_DURATION
        self.skill_target_start = 0.65
        self.skill_target_width = GEN_SKILL_ZONE_WIDTH
        self.skill_cooldown = random.uniform(
            GEN_SKILL_DELAY_MIN, GEN_SKILL_DELAY_MAX
        )
        self.skill_participants = []
        self.skill_bot_will_succeed = True

    def is_adjacent(self, r, c):
        return abs(r - self.r) + abs(c - self.c) <= 1   # same cell or 4-neighbour

    def draw(self, surf):
        x, y = cell_xy(self.r, self.c)
        size = int(CELL * 0.75)
        rect = pygame.Rect(x - size // 2, y - size // 2, size, size)

        if self.completed:
            pygame.draw.rect(surf, (80, 255, 80), rect, border_radius=4)
            pygame.draw.rect(surf, WHITE, rect, 2, border_radius=4)
            return

        # base box
        pygame.draw.rect(surf, (40, 80, 140), rect, border_radius=4)

        # in-box fill rises from the bottom as the repair progresses
        if self.progress > 0:
            frac = self.progress / GEN_REPAIR_TIME
            fill_h = max(2, int(size * frac))
            fill_rect = pygame.Rect(rect.x, rect.bottom - fill_h, size, fill_h)
            pygame.draw.rect(surf, (255, 220, 0), fill_rect, border_radius=4)

        pygame.draw.rect(surf, (120, 180, 255), rect, 2, border_radius=4)

        # thicker bar above the cell (always visible while repairing)
        if self.progress > 0:
            frac = self.progress / GEN_REPAIR_TIME
            bar_w = CELL - 6
            bar_h = max(6, CELL // 8)
            bx = x - bar_w // 2
            by = y - CELL // 2 - bar_h - 4
            pygame.draw.rect(surf, (30, 30, 30), (bx, by, bar_w, bar_h),
                             border_radius=2)
            pygame.draw.rect(surf, (255, 220, 0),
                             (bx, by, int(bar_w * frac), bar_h),
                             border_radius=2)
            pygame.draw.rect(surf, WHITE, (bx, by, bar_w, bar_h),
                             1, border_radius=2)


class FreezingPod:
    """A cryo-containment cell. When the hunter brings a runner here they're imprisoned."""

    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.imprisoned_pid = None   # player id currently held, or None

    def is_adjacent(self, r, c):
        return abs(r - self.r) + abs(c - self.c) <= 1

    def draw(self, surf):
        x, y = cell_xy(self.r, self.c)
        size = int(CELL * 0.8)
        rect = pygame.Rect(x - size // 2, y - size // 2, size, size)
        # Icy blue cryo-pod
        pygame.draw.rect(surf, (20, 40, 80), rect, border_radius=6)
        pygame.draw.rect(surf, (80, 180, 220), rect, 2, border_radius=6)
        # Frost lines (horizontal glass bands)
        for i in range(3):
            fy = rect.y + 5 + i * (size // 3)
            pygame.draw.line(surf, (100, 200, 240),
                             (rect.x + 4, fy), (rect.right - 4, fy), 1)
        # Centre glint
        pygame.draw.circle(surf, (150, 230, 255), (x, y), max(2, size // 8))


# ── portal ─────────────────────────────────────────────────────────────────────
class Portal:
    def __init__(self, r, c, pair_idx, color):
        self.r        = r
        self.c        = c
        self.pair_idx = pair_idx
        self.color    = color
        self.phase    = random.uniform(0, math.tau)

    def draw(self, surf, t):
        x, y  = cell_xy(self.r, self.c)
        pulse = 0.65 + 0.35 * math.sin(t * 3 + self.phase)
        size  = int(CELL * 0.42 * pulse)

        for ring in range(4, 0, -1):
            radius = size * ring // 4
            glow   = pygame.Surface((radius * 2 + 4, radius * 2 + 4), pygame.SRCALPHA)
            pygame.draw.circle(glow, (*self.color, int(40 * ring * pulse)),
                               (radius + 2, radius + 2), radius, 2)
            surf.blit(glow, (x - radius - 2, y - radius - 2))

        pygame.draw.circle(surf, self.color, (x, y), max(6, size // 2))
        pygame.draw.circle(surf, WHITE,      (x, y), max(3, size // 5))


# ── player ─────────────────────────────────────────────────────────────────────
class Player:
    def __init__(self, r, c):
        self.r = r
        self.c = c
        self.trail          = deque(maxlen=16)
        self.teleport_flash = 0.0

    def draw(self, surf):
        for i, (tr, tc) in enumerate(self.trail):
            frac  = i / max(1, len(self.trail))
            size  = max(2, int(CELL * 0.12 * frac))
            tx, ty = cell_xy(tr, tc)
            trail = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
            pygame.draw.circle(trail, (0, 255, 200, int(120 * frac)), (size, size), size)
            surf.blit(trail, (tx - size, ty - size))

        x, y   = cell_xy(self.r, self.c)
        radius = int(CELL * 0.30)
        glow   = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
        pygame.draw.circle(glow, (0, 255, 200, 60), (radius * 2, radius * 2), radius * 2)
        surf.blit(glow, (x - radius * 2, y - radius * 2))
        pygame.draw.circle(surf, CYAN, (x, y), radius)


# ── hunter ─────────────────────────────────────────────────────────────────────
class Hunter:
    def __init__(self, r, c):
        self.r     = r
        self.c     = c
        self.trail = deque(maxlen=12)

    def draw(self, surf):
        for i, (tr, tc) in enumerate(self.trail):
            frac  = i / max(1, len(self.trail))
            size  = max(2, int(CELL * 0.11 * frac))
            tx, ty = cell_xy(tr, tc)
            trail = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
            pygame.draw.circle(trail, (255, 60, 60, int(90 * frac)), (size, size), size)
            surf.blit(trail, (tx - size, ty - size))

        x, y = cell_xy(self.r, self.c)
        pygame.draw.circle(surf, (255, 60, 60), (x, y), int(CELL * 0.30))


# ── panel toggle button ────────────────────────────────────────────────────────
class ToggleButton:
    W = 260
    H = 38

    def __init__(self, label, x, y, state=True):
        self.label  = label
        self.rect   = pygame.Rect(x, y, self.W, self.H)
        self.state  = state   # True = ON

    def handle_click(self, pos):
        if self.rect.collidepoint(pos):
            self.state = not self.state
            return True
        return False

    def draw(self, surf, font):
        color_on  = (0, 180, 80)
        color_off = (120, 30, 30)
        color     = color_on if self.state else color_off

        pygame.draw.rect(surf, color,      self.rect, border_radius=6)
        pygame.draw.rect(surf, WHITE,      self.rect, 1, border_radius=6)

        tag  = "ON" if self.state else "OFF"
        text = font.render(f"{self.label}  [{tag}]", True, WHITE)
        surf.blit(text, (self.rect.x + 10, self.rect.y + (self.H - text.get_height()) // 2))


# ── game ───────────────────────────────────────────────────────────────────────
class Game:
    def __init__(self):
        # Borderless window at native resolution → Windows DWM GPU flip
        # presentation (no exclusive fullscreen mode-change, no flicker on
        # Alt-Tab, lower input lag than legacy FULLSCREEN).
        self.surf  = pygame.display.set_mode((W, H), pygame.NOFRAME)
        pygame.display.set_caption("VOID MAZE")
        self.clock = pygame.time.Clock()

        self.font_xl  = pygame.font.Font(None, 72)
        self.font_lg  = pygame.font.Font(None, 54)
        self.font_med = pygame.font.Font(None, 40)
        self.font_sm  = pygame.font.Font(None, 30)

        # ── toggle buttons (positioned in panel) ─────────────────────────────
        bx = PANEL_X + 30
        self.btn_maze    = ToggleButton("MAZE SHIFT",  bx, H - 160, state=True)
        self.btn_hunter  = ToggleButton("HUNTER",      bx, H - 112, state=True)
        self.btn_portals = ToggleButton("PORTALS",     bx, H -  64, state=True)

        # menu state
        self.menu_options = ["SINGLE PLAYER", "MULTIPLAYER", "QUIT"]
        self.menu_index   = 0
        self.menu_rects   = []   # populated each frame in draw_menu

        # lobby state
        self.lobby_options = ["HOST GAME", "JOIN GAME", "BACK"]
        self.lobby_index   = 0
        self.lobby_rects   = []

        # mode picker (host only, between lobby and wait_host)
        self.mode_options = ["ESCAPE MODE", "DBD-MAZE", "BACK"]
        self.mode_index   = 0
        self.mode_rects   = []
        self.mp_mode      = "escape"   # "escape" | "dbd" (DBD-MAZE)

        # ESCAPE-mode settings (host-decided in lobby_wait_host)
        self.mp_settings = {"portals": True, "maze_shift": True, "hunter": "bot", "bot_runners": 0}
        self.settings_rows  = ["PORTALS", "MAZE SHIFT", "HUNTER"]
        self.settings_index = 0

        # match-end screen
        self.mp_winner    = ""   # "runners" / "hunter" / ""
        self.mp_end_rects = {}   # "replay" / "menu" → pygame.Rect for clicks

        # multiplayer state
        self.server          = None   # network.Server when hosting
        self.client          = None   # network.Client when joining
        self.player_id       = 0      # 0 for host, 1..3 for clients
        self.mp_players      = []     # list of player dicts (see host_start_match)
        self.mp_gates_open   = []     # parallel list to self.gates
        self.mp_text_input   = ""     # for typing IP address
        self.mp_status_msg   = ""     # info/error line shown in lobby screens
        self.mp_move_timers  = [0.0] * network.MAX_PLAYERS   # host-side per-player move cd
        self.mp_pending_input = {}    # host-side: latest input per player id
        self.mp_local_input   = {
            "dr": 0, "dc": 0, "e_held": False, "skill_seq": 0
        }  # client-side
        self.mp_skill_seq      = 0     # increments on each SPACE press
        self.mp_skill_seen     = {}    # host-side last consumed sequence per pid
        self.mp_send_timer    = 0.0   # client throttle for sending input
        self.mp_broadcast_timer = 0.0 # host throttle for broadcasting state
        self.mp_prev_positions = {}   # host-side previous cells for crossing catches

        # DBD-specific shared state
        self.mp_generators   = []     # list of Generator
        self.mp_freezing_pods   = []     # list of FreezingPod
        self.mp_match_timer  = 0.0    # countdown seconds remaining in DBD match
        self.exit_unlocked   = True   # escape: always True; DBD: False until all gens done

        # map vote state
        self.mp_map_votes    = {}     # pid -> vote_index (0..3)
        self.mp_vote_timer   = 0.0
        self.mp_vote_options = ["RANDOM MAP", "THE FACILITY", "THE ASYLUM", "THE LABYRINTH"]
        self.mp_vote_index   = 0
        self.mp_selected_map = 0

        self.state = "menu"
        self.reset()

    # ── reset / level ─────────────────────────────────────────────────────────
    def reset(self):
        self.level        = 1
        self.score        = 0
        self.portal_count = 0
        self.new_level()

    def new_level(self):
        self.walls = build_maze(ROWS, COLS)

        self.player = Player(1, 1)
        self.hunter = Hunter(ROWS - 2, COLS - 2)
        self.exit_pos = (ROWS - 2, COLS - 2)

        self.portals = []
        self.gates   = []

        self.move_timer    = 0.0
        self.hunter_timer  = 0.0
        self.maze_timer    = 0.0

        self.build_portals()
        self.build_gates()

    # ── maze shift ────────────────────────────────────────────────────────────
    def shift_maze(self):
        """Regenerate maze, keep player/hunter, snap to nearest free cell."""
        pr, pc = self.player.r, self.player.c
        hr, hc = self.hunter.r, self.hunter.c

        self.walls = build_maze(ROWS, COLS)
        self.gates = []
        self.build_gates()

        # portals stay referenced to old positions — rebuild them too
        self.portals = []
        self.build_portals()

        # snap entities to nearest free cell
        self.player.r, self.player.c = nearest_free(self.walls, pr, pc, self.gates)
        self.hunter.r, self.hunter.c = nearest_free(self.walls, hr, hc, self.gates)
        self.player.trail.clear()
        self.hunter.trail.clear()

    # ── portals ───────────────────────────────────────────────────────────────
    def build_portals(self):
        taken = [(1, 1), self.exit_pos]

        for i in range(6):
            a = free_cell(self.walls, taken)
            if a is None:
                break
            taken.append(a)

            b = free_cell(self.walls, taken)
            if b is None:
                break
            taken.append(b)

            idx   = len(self.portals)
            color = PORTAL_COLORS[i % len(PORTAL_COLORS)]

            self.portals.append(Portal(a[0], a[1], idx + 1, color))
            self.portals.append(Portal(b[0], b[1], idx,     color))

    # ── gates ─────────────────────────────────────────────────────────────────
    def build_gates(self):
        """Pick wall cells that form a clean 2-case door: either walls above+below
        with free cells left+right (player passes horizontally — vertical bar),
        OR walls left+right with free cells above+below (player passes vertically
        — horizontal bar). T-junctions and corners are skipped.
        """
        candidates = [
            (r, c)
            for r in range(1, ROWS - 1)
            for c in range(1, COLS - 1)
            if self.walls[r][c]
        ]
        random.shuffle(candidates)

        for r, c in candidates:
            if len(self.gates) >= GATE_COUNT:
                break
            w_above = self.walls[r - 1][c]
            w_below = self.walls[r + 1][c]
            w_left  = self.walls[r][c - 1]
            w_right = self.walls[r][c + 1]
            vert_case = w_above and w_below and (not w_left) and (not w_right)
            horz_case = w_left  and w_right and (not w_above) and (not w_below)
            if vert_case or horz_case:
                self.gates.append(Gate(r, c))

    # ── drawing ───────────────────────────────────────────────────────────────
    def draw_maze(self):
        self.surf.fill(BLACK)

        gate_cells = {(g.r, g.c) for g in self.gates}

        for r in range(ROWS):
            for c in range(COLS):
                if self.walls[r][c] and (r, c) not in gate_cells:
                    rect = pygame.Rect(c * CELL + MAZE_OX, r * CELL + MAZE_OY,
                                       CELL, CELL)
                    pygame.draw.rect(self.surf, (0, 40, 70),   rect)
                    pygame.draw.rect(self.surf, (0, 90, 140),  rect, 1)

    def draw_exit(self):
        er, ec = self.exit_pos
        x, y   = cell_xy(er, ec)
        size   = int(CELL * 0.35)
        points = [(x, y - size), (x + size, y), (x, y + size), (x - size, y)]
        pygame.draw.polygon(self.surf, WHITE, points)

    def draw_maze_timer_bar(self):
        """Show a thin bar at the top of the maze indicating time until next shift."""
        if not self.btn_maze.state:
            return
        frac  = self.maze_timer / MAZE_CHANGE_INTERVAL
        bar_w = int(MAZE_W * frac)
        pygame.draw.rect(self.surf, (0, 60, 100),  (MAZE_OX, MAZE_OY, MAZE_W, 4))
        pygame.draw.rect(self.surf, (0, 200, 255), (MAZE_OX, MAZE_OY, bar_w, 4))

    def draw_panel(self):
        pygame.draw.rect(self.surf, PANEL_BG, (PANEL_X, 0, PANEL_W, H))
        pygame.draw.line(self.surf, (0, 120, 200), (PANEL_X, 0), (PANEL_X, H), 3)

        title = self.font_lg.render("VOID MAZE", True, CYAN)
        self.surf.blit(title, (PANEL_X + PANEL_W // 2 - title.get_width() // 2, 24))

        stats = [
            f"LEVEL   : {self.level}",
            f"SCORE   : {self.score}",
            f"PORTALS : {self.portal_count}",
        ]
        for i, text in enumerate(stats):
            s = self.font_med.render(text, True, WHITE)
            self.surf.blit(s, (PANEL_X + 24, 110 + i * 48))

        # countdown label
        if self.btn_maze.state:
            secs  = max(0, MAZE_CHANGE_INTERVAL - self.maze_timer)
            label = self.font_sm.render(f"SHIFT IN : {secs:.1f}s", True, (0, 200, 255))
            self.surf.blit(label, (PANEL_X + 24, 110 + 3 * 48))

        # controls hint
        hint_y = H - 200
        hint   = self.font_sm.render("HOLD E : open gate", True, (160, 160, 160))
        self.surf.blit(hint, (PANEL_X + 24, hint_y))

        # toggle buttons
        self.btn_maze.draw(self.surf,    self.font_sm)
        self.btn_hunter.draw(self.surf,  self.font_sm)
        self.btn_portals.draw(self.surf, self.font_sm)

    # ── movement ──────────────────────────────────────────────────────────────
    def try_move(self, dr, dc):
        nr = self.player.r + dr
        nc = self.player.c + dc

        if is_wall(self.walls, nr, nc, self.gates):
            return

        self.player.trail.append((self.player.r, self.player.c))
        self.player.r = nr
        self.player.c = nc
        self.score += 1

        if self.btn_portals.state:
            self.check_portal()
        self.check_exit()
        self.check_caught()

    # ── gate logic ────────────────────────────────────────────────────────────
    def update_gates(self, dt, e_held):
        """Each frame: each gate ticks its hold-progress if player is adjacent
        and holding E. After GATE_HOLD_TIME the gate toggles open/closed and
        stays that way until toggled again.
        """
        for gate in self.gates:
            adjacent = gate.is_adjacent(self.player.r, self.player.c)
            gate.update(dt, e_held and adjacent)

    # ── checks ────────────────────────────────────────────────────────────────
    def check_portal(self):
        for p in self.portals:
            if (p.r, p.c) == (self.player.r, self.player.c):
                dest = self.portals[p.pair_idx]
                self.player.r = dest.r
                self.player.c = dest.c
                self.score       += 15
                self.portal_count += 1
                return

    def check_exit(self):
        if (self.player.r, self.player.c) == self.exit_pos:
            self.score += 100
            self.level += 1
            self.new_level()

    def check_caught(self):
        if (self.player.r, self.player.c) == (self.hunter.r, self.hunter.c):
            self.state = "dead"

    # ── hunter AI ─────────────────────────────────────────────────────────────
    def update_hunter(self, dt):
        if not self.btn_hunter.state:
            return

        self.hunter_timer += dt
        if self.hunter_timer < HUNTER_DELAY:
            return
        self.hunter_timer = 0

        path = bfs(self.walls, self.hunter.r, self.hunter.c,
                   self.player.r, self.player.c, self.gates)

        if path:
            self.hunter.trail.append((self.hunter.r, self.hunter.c))
            self.hunter.r, self.hunter.c = path[0]

        self.check_caught()

    # ── overlay ───────────────────────────────────────────────────────────────
    def draw_overlay(self, title, lines, color):
        ow, oh = 700, 300
        ox = MAZE_OX + MAZE_W // 2 - ow // 2
        oy = MAZE_OY + MAZE_H // 2 - oh // 2

        overlay = pygame.Surface((ow, oh), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 220))
        self.surf.blit(overlay, (ox, oy))
        pygame.draw.rect(self.surf, color, (ox, oy, ow, oh), 3)

        ts = self.font_xl.render(title, True, color)
        self.surf.blit(ts, (ox + ow // 2 - ts.get_width() // 2, oy + 30))

        for i, line in enumerate(lines):
            ls = self.font_med.render(line, True, WHITE)
            self.surf.blit(ls, (ox + ow // 2 - ls.get_width() // 2, oy + 120 + i * 40))

    # ── menu ──────────────────────────────────────────────────────────────────
    def select_menu_option(self):
        choice = self.menu_options[self.menu_index]
        if choice == "SINGLE PLAYER":
            self.reset()
            self.state = "title"
        elif choice == "MULTIPLAYER":
            self.mp_status_msg = ""
            self.state = "lobby"
        elif choice == "QUIT":
            pygame.quit()
            sys.exit()

    def handle_menu_click(self, pos):
        for i, rect in enumerate(self.menu_rects):
            if rect.collidepoint(pos):
                self.menu_index = i
                self.select_menu_option()
                return True
        return False

    # ── lobby (host/join chooser) ─────────────────────────────────────────────
    def select_lobby_option(self):
        choice = self.lobby_options[self.lobby_index]
        if choice == "HOST GAME":
            self.mode_index = 0
            self.mp_status_msg = ""
            self.state = "lobby_mode_pick"
        elif choice == "JOIN GAME":
            self.mp_text_input = ""
            self.mp_status_msg = ""
            self.state = "lobby_join_input"
        elif choice == "BACK":
            self.state = "menu"

    # ── mode picker (host only) ───────────────────────────────────────────────
    def select_mode_option(self):
        choice = self.mode_options[self.mode_index]
        if choice == "ESCAPE MODE":
            self.mp_mode = "escape"
            self.start_host_mode()
        elif choice == "DBD-MAZE":
            self.mp_mode = "dbd"
            self.start_host_mode()
        elif choice == "BACK":
            self.state = "lobby"

    def handle_mode_click(self, pos):
        for i, rect in enumerate(self.mode_rects):
            if rect.collidepoint(pos):
                self.mode_index = i
                self.select_mode_option()
                return True
        return False

    def draw_lobby_mode_pick(self):
        self.surf.fill(BLACK)

        title = self.font_xl.render("CHOOSE MODE", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

        descriptions = {
            "ESCAPE MODE":
                "Co-op: all players race to escape the maze. Bot hunter.",
            "DBD-MAZE":
                "Hunter vs runners on a maze. Runners repair generators to unlock the exit.",
            "BACK":
                "Return to the lobby.",
        }

        self.mode_rects = []
        opt_h   = 70
        start_y = H // 2 - 40

        for i, label in enumerate(self.mode_options):
            selected = (i == self.mode_index)
            color    = CYAN if selected else WHITE
            text     = self.font_lg.render(label, True, color)
            tw, th   = text.get_size()
            x = W // 2 - tw // 2
            y = start_y + i * opt_h

            rect = pygame.Rect(x - 20, y - 6, tw + 40, th + 12)
            self.mode_rects.append(rect)

            if selected:
                pygame.draw.rect(self.surf, (0, 60, 90), rect, border_radius=6)
                pygame.draw.rect(self.surf, CYAN, rect, 2, border_radius=6)

            self.surf.blit(text, (x, y))

            desc = descriptions.get(label, "")
            if desc and selected:
                d = self.font_sm.render(desc, True, (160, 160, 160))
                self.surf.blit(d, (W // 2 - d.get_width() // 2, y + th + 14))

        hint = self.font_sm.render("UP/DOWN/ENTER  -  ESC = back",
                                   True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    def handle_lobby_click(self, pos):
        for i, rect in enumerate(self.lobby_rects):
            if rect.collidepoint(pos):
                self.lobby_index = i
                self.select_lobby_option()
                return True
        return False

    def draw_lobby(self):
        self.surf.fill(BLACK)

        title = self.font_xl.render("MULTIPLAYER", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

        sub = self.font_sm.render("Host opens a server. Join connects to a host's Radmin IP.",
                                  True, (160, 160, 160))
        self.surf.blit(sub, (W // 2 - sub.get_width() // 2, H // 5 + 80))

        self.lobby_rects = []
        opt_h   = 60
        start_y = H // 2

        for i, label in enumerate(self.lobby_options):
            selected = (i == self.lobby_index)
            color    = CYAN if selected else WHITE
            text     = self.font_lg.render(label, True, color)
            tw, th   = text.get_size()
            x = W // 2 - tw // 2
            y = start_y + i * opt_h

            rect = pygame.Rect(x - 20, y - 6, tw + 40, th + 12)
            self.lobby_rects.append(rect)

            if selected:
                pygame.draw.rect(self.surf, (0, 60, 90), rect, border_radius=6)
                pygame.draw.rect(self.surf, CYAN, rect, 2, border_radius=6)

            self.surf.blit(text, (x, y))

        if self.mp_status_msg:
            msg = self.font_sm.render(self.mp_status_msg, True, (255, 200, 80))
            self.surf.blit(msg, (W // 2 - msg.get_width() // 2, H - 100))

        hint = self.font_sm.render("UP/DOWN/ENTER  -  ESC = back", True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    # ── lobby_join_input ──────────────────────────────────────────────────────
    def draw_lobby_join_input(self):
        self.surf.fill(BLACK)

        title = self.font_xl.render("JOIN GAME", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

        prompt = self.font_med.render("Enter host IP (Radmin VPN):", True, WHITE)
        self.surf.blit(prompt, (W // 2 - prompt.get_width() // 2, H // 2 - 60))

        # text box
        box_w, box_h = 480, 60
        bx = W // 2 - box_w // 2
        by = H // 2
        pygame.draw.rect(self.surf, (20, 30, 50), (bx, by, box_w, box_h),
                         border_radius=6)
        pygame.draw.rect(self.surf, CYAN, (bx, by, box_w, box_h),
                         2, border_radius=6)

        txt = self.font_lg.render(self.mp_text_input + "_", True, WHITE)
        self.surf.blit(txt, (bx + 14, by + (box_h - txt.get_height()) // 2))

        port_hint = self.font_sm.render(
            f"Port: {network.DEFAULT_PORT}  -  e.g. 26.123.45.6",
            True, (160, 160, 160))
        self.surf.blit(port_hint, (W // 2 - port_hint.get_width() // 2,
                                   H // 2 + box_h + 14))

        if self.mp_status_msg:
            msg = self.font_sm.render(self.mp_status_msg, True, (255, 120, 120))
            self.surf.blit(msg, (W // 2 - msg.get_width() // 2,
                                 H // 2 + box_h + 50))

        hint = self.font_sm.render("ENTER = connect  -  ESC = back", True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    # ── ESCAPE-mode settings (host adjusts in the wait room) ──────────────────
    def _settings_value_str(self, key):
        v = self.mp_settings.get(key)
        if isinstance(v, bool):
            return "ON" if v else "OFF"
        return str(v).upper()

    def cycle_setting(self, key, direction):
        """direction: +1 or -1.  Booleans flip; HUNTER cycles OFF/BOT/PLAYER."""
        v = self.mp_settings.get(key)
        if isinstance(v, bool):
            self.mp_settings[key] = not v
        elif key == "hunter":
            choices = ["off", "bot", "player"]
            idx = choices.index(v) if v in choices else 1
            self.mp_settings[key] = choices[(idx + direction) % len(choices)]
        elif key == "bot_runners":
            max_bots = max(0, network.MAX_PLAYERS - self.server.count() - 1)
            v = max(0, min(v + direction, max_bots))
            self.mp_settings[key] = v

    # ── lobby_wait (host or client waiting room) ──────────────────────────────
    def draw_lobby_wait(self):
        self.surf.fill(BLACK)

        if self.server is not None:
            title = self.font_xl.render("HOSTING", True, CYAN)
            self.surf.blit(title, (W // 2 - title.get_width() // 2, 60))

            mode_label = {
                "escape": "ESCAPE MODE",
                "dbd":    "DBD-MAZE",
            }.get(self.mp_mode, self.mp_mode.upper())
            mode_text = self.font_med.render(f"Mode: {mode_label}",
                                             True, (255, 220, 80))
            self.surf.blit(mode_text,
                           (W // 2 - mode_text.get_width() // 2, 140))

            ip_hint = self.font_sm.render(
                f"Share your Radmin IP with players  -  Port {self.server.port}",
                True, (160, 160, 160))
            self.surf.blit(ip_hint, (W // 2 - ip_hint.get_width() // 2, 190))

            n = self.server.count()
            count = self.font_lg.render(f"Players: {n + 1} / {network.MAX_PLAYERS}",
                                        True, WHITE)
            self.surf.blit(count, (W // 2 - count.get_width() // 2, 240))

            # host-decided settings
            active_rows = ["PORTALS", "MAZE SHIFT", "HUNTER"] if self.mp_mode == "escape" else ["BOT RUNNERS"]
            self.settings_rows = active_rows
            if self.settings_index >= len(self.settings_rows):
                self.settings_index = 0

            head = self.font_med.render("SETTINGS", True, WHITE)
            self.surf.blit(head, (W // 2 - head.get_width() // 2, 320))

            row_h = 50
            start_y = 370
            for i, key_name in enumerate(self.settings_rows):
                key  = key_name.lower().replace(" ", "_")
                val  = self._settings_value_str(key)
                if key == "bot_runners":
                    max_bots = max(0, network.MAX_PLAYERS - self.server.count() - 1)
                    if self.mp_settings[key] > max_bots:
                        self.mp_settings[key] = max_bots
                        val = str(max_bots)
                sel  = (i == self.settings_index)
                col  = CYAN if sel else WHITE
                text = self.font_med.render(f"{key_name:<12s}  <  {val}  >", True, col)
                tw, th = text.get_size()
                x = W // 2 - tw // 2
                y = start_y + i * row_h
                if sel:
                    pygame.draw.rect(self.surf, (0, 60, 90), (x - 16, y - 4, tw + 32, th + 8), border_radius=4)
                self.surf.blit(text, (x, y))

            hint1 = self.font_sm.render(
                "UP/DOWN: select  -  LEFT/RIGHT: change value",
                True, (160, 160, 160))
            self.surf.blit(hint1, (W // 2 - hint1.get_width() // 2,
                                   start_y + len(self.settings_rows) * row_h + 30))

            hint = self.font_med.render("SPACE = start  -  ESC = cancel",
                                        True, (200, 200, 200))
            self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 90))

        elif self.client is not None:
            title = self.font_xl.render("CONNECTED", True, CYAN)
            self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 5))

            wait = self.font_lg.render(f"You are PLAYER {self.player_id + 1}",
                                       True, PLAYER_COLORS[self.player_id])
            self.surf.blit(wait, (W // 2 - wait.get_width() // 2, H // 2 - 30))

            msg = self.font_med.render("Waiting for host to press SPACE...",
                                       True, (200, 200, 200))
            self.surf.blit(msg, (W // 2 - msg.get_width() // 2, H // 2 + 40))

            hint = self.font_sm.render("ESC = disconnect", True, (120, 120, 120))
            self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))


    def draw_lobby_map_vote(self):
        self.surf.fill(BLACK)
        title = self.font_xl.render("VOTE MAP", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, 60))

        time_left = max(0, int(self.mp_vote_timer))
        time_text = self.font_lg.render(f"Time: {time_left}s", True, (255, 100, 100) if time_left <= 5 else WHITE)
        self.surf.blit(time_text, (W // 2 - time_text.get_width() // 2, 120))

        opt_h = 50
        start_y = 220
        # Calculate votes
        vote_counts = [0] * len(self.mp_vote_options)
        for v in self.mp_map_votes.values():
            if 0 <= v < len(vote_counts):
                vote_counts[v] += 1

        for i, label in enumerate(self.mp_vote_options):
            selected = (i == self.mp_vote_index)
            color = CYAN if selected else WHITE
            vc = vote_counts[i]
            text = self.font_med.render(f"{label} ({vc} votes)", True, color)
            tw, th = text.get_size()
            x = W // 2 - tw // 2
            y = start_y + i * opt_h

            if selected:
                pygame.draw.rect(self.surf, (0, 60, 90), (x - 20, y - 6, tw + 40, th + 12), border_radius=6)
                pygame.draw.rect(self.surf, CYAN, (x - 20, y - 6, tw + 40, th + 12), 2, border_radius=6)

            self.surf.blit(text, (x, y))

            # Draw who voted for this
            voters = [str(pid+1) if pid > 0 else "HOST" for pid, v in self.mp_map_votes.items() if v == i]
            if voters:
                v_text = self.font_sm.render(", ".join(voters), True, (160, 160, 160))
                self.surf.blit(v_text, (x + tw + 30, y + th//2 - v_text.get_height()//2))

        hint = self.font_sm.render("UP/DOWN: change  -  ENTER: confirm vote", True, (160, 160, 160))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))


    def draw_menu(self):
        self.surf.fill(BLACK)

        # title
        title = self.font_xl.render("VOID MAZE", True, CYAN)
        self.surf.blit(title, (W // 2 - title.get_width() // 2, H // 4))

        subtitle = self.font_sm.render("SELECT MODE", True, (160, 160, 160))
        self.surf.blit(subtitle, (W // 2 - subtitle.get_width() // 2, H // 4 + 80))

        # options
        self.menu_rects = []
        opt_h = 60
        start_y = H // 2

        for i, label in enumerate(self.menu_options):
            selected = (i == self.menu_index)
            color    = CYAN if selected else WHITE
            text     = self.font_lg.render(label, True, color)
            tw, th   = text.get_size()
            x = W // 2 - tw // 2
            y = start_y + i * opt_h

            rect = pygame.Rect(x - 20, y - 6, tw + 40, th + 12)
            self.menu_rects.append(rect)

            if selected:
                pygame.draw.rect(self.surf, (0, 60, 90), rect, border_radius=6)
                pygame.draw.rect(self.surf, CYAN, rect, 2, border_radius=6)

            self.surf.blit(text, (x, y))

        # footer hint
        hint = self.font_sm.render("UP/DOWN to navigate, ENTER to select, ESC to quit",
                                   True, (120, 120, 120))
        self.surf.blit(hint, (W // 2 - hint.get_width() // 2, H - 60))

    # ── multiplayer: host/client lifecycle ────────────────────────────────────
    def start_host_mode(self):
        """Open a TCP server on DEFAULT_PORT and become player 0."""
        try:
            self.server = network.Server()
        except Exception as e:
            self.mp_status_msg = f"Server failed to start: {e}"
            return
        self.player_id = 0
        self.mp_status_msg = ""
        self.state = "lobby_wait_host"

    def start_client_mode(self, ip):
        """Connect to a host's TCP server. We get our player_id back via 'welcome'."""
        try:
            self.client = network.Client(ip.strip(), network.DEFAULT_PORT)
        except Exception as e:
            self.mp_status_msg = f"Connection failed: {e}"
            self.client = None
            return
        # send hello, wait briefly for welcome (handled in tick)
        self.client.send({"type": "hello"})
        self.mp_status_msg = ""
        self.state = "lobby_wait_client"

    def mp_disconnect(self):
        """Tear down any open server/client; return to main menu."""
        if self.server is not None:
            try: self.server.close()
            except Exception: pass
            self.server = None
        if self.client is not None:
            try: self.client.close()
            except Exception: pass
            self.client = None
        self.mp_players      = []
        self.mp_pending_input = {}
        self.mp_skill_seen    = {}
        self.mp_skill_seq     = 0
        self.mp_local_input   = {
            "dr": 0, "dc": 0, "e_held": False, "skill_seq": 0
        }
        self.mp_generators   = []
        self.mp_freezing_pods   = []
        self.mp_winner       = ""
        self.exit_unlocked   = True
        self.player_id        = 0
        self.state            = "menu"

    # ── host: start a multiplayer match ───────────────────────────────────────

    def host_start_dbd_match(self):
        self.server.prune_dead()
        self.new_level()
        self.mp_generators = []
        self.mp_freezing_pods = []
        self.mp_skill_seen = {}
        self.mp_winner = ""
        self.mp_match_timer = DBD_MATCH_LENGTH
        self.exit_unlocked = False
        
        slots = 1 + self.server.count() + self.mp_settings.get("bot_runners", 0)
        self.mp_players = []
        
        # Load map
        if self.mp_selected_map == 0:
            # Random Map: DFS maze + carved rectangular rooms (so the layout
            # has actual open spaces players can maneuver in, not 1-cell-wide
            # corridors everywhere).
            # Previously this branch did `self.walls = [[True]*COLS ...]` which
            # turned the entire grid into walls — that's why the random map
            # appeared blank/broken.
            self.walls = build_maze_with_rooms(ROWS, COLS)
            self.gates = []
            self.build_gates()
            self.portals = []
            gen_pos, pod_pos = [], []

            runner_spawns = [(1, 1), (1, 3), (3, 1), (3, 3)]
            self.exit_pos = nearest_free(self.walls, ROWS - 2, COLS - 2, self.gates)
            hunter_r, hunter_c = nearest_free(
                self.walls, ROWS - 2, COLS - 2, self.gates
            )

            taken = [(hunter_r, hunter_c)]
            for _ in range(DBD_GEN_COUNT):
                cell = free_cell(self.walls, taken)
                if cell: taken.append(cell); gen_pos.append(cell)
            for _ in range(DBD_FREEZING_POD_COUNT):
                cell = free_cell(self.walls, taken)
                if cell: taken.append(cell); pod_pos.append(cell)
                
        else:
            if self.mp_selected_map == 1:
                (self.walls, gen_pos, pod_pos, runner_spawns, hunter_spawn, exit_pos) = maps.map_dbd.build_dbd_facility()
            elif self.mp_selected_map == 2:
                (self.walls, gen_pos, pod_pos, runner_spawns, hunter_spawn, exit_pos) = maps.map_dbd.build_dbd_facility_2()
            else:
                (self.walls, gen_pos, pod_pos, runner_spawns, hunter_spawn, exit_pos) = maps.map_dbd.build_dbd_facility_3()
            
            self.exit_pos = exit_pos
            self.gates = []
            self.build_gates()
            self.portals = []
            hunter_r, hunter_c = nearest_free(self.walls, *hunter_spawn, self.gates)

        # Spawns
        hunter_idx = random.randrange(slots)
        runner_seen = 0
        
        # Human slots: 0 to server.count()
        human_count = 1 + self.server.count()
        for i in range(slots):
            is_bot = i >= human_count
            if i == hunter_idx:
                r, c, role = hunter_r, hunter_c, "hunter"
            else:
                sr, sc = runner_spawns[runner_seen % len(runner_spawns)]
                r, c = nearest_free(self.walls, sr, sc, self.gates)
                role = "runner"
                runner_seen += 1
                
            self.mp_players.append({
                "id": i, "r": r, "c": c, "alive": True,
                "role": role, "is_bot": is_bot,
                "imprisoned": False, "imprison_remaining": 0.0,
                "carried": False, "carry_timer": 0.0,
                "carrying_pid": None, "catch_cooldown": 0.0,
                "stun_remaining": 0.0,
                # AI state — only used for bots. Each bot commits to its
                # current "ai_state" until "ai_timer" hits 0 (anti-oscillation).
                "ai_state":     "idle",
                "ai_timer":     0.0,
                "ai_target_id": None,
                "ai_target_pos": None,
                "ai_last_pos":  None,
                "ai_recent":    [],
            })
            self.mp_move_timers.append(0.0)

        for cell in gen_pos:
            self.mp_generators.append(Generator(cell[0], cell[1]))
        for cell in pod_pos:
            self.mp_freezing_pods.append(FreezingPod(cell[0], cell[1]))
            
        self.state = "mp_play"
        self.server.broadcast({"type": "start", **self._serialize_state()})


    def host_start_match(self):
        """Host pressed SPACE — initialise level and broadcast maze + start."""
        # clean any dead connections BEFORE locking in player_id assignments
        self.server.prune_dead()

        self.new_level()
        self.mp_generators = []
        self.mp_freezing_pods = []
        self.mp_skill_seen = {}
        self.mp_winner     = ""
        self.mp_match_timer = DBD_MATCH_LENGTH

        # initial player spots: corners-ish around (1, 1) for runners,
        # opposite corner for hunter in DBD
        runner_spawns = [(1, 1), (1, 3), (3, 1), (3, 3)]
        slots = 1 + self.server.count()
        self.mp_players = []

        if self.mp_mode == "escape":
            self.exit_unlocked = True
            # apply host-decided ESCAPE settings
            if not self.mp_settings.get("portals", True):
                self.portals = []

            hunter_setting = self.mp_settings.get("hunter", "bot")
            hunter_idx = -1
            if hunter_setting == "off":
                self.hunter = None
            elif hunter_setting == "player":
                self.hunter = None   # no bot — a player is the hunter
                hunter_idx = random.randrange(slots)
            # "bot" → keep self.hunter from new_level()

            runner_seen = 0
            for i in range(slots):
                if i == hunter_idx:
                    r, c = nearest_free(self.walls, ROWS // 2, COLS // 2,
                                        self.gates)
                    role = "hunter"
                else:
                    sr, sc = runner_spawns[runner_seen] \
                             if runner_seen < len(runner_spawns) else (1, 1)
                    r, c = nearest_free(self.walls, sr, sc, self.gates)
                    role = "runner"
                    runner_seen += 1
                self.mp_players.append({
                    "id": i, "r": r, "c": c, "alive": True,
                    "role": role,
                    "imprisoned": False, "imprison_remaining": 0.0,
                    "carried": False, "carry_timer": 0.0,
                    "carrying_pid": None, "catch_cooldown": 0.0,
                    "stun_remaining": 0.0,
                })
        else:  # DBD-MAZE
            self.exit_unlocked = False
            # randomly pick which slot is the hunter
            hunter_idx = random.randrange(slots)
            # Spawn hunter at the centre of the maze.
            hunter_r, hunter_c = nearest_free(self.walls,
                                              ROWS // 2, COLS // 2, self.gates)
            runner_seen = 0
            for i in range(slots):
                if i == hunter_idx:
                    r, c, role = hunter_r, hunter_c, "hunter"
                else:
                    sr, sc = runner_spawns[runner_seen] \
                             if runner_seen < len(runner_spawns) else (1, 1)
                    r, c = nearest_free(self.walls, sr, sc, self.gates)
                    role = "runner"
                    runner_seen += 1
                self.mp_players.append({
                    "id": i, "r": r, "c": c, "alive": True,
                    "role": role,
                    "imprisoned": False, "imprison_remaining": 0.0,
                    "carried": False, "carry_timer": 0.0,
                    "carrying_pid": None, "catch_cooldown": 0.0,
                    "stun_remaining": 0.0,
                })
            # place generators + freezing pods on free cells
            taken = [(p["r"], p["c"]) for p in self.mp_players]
            for _ in range(DBD_GEN_COUNT):
                cell = free_cell(self.walls, taken)
                if cell is None: break
                taken.append(cell)
                self.mp_generators.append(Generator(cell[0], cell[1]))
            for _ in range(DBD_FREEZING_POD_COUNT):
                cell = free_cell(self.walls, taken)
                if cell is None: break
                taken.append(cell)
                self.mp_freezing_pods.append(FreezingPod(cell[0], cell[1]))
            # DBD-MAZE: no portals
            self.portals = []

        self.mp_move_timers = [0.0] * network.MAX_PLAYERS

        # broadcast maze then start (include mode + DBD entities)
        self.server.broadcast(self._serialize_maze())
        self.server.broadcast({"type": "start", "mode": self.mp_mode})
        self.state = "mp_play"

    def _serialize_maze(self):
        """Compact maze description sent on level start / maze shift."""
        wall_rows = ["".join("1" if w else "0" for w in row) for row in self.walls]
        rows = len(self.walls)
        cols = len(self.walls[0]) if rows else 0
        return {
            "type":    "maze",
            "mode":    self.mp_mode,
            "rows":    rows,
            "cols":    cols,
            "walls":   wall_rows,
            "exit":    list(self.exit_pos),
            "gates":   [[g.r, g.c] for g in self.gates],
            "portals": [[p.r, p.c, p.pair_idx, list(p.color)] for p in self.portals],
            "generators": [[g.r, g.c] for g in self.mp_generators],
            "freezing_pods": [[w.r, w.c] for w in self.mp_freezing_pods],
        }

    def _apply_maze(self, msg):
        """Client-side: rebuild local maze structures from a 'maze' message."""
        self.mp_mode = msg.get("mode", "escape")
        self.walls = [[c == "1" for c in row] for row in msg["walls"]]
        self.exit_pos = tuple(msg["exit"])
        self.gates = [Gate(r, c) for r, c in msg["gates"]]
        self.portals = [Portal(r, c, pi, tuple(co))
                        for r, c, pi, co in msg["portals"]]
        self.mp_generators = [Generator(r, c) for r, c in msg.get("generators", [])]
        self.mp_freezing_pods = [FreezingPod(r, c) for r, c in msg.get("freezing_pods", [])]

    def _serialize_state(self):
        hunter_pos = None
        if self.mp_mode == "escape" and self.hunter is not None:
            hunter_pos = [self.hunter.r, self.hunter.c]
        return {
            "type":    "state",
            "players": self.mp_players,
            "hunter":  hunter_pos,
            "gates":   [g.is_open for g in self.gates],
            "level":   self.level,
            "gens":    [
                [
                    g.progress, g.completed,
                    g.skill_active, g.skill_owner_id,
                    g.skill_elapsed, g.skill_duration,
                    g.skill_target_start, g.skill_target_width,
                ]
                for g in self.mp_generators
            ],
            "fp":      [w.imprisoned_pid for w in self.mp_freezing_pods],
            "timer":   self.mp_match_timer,
            "exit_unlocked": self.exit_unlocked,
        }

    def _apply_state(self, msg):
        self.mp_players = msg["players"]
        hp = msg.get("hunter")
        if hp is not None:
            if self.hunter is None:
                self.hunter = Hunter(*hp)
            else:
                self.hunter.r, self.hunter.c = hp
        else:
            self.hunter = None
        opens = msg.get("gates", [])
        for i, g in enumerate(self.gates):
            if i < len(opens):
                g.is_open = bool(opens[i])
        self.level = msg.get("level", self.level)
        gens = msg.get("gens", [])
        for i, g in enumerate(self.mp_generators):
            if i < len(gens):
                state = gens[i]
                g.progress, g.completed = state[:2]
                if len(state) >= 8:
                    g.skill_active = bool(state[2])
                    g.skill_owner_id = state[3]
                    g.skill_elapsed = float(state[4])
                    g.skill_duration = float(state[5])
                    g.skill_target_start = float(state[6])
                    g.skill_target_width = float(state[7])
        wh = msg.get("fp", [])
        for i, w in enumerate(self.mp_freezing_pods):
            if i < len(wh):
                w.imprisoned_pid = wh[i]
        self.mp_match_timer = msg.get("timer", self.mp_match_timer)
        self.exit_unlocked  = msg.get("exit_unlocked", True)

    # ── host: per-frame tick ──────────────────────────────────────────────────
    def mp_host_tick(self, dt):
        """Authoritative game step + state broadcast (branches on mp_mode)."""
        # 1. ingest client messages
        for ci, msg in self.server.drain_all():
            if msg.get("type") == "input":
                pid = ci + 1
                if pid < len(self.mp_players):
                    self.mp_pending_input[pid] = msg

        # mark disconnected clients as dead
        for ci in self.server.dead_indices():
            pid = ci + 1
            if pid < len(self.mp_players) and self.mp_players[pid]["alive"]:
                self.mp_players[pid]["alive"] = False

        # 2. host's own input as player 0
        self.mp_pending_input[0] = self.mp_local_input

        for p in self.mp_players:
            p["stun_remaining"] = max(
                0.0, p.get("stun_remaining", 0.0) - dt
            )

        # 3. per-player movement (disabled while imprisoned, carried, or stunned)
        self.mp_prev_positions = {p["id"]: (p["r"], p["c"]) for p in self.mp_players}
        for p in self.mp_players:
            if not p["alive"] or p.get("imprisoned") or p.get("carried") \
                    or p.get("stun_remaining", 0.0) > 0:
                continue
            pid = p["id"]
            self.mp_move_timers[pid] += dt
            inp = self.mp_pending_input.get(pid, {"dr": 0, "dc": 0, "e_held": False})
            dr, dc = int(inp.get("dr", 0)), int(inp.get("dc", 0))
            if (dr or dc) and self.mp_move_timers[pid] >= MOVE_DELAY:
                nr, nc = p["r"] + dr, p["c"] + dc
                if not is_wall(self.walls, nr, nc, self.gates):
                    p["r"], p["c"] = nr, nc
                    self.mp_move_timers[pid] = 0.0
                    # portal teleport (escape mode only — DBD has no portals)
                    if self.mp_mode == "escape":
                        for portal in self.portals:
                            if (portal.r, portal.c) == (nr, nc):
                                dest = self.portals[portal.pair_idx]
                                p["r"], p["c"] = dest.r, dest.c
                                break
                    # exit reached
                    if (p["r"], p["c"]) == self.exit_pos:
                        if self.mp_mode == "escape":
                            # only a runner triggers exit; hunter walking on it does nothing
                            if p.get("role", "runner") != "runner":
                                pass
                            elif self.mp_settings.get("hunter") == "player":
                                # player hunter mode: first runner to exit wins the match
                                self.mp_winner = "runners"
                                self.state    = "mp_end"
                                self.server.broadcast(self._serialize_state())
                                self.server.broadcast({"type": "end", "winner": "runners"})
                                return
                            else:
                                self.level += 1
                                self.host_advance_level()
                                return
                        elif self.mp_mode == "dbd" and p["role"] == "runner" \
                             and self.exit_unlocked:
                            # Runner reached the exit after all gens were repaired.
                            self.mp_winner = "runners"
                            self.state = "mp_end"
                            self.server.broadcast({"type": "end", "winner": "runners"})
                            return

        # 4. gates — any adjacent alive player holding E drives toggle
        for gate in self.gates:
            holding = False
            for p in self.mp_players:
                if p["alive"] and not p.get("imprisoned") and not p.get("carried") \
                   and p.get("stun_remaining", 0.0) <= 0 \
                   and gate.is_adjacent(p["r"], p["c"]):
                    inp = self.mp_pending_input.get(p["id"], {})
                    if inp.get("e_held"):
                        holding = True
                        break
            gate.update(dt, holding)

        # 5. mode-specific logic
        if self.mp_mode == "escape":
            # PLAYER hunter: any runner sharing the hunter's cell is eliminated
            if self.mp_settings.get("hunter") == "player":
                hp = next((p for p in self.mp_players
                           if p.get("role") == "hunter" and p["alive"]), None)
                if hp is not None:
                    for p in self.mp_players:
                        if p.get("role") == "runner" and p["alive"] \
                           and (p["r"], p["c"]) == (hp["r"], hp["c"]):
                            p["alive"] = False
                    runners = [p for p in self.mp_players
                               if p.get("role") == "runner"]
                    if runners and all(not p["alive"] for p in runners):
                        self.mp_winner = "hunter"
                        self.state     = "mp_end"
                        self.server.broadcast(self._serialize_state())
                        self.server.broadcast({"type": "end", "winner": "hunter"})
                        return

            # bot hunter (only if HUNTER setting is "bot" and we have one)
            if self.mp_settings.get("hunter", "bot") == "bot" and self.hunter is not None:
                self.hunter_timer += dt
                if self.hunter_timer >= HUNTER_DELAY:
                    self.hunter_timer = 0.0
                    target = self._nearest_alive_to(self.hunter.r, self.hunter.c)
                    if target is not None:
                        path = bfs(self.walls, self.hunter.r, self.hunter.c,
                                   target["r"], target["c"], self.gates)
                        if path:
                            self.hunter.r, self.hunter.c = path[0]
                        for p in self.mp_players:
                            if p["alive"] and (p["r"], p["c"]) == (self.hunter.r, self.hunter.c):
                                p["alive"] = False
            # maze shift
            if self.mp_settings.get("maze_shift", True):
                self.maze_timer += dt
                if self.maze_timer >= MAZE_CHANGE_INTERVAL:
                    self.maze_timer = 0.0
                    self.host_shift_maze()

        else:  # DBD-MAZE
            self._bot_tick(dt)
            self._dbd_tick(dt)
            if self.state == "mp_end":
                return

        # 6. clear pending inputs
        # self.mp_pending_input = {} # REMOVED: keep input states to sync speeds

        # 7. broadcast state ~30Hz
        self.mp_broadcast_timer += dt
        if self.mp_broadcast_timer >= 1.0 / 30.0:
            self.mp_broadcast_timer = 0.0
            self.server.broadcast(self._serialize_state())

    # ── DBD core logic ────────────────────────────────────────────────────────

    # ── bot AI tunables ───────────────────────────────────────────────────────
    # The runner FLEE state commits for FLEE_COMMIT seconds; the runner only
    # leaves FLEE once the timer hits 0 AND the hunter is at least
    # SAFE_DIST cells away. This hysteresis is what stops the
    # "repair → see hunter → flee 1 cell → see hunter further → repair → ..."
    # oscillation loop.
    BOT_FLEE_TRIGGER_DIST = 6   # enter FLEE when hunter ≤ this many cells away
    BOT_FLEE_SAFE_DIST    = 12  # only exit FLEE when hunter ≥ this many cells
    BOT_FLEE_COMMIT_SEC   = 4.0 # min seconds to stay in FLEE
    BOT_FLEE_TARGET_RADIUS = 10 # reachable cells scanned for a safe flee target
    BOT_RESCUE_DANGER_DIST = 5  # avoid sending bots into a camped freezing pod
    BOT_HUNTER_COMMIT_SEC = 3.0 # hunter sticks with one target this long

    def _bot_tick(self, dt):
        """Generate inputs for all bot players (runners and hunter).

        Anti-oscillation strategy:
        - Runner bots commit to FLEE for `BOT_FLEE_COMMIT_SEC`. They only return
          to repair / rescue when the timer expires AND the hunter is at least
          `BOT_FLEE_SAFE_DIST` away (hysteresis on the distance threshold).
        - Hunter bot commits to a single target for `BOT_HUNTER_COMMIT_SEC`
          before considering a switch — prevents flicker when 2 runners are
          equidistant or running in opposite directions.
        """
        hunter = next((p for p in self.mp_players
                       if p["role"] == "hunter" and p["alive"]), None)
        hunter_dist = None
        if hunter is not None:
            hunter_dist = bfs_distances(
                self.walls, hunter["r"], hunter["c"], self.gates, True
            )
        runner_positions = [(x["id"], x["r"], x["c"]) for x in self.mp_players
                            if x.get("role") == "runner"
                            and x["alive"]
                            and not x.get("imprisoned")
                            and not x.get("carried")]

        for p in self.mp_players:
            if not p.get("is_bot"):
                continue
            pid = p["id"]

            # Imprisoned / carried bots can't act — clear input so they don't
            # carry a stale dr/dc from earlier frames.
            if not p["alive"] or p.get("imprisoned") or p.get("carried") \
                    or p.get("stun_remaining", 0.0) > 0:
                self.mp_pending_input[pid] = {
                    "dr": 0, "dc": 0, "e_held": False, "skill_seq": 0
                }
                continue

            # Decrement state-commitment timer
            p["ai_timer"] = max(0.0, p.get("ai_timer", 0.0) - dt)
            p.setdefault("ai_recent", [])
            p.setdefault("ai_target_pos", None)
            p.setdefault("ai_last_pos", None)

            if pid not in self.mp_pending_input:
                self.mp_pending_input[pid] = {
                    "dr": 0, "dc": 0, "e_held": False, "skill_seq": 0
                }
            inp = self.mp_pending_input[pid]
            inp["dr"], inp["dc"], inp["e_held"] = 0, 0, False

            if p["role"] == "hunter":
                self._bot_hunter_tick(p, inp)
            else:
                self._bot_runner_tick(p, hunter, inp, hunter_dist, runner_positions)

    def _set_bot_step(self, p, inp, nr, nc):
        inp["dr"], inp["dc"] = nr - p["r"], nc - p["c"]
        p["ai_last_pos"] = [p["r"], p["c"]]
        recent = p.setdefault("ai_recent", [])
        recent.append([nr, nc])
        del recent[:-8]

    def _closed_gate_at(self, r, c):
        gate = gate_at(self.gates, r, c)
        if gate and not gate.is_open:
            return gate
        return None

    def _follow_bot_path(self, p, inp, path):
        if path is None:
            return False
        if not path:
            return True
        nr, nc = path[0]
        gate = self._closed_gate_at(nr, nc)
        if gate and gate.is_adjacent(p["r"], p["c"]):
            inp["e_held"] = True
            inp["dr"], inp["dc"] = 0, 0
            return True
        self._set_bot_step(p, inp, nr, nc)
        return True

    def _flee_cell_score(self, p, r, c, path_dist, hunter_dist,
                         current_hdist, runner_positions):
        hd = hunter_dist[r][c] if hunter_dist is not None else 999
        if hd < 0:
            hd = 999

        open_neighbors = 0
        for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            if not is_wall_for_path(self.walls, r + dr, c + dc, self.gates, True):
                open_neighbors += 1

        score = hd * 3 + path_dist * 0.35 + open_neighbors * 1.5
        if hd <= current_hdist:
            score -= 10
        if open_neighbors <= 1:
            score -= 7

        recent = {tuple(x) for x in p.get("ai_recent", [])}
        if (r, c) in recent:
            score -= 6
        if p.get("ai_last_pos") == [r, c]:
            score -= 9

        for rid, rr, rc in runner_positions:
            if rid == p["id"]:
                continue
            sep = abs(r - rr) + abs(c - rc)
            if sep == 0:
                score -= 14
            elif sep <= 4:
                score -= (5 - sep) * 2.5

        score += ((r * 17 + c * 31 + p["id"] * 13) % 7) * 0.01
        return score

    def _choose_runner_flee_target(self, p, hunter_dist, runner_positions):
        rows = len(self.walls)
        cols = len(self.walls[0]) if rows else 0
        pr, pc = p["r"], p["c"]
        current_hdist = 999
        if hunter_dist is not None:
            current_hdist = hunter_dist[pr][pc]
            if current_hdist < 0:
                current_hdist = 999

        q = deque([(pr, pc, 0, None)])
        visited = {(pr, pc)}
        best_cell, best_score = None, -10 ** 9
        last_pos = p.get("ai_last_pos")

        while q:
            r, c, path_dist, first_step = q.popleft()
            if path_dist > self.BOT_FLEE_TARGET_RADIUS:
                continue
            if path_dist >= 2:
                score = self._flee_cell_score(
                    p, r, c, path_dist, hunter_dist,
                    current_hdist, runner_positions
                )
                if first_step and last_pos == [first_step[0], first_step[1]]:
                    score -= 12
                if score > best_score:
                    best_cell, best_score = (r, c), score

            for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                nr, nc = r + dr, c + dc
                if (nr, nc) in visited:
                    continue
                if 0 <= nr < rows and 0 <= nc < cols \
                        and not is_wall_for_path(self.walls, nr, nc, self.gates, True):
                    visited.add((nr, nc))
                    q.append((nr, nc, path_dist + 1, first_step or (nr, nc)))

        return best_cell

    def _runner_flee_target_is_valid(self, p, hunter_dist):
        target = p.get("ai_target_pos")
        if not target or len(target) != 2:
            return False
        tr, tc = int(target[0]), int(target[1])
        if (tr, tc) == (p["r"], p["c"]):
            return False
        if is_wall_for_path(self.walls, tr, tc, self.gates, True):
            return False
        if hunter_dist is None:
            return True

        current_hdist = hunter_dist[p["r"]][p["c"]]
        target_hdist = hunter_dist[tr][tc]
        if current_hdist < 0:
            current_hdist = 999
        if target_hdist < 0:
            target_hdist = 999
        return target_hdist >= max(self.BOT_FLEE_TRIGGER_DIST + 1,
                                   current_hdist - 1)

    def _first_imprisoned_pod(self):
        for w in self.mp_freezing_pods:
            if w.imprisoned_pid is not None:
                return w
        return None

    def _best_rescue_side_safety(self, pod, hunter_dist):
        if hunter_dist is None:
            return 999
        best = -1
        for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nr, nc = pod.r + dr, pod.c + dc
            if is_wall_for_path(self.walls, nr, nc, self.gates, True):
                continue
            d = hunter_dist[nr][nc]
            if d < 0:
                d = 999
            best = max(best, d)
        return best

    def _rescue_path(self, p, pod, hunter_dist=None, require_safe=False):
        if pod.is_adjacent(p["r"], p["c"]):
            return []

        current_hdist = 999
        if hunter_dist is not None:
            current_hdist = hunter_dist[p["r"]][p["c"]]
            if current_hdist < 0:
                current_hdist = 999

        best_path, best_score = None, -10 ** 9
        for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nr, nc = pod.r + dr, pod.c + dc
            if is_wall_for_path(self.walls, nr, nc, self.gates, True):
                continue

            path = bfs(self.walls, p["r"], p["c"],
                       nr, nc, self.gates, True)
            if path is None:
                continue

            side_hdist = 999
            if hunter_dist is not None:
                side_hdist = hunter_dist[nr][nc]
                if side_hdist < 0:
                    side_hdist = 999
            if require_safe and side_hdist < max(
                    self.BOT_FLEE_TRIGGER_DIST + 1, current_hdist):
                continue

            score = side_hdist * 3 - len(path)
            if path and p.get("ai_last_pos") == [path[0][0], path[0][1]]:
                score -= 5
            if score > best_score:
                best_path, best_score = path, score
        return best_path

    def _assigned_rescuer_id(self, pod, hunter_dist):
        pod_safety = self._best_rescue_side_safety(pod, hunter_dist)
        best_id, best_score = None, 10 ** 9
        for candidate in self.mp_players:
            if not candidate.get("is_bot"):
                continue
            if candidate.get("role") != "runner" or not candidate["alive"]:
                continue
            if candidate.get("imprisoned") or candidate.get("carried"):
                continue

            path = self._rescue_path(candidate, pod, hunter_dist)
            if path is None:
                continue

            already_adjacent = pod.is_adjacent(candidate["r"], candidate["c"])
            if pod_safety < self.BOT_RESCUE_DANGER_DIST and not already_adjacent:
                continue

            score = len(path)
            if already_adjacent:
                score -= 20
            if candidate.get("ai_state") == "flee":
                score += 4
            if hunter_dist is not None:
                cd = hunter_dist[candidate["r"]][candidate["c"]]
                if cd >= 0 and cd <= self.BOT_FLEE_TRIGGER_DIST and not already_adjacent:
                    score += 6
                score += max(0, self.BOT_RESCUE_DANGER_DIST - pod_safety) * 3

            if best_id is None or score < best_score \
                    or (score == best_score and candidate["id"] < best_id):
                best_id, best_score = candidate["id"], score
        return best_id

    def _bot_hunter_tick(self, p, inp):
        """Hunter bot: BFS-chase the committed target runner.

        The hunter keeps the same target for `BOT_HUNTER_COMMIT_SEC` seconds.
        Only when that timer expires (or the target dies / is imprisoned /
        gets carried by another hunter — irrelevant in DBD-MAZE) does it
        repick the nearest fresh runner.
        """
        if p.get("carrying_pid") is not None:
            best_path = None
            for w in self.mp_freezing_pods:
                if w.imprisoned_pid is not None:
                    continue
                path = bfs_adjacent(
                    self.walls, p["r"], p["c"], w.r, w.c, self.gates, True
                )
                if path is not None and (best_path is None or len(path) < len(best_path)):
                    best_path = path
            p["ai_state"] = "carry"
            p["ai_target_id"] = None
            p["ai_target_pos"] = None
            p["ai_timer"] = 0.0
            self._follow_bot_path(p, inp, best_path)
            return

        # Resolve current target if commitment is still alive
        cur_target = None
        if p.get("ai_target_id") is not None and p.get("ai_timer", 0.0) > 0:
            cur_target = next((x for x in self.mp_players
                               if x["id"] == p["ai_target_id"]
                               and x["alive"]
                               and not x.get("imprisoned")
                               and not x.get("carried")), None)

        path = None
        if cur_target is not None:
            p["ai_target_pos"] = [cur_target["r"], cur_target["c"]]
            path = bfs(
                self.walls, p["r"], p["c"],
                cur_target["r"], cur_target["c"], self.gates, True
            )

        if cur_target is None or path is None:
            candidates = [x for x in self.mp_players
                          if x.get("role") == "runner"
                          and x["alive"]
                          and not x.get("imprisoned")
                          and not x.get("carried")]
            best_target, best_path = None, None
            for x in candidates:
                candidate_path = bfs(
                    self.walls, p["r"], p["c"], x["r"], x["c"], self.gates, True
                )
                if candidate_path is not None and (best_path is None or len(candidate_path) < len(best_path)):
                    best_target, best_path = x, candidate_path
            if best_target is None:
                p["ai_target_id"] = None
                return
            cur_target, path = best_target, best_path
            p["ai_target_id"] = cur_target["id"]
            p["ai_target_pos"] = [cur_target["r"], cur_target["c"]]
            p["ai_timer"]     = self.BOT_HUNTER_COMMIT_SEC

        self._follow_bot_path(p, inp, path)

    def _bot_runner_tick(self, p, hunter, inp, hunter_dist, runner_positions):
        """Runner bot with FLEE-commit hysteresis.

        Decision order:
          1. FLEE state — committed for at least BOT_FLEE_COMMIT_SEC; exit
             only when hunter is BOT_FLEE_SAFE_DIST away.
          2. Help carried teammates by following the hunter (rescue chance).
          3. Rescue imprisoned teammates from freezing pods.
          4. Repair the nearest unfinished generator.
          5. Head for the exit if it's unlocked.
        """
        hdist = 999
        hunter_seen = bool(hunter and hunter["alive"])
        if hunter_seen and hunter_dist is not None:
            hdist = hunter_dist[p["r"]][p["c"]]
            if hdist < 0:
                hdist = 999

        state = p.get("ai_state", "idle")
        target_pod = self._first_imprisoned_pod()
        can_rescue_now = bool(
            target_pod is not None and target_pod.is_adjacent(p["r"], p["c"])
        )
        if can_rescue_now:
            p["ai_state"] = "rescue"
            p["ai_target_pos"] = [target_pod.r, target_pod.c]
            inp["e_held"] = True
            inp["dr"], inp["dc"] = 0, 0
            return

        assigned_to_rescue = target_pod is not None \
            and self._assigned_rescuer_id(target_pod, hunter_dist) == p["id"]
        rescue_under_pressure = hunter_seen \
            and hdist <= self.BOT_FLEE_TRIGGER_DIST \
            and not can_rescue_now

        if assigned_to_rescue:
            rescue_path = self._rescue_path(
                p, target_pod, hunter_dist, rescue_under_pressure
            )
            if rescue_path is None and not rescue_under_pressure:
                rescue_path = bfs_adjacent(
                    self.walls, p["r"], p["c"],
                    target_pod.r, target_pod.c, self.gates, True
                )

        if assigned_to_rescue and (not rescue_under_pressure or rescue_path is not None):
            p["ai_state"] = "rescue"
            p["ai_target_pos"] = [target_pod.r, target_pod.c]
            if self._follow_bot_path(p, inp, rescue_path):
                return

        # ── 1. FLEE state transitions (hysteresis) ────────────────────────────
        if state != "flee" \
                and hunter_seen \
                and hdist <= self.BOT_FLEE_TRIGGER_DIST \
                and not (hunter and hunter.get("carrying_pid")):
            # Hunter is close and not busy carrying someone — commit to flee.
            p["ai_state"] = "flee"
            p["ai_timer"] = self.BOT_FLEE_COMMIT_SEC
            p["ai_target_pos"] = None
            state = "flee"
        elif state == "flee" and p["ai_timer"] <= 0:
            if hdist >= self.BOT_FLEE_SAFE_DIST:
                p["ai_state"] = "idle"
                p["ai_target_pos"] = None
                state = "idle"
            else:
                # Still not safe — refresh the commitment instead of bouncing
                # back to repair only to immediately re-flee next frame.
                p["ai_timer"] = self.BOT_FLEE_COMMIT_SEC / 2
        elif state != "flee":
            p["ai_target_pos"] = None

        # ── 2. Execute FLEE ──────────────────────────────────────────────────
        if state == "flee":
            if hunter_seen or p.get("ai_target_pos"):
                if not self._runner_flee_target_is_valid(p, hunter_dist):
                    target = None
                    if hunter_seen:
                        target = self._choose_runner_flee_target(
                            p, hunter_dist, runner_positions
                        )
                    p["ai_target_pos"] = [target[0], target[1]] if target else None

                target = p.get("ai_target_pos")
                path = None
                if target:
                    path = bfs(self.walls, p["r"], p["c"],
                               int(target[0]), int(target[1]), self.gates, True)
                    last_pos = p.get("ai_last_pos")
                    if path and last_pos == [path[0][0], path[0][1]]:
                        target = None
                        if hunter_seen:
                            target = self._choose_runner_flee_target(
                                p, hunter_dist, runner_positions
                            )
                        p["ai_target_pos"] = [target[0], target[1]] if target else None
                        if target:
                            path = bfs(self.walls, p["r"], p["c"],
                                       target[0], target[1], self.gates, True)
                            if path and last_pos == [path[0][0], path[0][1]]:
                                path = None

                if self._follow_bot_path(p, inp, path):
                    return

                best_score = -10 ** 9
                best_cell = None
                for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                    nr, nc = p["r"] + dr, p["c"] + dc
                    if is_wall_for_path(self.walls, nr, nc, self.gates, True):
                        continue
                    score = self._flee_cell_score(
                        p, nr, nc, 1, hunter_dist, hdist, runner_positions
                    )
                    if score > best_score:
                        best_score = score
                        best_cell = (nr, nc)
                if best_cell:
                    self._follow_bot_path(p, inp, [best_cell])
            return

        # ── 3. Help carried teammate (shadow the hunter to ambush a rescue) ───
        if hunter_seen and hunter and hunter.get("carrying_pid") and hdist < 15:
            if hdist > 2:
                path = bfs(self.walls, p["r"], p["c"],
                           hunter["r"], hunter["c"], self.gates, True)
                self._follow_bot_path(p, inp, path)
            for w in self.mp_freezing_pods:
                if w.imprisoned_pid is not None and w.is_adjacent(p["r"], p["c"]):
                    inp["e_held"] = True
                    inp["dr"], inp["dc"] = 0, 0
            return

        # ── 4. Rescue imprisoned teammates ───────────────────────────────────
        # ── 5. Repair the closest unfinished generator ───────────────────────
        if not self.exit_unlocked:
            best_gen, best_path = None, None
            for gen in self.mp_generators:
                if gen.completed:
                    continue
                path = bfs_adjacent(self.walls, p["r"], p["c"],
                                    gen.r, gen.c, self.gates, True)
                if path is not None and (best_path is None or len(path) < len(best_path)):
                    best_path = path
                    best_gen  = gen
            if best_gen:
                if best_gen.is_adjacent(p["r"], p["c"]):
                    inp["e_held"] = True
                elif best_path:
                    self._follow_bot_path(p, inp, best_path)
            return

        # ── 6. Exit unlocked — head to the exit ──────────────────────────────
        path = bfs(self.walls, p["r"], p["c"],
                   self.exit_pos[0], self.exit_pos[1], self.gates, True)
        if path:
            nr, nc = path[0]
            gate_in_way = next((g for g in self.gates
                                if g.r == nr and g.c == nc), None)
            if gate_in_way and not gate_in_way.is_open:
                if gate_in_way.is_adjacent(p["r"], p["c"]):
                    inp["e_held"] = True
            else:
                self._follow_bot_path(p, inp, path)

    def _consume_skill_check_presses(self):
        pressed = set()
        for p in self.mp_players:
            pid = p["id"]
            inp = self.mp_pending_input.get(pid, {})
            seq = int(inp.get("skill_seq", 0))
            if pid not in self.mp_skill_seen:
                self.mp_skill_seen[pid] = seq
            elif seq != self.mp_skill_seen[pid]:
                self.mp_skill_seen[pid] = seq
                pressed.add(pid)
        return pressed

    def _start_generator_skill_check(self, gen, repairers):
        humans = [p for p in repairers if not p.get("is_bot")]
        owner = random.choice(humans or repairers)
        gen.skill_active = True
        gen.skill_owner_id = owner["id"]
        gen.skill_elapsed = 0.0
        gen.skill_duration = GEN_SKILL_DURATION
        gen.skill_target_width = GEN_SKILL_ZONE_WIDTH
        gen.skill_target_start = random.uniform(
            0.52, 0.96 - gen.skill_target_width
        )
        gen.skill_participants = [p["id"] for p in repairers]
        gen.skill_bot_will_succeed = (
            random.random() < GEN_BOT_SKILL_SUCCESS_RATE
        )

    def _finish_generator_skill_check(self, gen, success):
        if not success:
            gen.progress = max(
                0.0, gen.progress - GEN_SKILL_FAIL_PENALTY
            )
            participant_ids = set(gen.skill_participants)
            for p in self.mp_players:
                if p["id"] not in participant_ids:
                    continue
                if p.get("role") != "runner" or not p["alive"]:
                    continue
                p["stun_remaining"] = max(
                    p.get("stun_remaining", 0.0),
                    GEN_SKILL_STUN_TIME,
                )
                inp = self.mp_pending_input.get(p["id"])
                if inp is not None:
                    inp["dr"], inp["dc"], inp["e_held"] = 0, 0, False

        gen.skill_active = False
        gen.skill_owner_id = None
        gen.skill_elapsed = 0.0
        gen.skill_participants = []
        gen.skill_cooldown = random.uniform(
            GEN_SKILL_DELAY_MIN, GEN_SKILL_DELAY_MAX
        )

    def _dbd_tick(self, dt):
        """Generators, catches, imprisonment, rescues, win conditions."""
        # match timer
        self.mp_match_timer -= dt
        if self.mp_match_timer <= 0:
            self.mp_match_timer = 0
            self._end_dbd("hunter")
            return

        skill_presses = self._consume_skill_check_presses()

        # Generators: repair speed stacks; one shared skill check can interrupt it.
        for gen in self.mp_generators:
            if gen.completed:
                gen.skill_active = False
                continue

            repairers = []
            for p in self.mp_players:
                if p["role"] != "runner" or not p["alive"] \
                        or p.get("imprisoned") or p.get("carried") \
                        or p.get("stun_remaining", 0.0) > 0:
                    continue
                inp = self.mp_pending_input.get(p["id"], {})
                if gen.is_adjacent(p["r"], p["c"]) and inp.get("e_held"):
                    repairers.append(p)

            failed = False
            if gen.skill_active:
                current_ids = {p["id"] for p in repairers}
                gen.skill_participants = sorted(
                    set(gen.skill_participants) | current_ids
                )
                owner = next(
                    (p for p in self.mp_players
                     if p["id"] == gen.skill_owner_id),
                    None,
                )
                gen.skill_elapsed += dt
                needle = min(
                    1.0, gen.skill_elapsed / max(0.01, gen.skill_duration)
                )
                target_end = (
                    gen.skill_target_start + gen.skill_target_width
                )

                if owner is None or owner["id"] not in current_ids:
                    self._finish_generator_skill_check(gen, False)
                    failed = True
                elif owner.get("is_bot"):
                    target_mid = (
                        gen.skill_target_start + gen.skill_target_width / 2
                    )
                    if gen.skill_bot_will_succeed and needle >= target_mid:
                        self._finish_generator_skill_check(gen, True)
                    elif needle >= 1.0:
                        self._finish_generator_skill_check(gen, False)
                        failed = True
                elif owner["id"] in skill_presses:
                    success = gen.skill_target_start <= needle <= target_end
                    self._finish_generator_skill_check(gen, success)
                    failed = not success
                elif needle >= 1.0:
                    self._finish_generator_skill_check(gen, False)
                    failed = True

            if failed:
                continue

            if repairers:
                gen.progress += dt * len(repairers)
                if gen.progress >= GEN_REPAIR_TIME:
                    gen.progress = GEN_REPAIR_TIME
                    gen.completed = True
                    gen.skill_active = False
                elif not gen.skill_active:
                    gen.skill_cooldown -= dt
                    if gen.skill_cooldown <= 0:
                        self._start_generator_skill_check(gen, repairers)

        # all gens done → unlock exit
        if not self.exit_unlocked and all(g.completed for g in self.mp_generators):
            self.exit_unlocked = True

        # hunter catches: hunter shares cell with a runner
        hunter = next((p for p in self.mp_players if p["role"] == "hunter"), None)
        if hunter and hunter["alive"]:
            if hunter.get("catch_cooldown", 0) > 0:
                hunter["catch_cooldown"] -= dt
            elif hunter.get("carrying_pid") is None:
                old_positions = getattr(self, "mp_prev_positions", {})
                hunter_old = old_positions.get(
                    hunter["id"], (hunter["r"], hunter["c"])
                )
                for p in self.mp_players:
                    if p["role"] != "runner" or not p["alive"] or p.get("imprisoned") or p.get("carried"):
                        continue
                    runner_old = old_positions.get(p["id"], (p["r"], p["c"]))
                    same_cell = (p["r"], p["c"]) == (hunter["r"], hunter["c"])
                    crossed = runner_old == (hunter["r"], hunter["c"]) \
                        and hunter_old == (p["r"], p["c"])
                    if same_cell or crossed:
                        hunter["carrying_pid"] = p["id"]
                        p["carried"] = True
                        p["carry_timer"] = HUNTER_CARRY_TIME
                        p["ai_state"] = "idle"
                        p["ai_target_pos"] = None
                        break
            else:
                carried_runner = next((p for p in self.mp_players if p["id"] == hunter["carrying_pid"]), None)
                if carried_runner:
                    carried_runner["carry_timer"] -= dt
                    carried_runner["r"] = hunter["r"]
                    carried_runner["c"] = hunter["c"]

                    put_in_wh = None
                    for w in self.mp_freezing_pods:
                        if w.imprisoned_pid is None and w.is_adjacent(hunter["r"], hunter["c"]):
                            put_in_wh = w
                            break

                    if put_in_wh:
                        carried_runner["carried"] = False
                        hunter["carrying_pid"] = None
                        self._imprison_runner_in(carried_runner, put_in_wh)
                    elif carried_runner["carry_timer"] <= 0:
                        carried_runner["carried"] = False
                        hunter["carrying_pid"] = None
                        hunter["catch_cooldown"] = 2.0
                        for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                            nr, nc = hunter["r"] + dr, hunter["c"] + dc
                            if not is_wall(self.walls, nr, nc, self.gates):
                                carried_runner["r"], carried_runner["c"] = nr, nc
                                break
                else:
                    hunter["carrying_pid"] = None

        # rescue: any free runner adjacent to a freezing_pod holding an imprisoned one
        for w in self.mp_freezing_pods:
            if w.imprisoned_pid is None:
                continue
            for p in self.mp_players:
                if p["role"] != "runner" or not p["alive"] or p.get("imprisoned") or p.get("carried"):
                    continue
                if p.get("stun_remaining", 0.0) > 0:
                    continue
                if w.is_adjacent(p["r"], p["c"]):
                    inp = self.mp_pending_input.get(p["id"], {})
                    if inp.get("e_held"):
                        self._free_runner(w)
                        break

        # imprisonment timers
        for p in self.mp_players:
            if p.get("imprisoned"):
                p["imprison_remaining"] -= dt
                if p["imprison_remaining"] <= 0:
                    # timer ran out → eliminated
                    p["alive"] = False
                    p["imprisoned"] = False
                    for w in self.mp_freezing_pods:
                        if w.imprisoned_pid == p["id"]:
                            w.imprisoned_pid = None

        # win check: all runners eliminated → hunter wins
        runners = [p for p in self.mp_players if p["role"] == "runner"]
        if runners and all(not p["alive"] for p in runners):
            self._end_dbd("hunter")
            return

    def _imprison_runner_in(self, runner, w):
        runner["r"], runner["c"] = w.r, w.c
        runner["imprisoned"] = True
        runner["imprison_remaining"] = FREEZING_POD_IMPRISON
        w.imprisoned_pid = runner["id"]

    def _imprison_runner(self, runner):
        # find a freezing_pod without an imprisoned player
        free_wh = [w for w in self.mp_freezing_pods if w.imprisoned_pid is None]
        if not free_wh:
            # all freezing_pods occupied — just kill them (edge case)
            runner["alive"] = False
            return
        w = random.choice(free_wh)
        runner["r"], runner["c"] = w.r, w.c
        runner["imprisoned"] = True
        runner["imprison_remaining"] = FREEZING_POD_IMPRISON
        w.imprisoned_pid = runner["id"]

    def _free_runner(self, w):
        for p in self.mp_players:
            if p["id"] == w.imprisoned_pid:
                p["imprisoned"] = False
                p["imprison_remaining"] = 0.0
                # bump them to an adjacent free cell so they aren't stuck on the freezing_pod
                for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
                    nr, nc = w.r + dr, w.c + dc
                    if not is_wall(self.walls, nr, nc, self.gates):
                        p["r"], p["c"] = nr, nc
                        break
                break
        w.imprisoned_pid = None

    def _end_dbd(self, winner):
        self.mp_winner = winner
        self.state = "mp_end"
        # final state push so clients see correct positions / timer
        if self.server is not None:
            self.server.broadcast(self._serialize_state())
            self.server.broadcast({"type": "end", "winner": winner})

    def _nearest_alive_to(self, r, c):
        best = None
        bestd = 10 ** 9
        for p in self.mp_players:
            if not p["alive"]:
                continue
            d = abs(p["r"] - r) + abs(p["c"] - c)
            if d < bestd:
                bestd, best = d, p
        return best

    def host_advance_level(self):
        """Host-side: build next level, reset positions, broadcast new maze."""
        self.new_level()
        # re-apply ESCAPE settings (new_level rebuilds defaults)
        if not self.mp_settings.get("portals", True):
            self.portals = []
        if self.mp_settings.get("hunter", "bot") == "off":
            self.hunter = None
        spawns = [(1, 1), (1, 3), (3, 1), (3, 3)]
        for i, p in enumerate(self.mp_players):
            r, c = spawns[i] if i < len(spawns) else (1, 1)
            r, c = nearest_free(self.walls, r, c, self.gates)
            p["r"], p["c"] = r, c
            p["alive"] = True
        self.server.broadcast(self._serialize_maze())

    def host_shift_maze(self):
        """Maze shift in MP: regenerate, snap all players, broadcast new maze."""
        self.walls = build_maze(ROWS, COLS)
        self.gates = []
        self.build_gates()
        self.portals = []
        if self.mp_settings.get("portals", True):
            self.build_portals()

        for p in self.mp_players:
            if p["alive"]:
                p["r"], p["c"] = nearest_free(self.walls, p["r"], p["c"], self.gates)
        if self.hunter is not None:
            self.hunter.r, self.hunter.c = nearest_free(
                self.walls, self.hunter.r, self.hunter.c, self.gates
            )
        self.server.broadcast(self._serialize_maze())

    # ── client: per-frame tick ────────────────────────────────────────────────
    def mp_client_tick(self, dt):
        """Drain server messages; send our input throttled."""
        for msg in self.client.drain():
            t = msg.get("type")
            if t == "welcome":
                self.player_id = msg.get("id", 0)
            elif t == "maze":
                self._apply_maze(msg)
            elif t == "state":
                self._apply_state(msg)
            elif t == "start":
                self.mp_mode   = msg.get("mode", "escape")
                self.mp_winner = ""
                if self.state in ("lobby_wait_client", "mp_end"):
                    self.state = "mp_play"
            elif t == "end":
                self.mp_winner = msg.get("winner", "")
                self.state = "mp_end"

        if not self.client.alive:
            self.mp_status_msg = "Lost connection to host."
            self.mp_disconnect()
            return

        # send input ~30Hz
        self.mp_send_timer += dt
        if self.mp_send_timer >= 1.0 / 30.0:
            self.mp_send_timer = 0.0
            self.client.send({"type": "input", **self.mp_local_input})

    # ── multiplayer render ────────────────────────────────────────────────────
    def draw_mp_play(self):
        self.draw_maze()

        for g in self.gates:
            g.draw(self.surf, self.walls)

        # DBD entities
        for gen in self.mp_generators:
            gen.draw(self.surf)
        for w in self.mp_freezing_pods:
            w.draw(self.surf)

        # portals (escape mode only)
        if self.mp_mode == "escape":
            t = pygame.time.get_ticks() / 1000
            for p in self.portals:
                p.draw(self.surf, t)

        # exit (X-overlay if locked)
        self.draw_exit()
        if not self.exit_unlocked:
            ex, ey = cell_xy(*self.exit_pos)
            pygame.draw.line(self.surf, (255, 80, 80),
                             (ex - CELL // 3, ey - CELL // 3),
                             (ex + CELL // 3, ey + CELL // 3), 3)
            pygame.draw.line(self.surf, (255, 80, 80),
                             (ex - CELL // 3, ey + CELL // 3),
                             (ex + CELL // 3, ey - CELL // 3), 3)

        # players
        for p in self.mp_players:
            if not p["alive"]:
                continue
            # any player with role=hunter (DBD or ESCAPE-with-player-hunter) is red
            if p.get("role") == "hunter":
                color = (255, 60, 60)
            else:
                color = PLAYER_COLORS[p["id"] % len(PLAYER_COLORS)]
            x, y   = cell_xy(p["r"], p["c"])
            radius = int(CELL * 0.30)

            # imprisoned or carried runners: draw smaller + a circle outline
            if p.get("imprisoned") or p.get("carried"):
                pygame.draw.circle(self.surf, color, (x, y), radius // 2)
                if p.get("imprisoned"):
                    pygame.draw.circle(self.surf, (200, 200, 200), (x, y), radius, 2)
                    frac = max(0.0, p.get("imprison_remaining", 0)) / FREEZING_POD_IMPRISON
                    arc_color = (255, 80, 80)
                else:
                    pygame.draw.circle(self.surf, (255, 220, 0), (x, y), radius, 2)
                    frac = max(0.0, p.get("carry_timer", 0)) / HUNTER_CARRY_TIME
                    arc_color = (255, 220, 0)

                # remaining-time arc
                arc_rect = pygame.Rect(x - radius - 4, y - radius - 4,
                                       (radius + 4) * 2, (radius + 4) * 2)
                pygame.draw.arc(self.surf, arc_color, arc_rect,
                                -math.pi / 2,
                                -math.pi / 2 + math.tau * frac, 3)
            else:
                glow = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
                pygame.draw.circle(glow, (*color, 60),
                                   (radius * 2, radius * 2), radius * 2)
                self.surf.blit(glow, (x - radius * 2, y - radius * 2))
                pygame.draw.circle(self.surf, color, (x, y), radius)

            # own avatar: white outline
            if p["id"] == self.player_id:
                pygame.draw.circle(self.surf, WHITE, (x, y), radius + 2, 2)
            if p.get("stun_remaining", 0.0) > 0:
                pygame.draw.circle(
                    self.surf, (255, 220, 0), (x, y), radius + 5, 3
                )

        # bot hunter (escape mode only)
        if self.mp_mode == "escape" and self.hunter is not None:
            self.hunter.draw(self.surf)

        self.draw_mp_panel()
        self.draw_skill_check()

    def draw_skill_check(self):
        if self.mp_mode != "dbd":
            return

        me = next(
            (p for p in self.mp_players if p["id"] == self.player_id),
            None,
        )
        if me is None:
            return

        if me.get("stun_remaining", 0.0) > 0:
            text = self.font_med.render(
                f"STUNNED  {me['stun_remaining']:.1f}s",
                True,
                (255, 220, 0),
            )
            x = PANEL_X // 2 - text.get_width() // 2
            self.surf.blit(text, (x, 32))

        gen = next(
            (
                g for g in self.mp_generators
                if g.skill_active and g.skill_owner_id == self.player_id
            ),
            None,
        )
        if gen is None:
            return

        panel_w = min(640, PANEL_X - 80)
        panel_h = 120
        panel_x = PANEL_X // 2 - panel_w // 2
        panel_y = H - panel_h - 42
        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 225))
        self.surf.blit(panel, (panel_x, panel_y))
        pygame.draw.rect(
            self.surf, (255, 220, 0),
            (panel_x, panel_y, panel_w, panel_h), 2,
        )

        label = self.font_med.render(
            "SKILL CHECK  -  PRESS SPACE", True, WHITE
        )
        self.surf.blit(
            label,
            (panel_x + panel_w // 2 - label.get_width() // 2, panel_y + 12),
        )

        bar_x = panel_x + 38
        bar_y = panel_y + 72
        bar_w = panel_w - 76
        bar_h = 22
        pygame.draw.rect(
            self.surf, (45, 45, 55),
            (bar_x, bar_y, bar_w, bar_h),
        )

        target_x = bar_x + int(bar_w * gen.skill_target_start)
        target_w = max(4, int(bar_w * gen.skill_target_width))
        pygame.draw.rect(
            self.surf, (80, 220, 100),
            (target_x, bar_y, target_w, bar_h),
        )

        needle = min(
            1.0, gen.skill_elapsed / max(0.01, gen.skill_duration)
        )
        needle_x = bar_x + int(bar_w * needle)
        pygame.draw.line(
            self.surf, WHITE,
            (needle_x, bar_y - 8),
            (needle_x, bar_y + bar_h + 8),
            4,
        )
        pygame.draw.rect(
            self.surf, WHITE,
            (bar_x, bar_y, bar_w, bar_h), 2,
        )

    def draw_mp_panel(self):
        pygame.draw.rect(self.surf, PANEL_BG, (PANEL_X, 0, PANEL_W, H))
        pygame.draw.line(self.surf, (0, 120, 200), (PANEL_X, 0), (PANEL_X, H), 3)

        mode_label = {
            "escape": "ESCAPE",
            "dbd":    "DBD-MAZE",
        }.get(self.mp_mode, self.mp_mode.upper())
        role = "HOST" if self.server is not None else "CLIENT"
        title = self.font_lg.render(mode_label, True, CYAN)
        self.surf.blit(title, (PANEL_X + PANEL_W // 2 - title.get_width() // 2, 18))

        if self.mp_mode == "escape":
            sub = self.font_sm.render(f"{role}  -  LEVEL {self.level}",
                                      True, (160, 160, 160))
            self.surf.blit(sub, (PANEL_X + PANEL_W // 2 - sub.get_width() // 2, 70))
        else:
            mins = max(0, int(self.mp_match_timer)) // 60
            secs = max(0, int(self.mp_match_timer)) % 60
            time_txt = self.font_lg.render(f"{mins:01d}:{secs:02d}",
                                           True, (255, 220, 80))
            self.surf.blit(time_txt,
                           (PANEL_X + PANEL_W // 2 - time_txt.get_width() // 2, 60))
            done = sum(1 for g in self.mp_generators if g.completed)
            gen_txt = self.font_med.render(
                f"GENERATORS  {done} / {len(self.mp_generators)}",
                True, WHITE)
            self.surf.blit(gen_txt,
                           (PANEL_X + PANEL_W // 2 - gen_txt.get_width() // 2, 110))
            status = "EXIT UNLOCKED" if self.exit_unlocked else "EXIT LOCKED"
            scol = (80, 255, 80) if self.exit_unlocked else (255, 80, 80)
            stxt = self.font_sm.render(status, True, scol)
            self.surf.blit(stxt,
                           (PANEL_X + PANEL_W // 2 - stxt.get_width() // 2, 152))

        # player list
        y0 = 200
        head = self.font_med.render("PLAYERS", True, WHITE)
        self.surf.blit(head, (PANEL_X + 24, y0))

        for i, p in enumerate(self.mp_players):
            if p.get("role") == "hunter":
                color    = (255, 60, 60)
                role_tag = " [H]"
            else:
                color    = PLAYER_COLORS[p["id"] % len(PLAYER_COLORS)]
                role_tag = ""
            label = f"P{p['id'] + 1}{role_tag}" + \
                    ("  (you)" if p["id"] == self.player_id else "")
            if not p["alive"]:
                status = "ELIMINATED"
            elif p.get("imprisoned"):
                status = f"IMPRISONED {p['imprison_remaining']:.0f}s"
            elif p.get("carried"):
                status = f"CARRIED {p['carry_timer']:.1f}s"
            elif p.get("stun_remaining", 0.0) > 0:
                status = f"STUNNED {p['stun_remaining']:.1f}s"
            else:
                status = "ALIVE"
            text = self.font_sm.render(f"{label}  -  {status}", True, color)
            pygame.draw.rect(self.surf, color, (PANEL_X + 24, y0 + 50 + i * 36, 14, 14))
            self.surf.blit(text, (PANEL_X + 46, y0 + 46 + i * 36))

        hint_y = H - 220
        if self.mp_mode == "escape":
            hints = [
                "WASD / ARROWS : MOVE",
                "HOLD E : open gate",
                "ESC : leave match",
            ]
        else:
            hints = [
                "WASD / ARROWS : MOVE",
                "HOLD E : repair / rescue / gate",
                "SPACE : skill check",
                "ESC : leave match",
            ]
        for i, hint in enumerate(hints):
            txt = self.font_sm.render(hint, True, (160, 160, 160))
            self.surf.blit(txt, (PANEL_X + 24, hint_y + i * 28))

    def draw_mp_end(self):
        # render the maze + entities frozen behind the overlay
        self.draw_mp_play()

        if self.mp_winner == "runners":
            title  = "RUNNERS WIN"
            color  = (80, 255, 80)
            reason = "A runner reached the exit."
        elif self.mp_winner == "hunter":
            title  = "HUNTER WINS"
            color  = (255, 80, 80)
            reason = "Time ran out or all runners eliminated."
        else:
            title  = "MATCH OVER"
            color  = WHITE
            reason = ""

        # overlay box (bigger than the generic draw_overlay because we need buttons)
        ow, oh = 760, 380
        ox = MAZE_OX + MAZE_W // 2 - ow // 2
        oy = MAZE_OY + MAZE_H // 2 - oh // 2
        panel = pygame.Surface((ow, oh), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 220))
        self.surf.blit(panel, (ox, oy))
        pygame.draw.rect(self.surf, color, (ox, oy, ow, oh), 3)

        ts = self.font_xl.render(title, True, color)
        self.surf.blit(ts, (ox + ow // 2 - ts.get_width() // 2, oy + 36))

        if reason:
            sub = self.font_med.render(reason, True, WHITE)
            self.surf.blit(sub, (ox + ow // 2 - sub.get_width() // 2, oy + 140))

        # buttons
        self.mp_end_rects = {}
        btn_w, btn_h = 240, 64
        btn_y = oy + oh - btn_h - 50

        if self.server is not None:
            # host: REPLAY + MENU side by side
            gap = 30
            bx_replay = ox + ow // 2 - btn_w - gap // 2
            bx_menu   = ox + ow // 2 + gap // 2

            replay_rect = pygame.Rect(bx_replay, btn_y, btn_w, btn_h)
            pygame.draw.rect(self.surf, (0, 80, 40), replay_rect, border_radius=6)
            pygame.draw.rect(self.surf, (80, 255, 80), replay_rect, 2, border_radius=6)
            rt = self.font_med.render("REPLAY", True, WHITE)
            self.surf.blit(rt, (replay_rect.centerx - rt.get_width() // 2,
                                replay_rect.centery - rt.get_height() // 2))
            self.mp_end_rects["replay"] = replay_rect

            menu_rect = pygame.Rect(bx_menu, btn_y, btn_w, btn_h)
            pygame.draw.rect(self.surf, (40, 40, 60), menu_rect, border_radius=6)
            pygame.draw.rect(self.surf, WHITE, menu_rect, 2, border_radius=6)
            mt = self.font_med.render("MENU", True, WHITE)
            self.surf.blit(mt, (menu_rect.centerx - mt.get_width() // 2,
                                menu_rect.centery - mt.get_height() // 2))
            self.mp_end_rects["menu"] = menu_rect

            hint = self.font_sm.render(
                "ENTER = replay  -  ESC = menu", True, (160, 160, 160))
            self.surf.blit(hint,
                           (ox + ow // 2 - hint.get_width() // 2, btn_y + btn_h + 10))
        else:
            # client: only MENU; show wait line
            wait = self.font_sm.render(
                "Waiting for host... press REPLAY to play again.",
                True, (180, 180, 180))
            self.surf.blit(wait,
                           (ox + ow // 2 - wait.get_width() // 2, btn_y - 28))

            menu_rect = pygame.Rect(ox + ow // 2 - btn_w // 2, btn_y, btn_w, btn_h)
            pygame.draw.rect(self.surf, (40, 40, 60), menu_rect, border_radius=6)
            pygame.draw.rect(self.surf, WHITE, menu_rect, 2, border_radius=6)
            mt = self.font_med.render("MENU", True, WHITE)
            self.surf.blit(mt, (menu_rect.centerx - mt.get_width() // 2,
                                menu_rect.centery - mt.get_height() // 2))
            self.mp_end_rects["menu"] = menu_rect

            hint = self.font_sm.render("ESC = menu", True, (160, 160, 160))
            self.surf.blit(hint,
                           (ox + ow // 2 - hint.get_width() // 2, btn_y + btn_h + 10))

    def host_replay(self):
        """Host restarts the match with current mode + settings."""
        if self.server is None:
            return
        self.host_start_match()

    # ── main loop ─────────────────────────────────────────────────────────────
    def run(self):
        move_keys = {
            pygame.K_UP:    (-1,  0),
            pygame.K_w:     (-1,  0),
            pygame.K_DOWN:  ( 1,  0),
            pygame.K_s:     ( 1,  0),
            pygame.K_LEFT:  ( 0, -1),
            pygame.K_a:     ( 0, -1),
            pygame.K_RIGHT: ( 0,  1),
            pygame.K_d:     ( 0,  1),
        }

        while True:
            dt = self.clock.tick(FPS) / 1000.0

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()

                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        if self.state == "menu":
                            pygame.quit()
                            sys.exit()
                        elif self.state in ("lobby_wait_host", "lobby_wait_client",
                                            "mp_play", "mp_end"):
                            self.mp_disconnect()
                        elif self.state == "lobby_join_input":
                            self.state = "lobby"
                        elif self.state == "lobby_mode_pick":
                            self.state = "lobby"
                        elif self.state == "lobby":
                            self.state = "menu"
                        else:
                            self.state = "menu"

                    elif self.state == "mp_play" and event.key == pygame.K_SPACE:
                        self.mp_skill_seq += 1

                    elif self.state == "menu":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.menu_index = (self.menu_index - 1) % len(self.menu_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.menu_index = (self.menu_index + 1) % len(self.menu_options)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.select_menu_option()

                    elif self.state == "lobby":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.lobby_index = (self.lobby_index - 1) % len(self.lobby_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.lobby_index = (self.lobby_index + 1) % len(self.lobby_options)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.select_lobby_option()

                    elif self.state == "lobby_mode_pick":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.mode_index = (self.mode_index - 1) % len(self.mode_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.mode_index = (self.mode_index + 1) % len(self.mode_options)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.select_mode_option()

                    elif self.state == "lobby_join_input":
                        if event.key == pygame.K_RETURN:
                            if self.mp_text_input.strip():
                                self.start_client_mode(self.mp_text_input)
                        elif event.key == pygame.K_BACKSPACE:
                            self.mp_text_input = self.mp_text_input[:-1]
                        elif event.key == pygame.K_v and (pygame.key.get_mods() & pygame.KMOD_CTRL):
                            try:
                                if not pygame.scrap.get_init():
                                    pygame.scrap.init()
                                text = pygame.scrap.get(pygame.SCRAP_TEXT)
                                if text:
                                    text = text.decode("utf-8", errors="ignore").strip("\x00")
                                    valid = "".join(c for c in text if c.isdigit() or c in ".:" or c.isalpha() or c == "-")
                                    self.mp_text_input = (self.mp_text_input + valid)[:40]
                            except Exception:
                                pass
                        else:
                            ch = event.unicode
                            if not (pygame.key.get_mods() & pygame.KMOD_CTRL):
                                if ch and (ch.isdigit() or ch in ".:" or ch.isalpha() or ch == "-"):
                                    if len(self.mp_text_input) < 40:
                                        self.mp_text_input += ch

                    elif self.state == "mp_end":
                        if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            if self.server is not None:
                                self.host_replay()


                    elif self.state == "lobby_map_vote":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.mp_vote_index = (self.mp_vote_index - 1) % len(self.mp_vote_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.mp_vote_index = (self.mp_vote_index + 1) % len(self.mp_vote_options)
                        elif event.key == pygame.K_RETURN:
                            self.mp_map_votes[self.player_id] = self.mp_vote_index
                            if self.server:
                                self.server.broadcast({"type": "votes_update", "votes": self.mp_map_votes})
                            elif self.client:
                                self.client.send({"type": "vote", "vote": self.mp_vote_index})

                    elif self.state == "lobby_wait_host":
                        if event.key == pygame.K_SPACE:
                            if self.mp_mode == "dbd":
                                self.state = "lobby_map_vote"
                                self.mp_vote_timer = 15.0
                                self.mp_map_votes = {}
                                self.server.broadcast({"type": "start_vote"})
                            else:
                                self.host_start_match()
                        else:
                            if event.key in (pygame.K_UP, pygame.K_w):
                                self.settings_index = \
                                    (self.settings_index - 1) % len(self.settings_rows)
                            elif event.key in (pygame.K_DOWN, pygame.K_s):
                                self.settings_index = \
                                    (self.settings_index + 1) % len(self.settings_rows)
                            elif event.key in (pygame.K_LEFT, pygame.K_a):
                                key = self.settings_rows[self.settings_index] \
                                          .lower().replace(" ", "_")
                                self.cycle_setting(key, -1)
                            elif event.key in (pygame.K_RIGHT, pygame.K_d):
                                key = self.settings_rows[self.settings_index] \
                                          .lower().replace(" ", "_")
                                self.cycle_setting(key, +1)

                    elif self.state == "title":
                        self.state = "play"
                    elif self.state == "dead":
                        self.reset()
                        self.state = "play"

                # mouse clicks
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.state == "menu":
                        self.handle_menu_click(event.pos)
                    elif self.state == "lobby":
                        self.handle_lobby_click(event.pos)
                    elif self.state == "lobby_mode_pick":
                        self.handle_mode_click(event.pos)
                    elif self.state == "mp_end":
                        if "replay" in self.mp_end_rects \
                           and self.mp_end_rects["replay"].collidepoint(event.pos):
                            self.host_replay()
                        elif "menu" in self.mp_end_rects \
                             and self.mp_end_rects["menu"].collidepoint(event.pos):
                            self.mp_disconnect()
                    elif self.state == "play":
                        # panel toggle buttons
                        self.btn_maze.handle_click(event.pos)
                        self.btn_hunter.handle_click(event.pos)
                        self.btn_portals.handle_click(event.pos)

            if self.state == "play":
                pressed = pygame.key.get_pressed()
                e_held  = pressed[pygame.K_e]

                # gates (hold E to toggle; gate stays in toggled state)
                self.update_gates(dt, e_held)

                # player movement
                self.move_timer += dt
                if self.move_timer >= MOVE_DELAY:
                    for key, (dr, dc) in move_keys.items():
                        if pressed[key]:
                            self.move_timer = 0
                            self.try_move(dr, dc)
                            break

                # hunter
                self.update_hunter(dt)

                # maze shift
                if self.btn_maze.state:
                    self.maze_timer += dt
                    if self.maze_timer >= MAZE_CHANGE_INTERVAL:
                        self.maze_timer = 0.0
                        self.shift_maze()

            elif self.state == "lobby_map_vote":
                if self.server is not None:
                    # Host handles vote packets
                    for ci, msg in self.server.drain_all():
                        if msg.get("type") == "vote":
                            self.mp_map_votes[ci + 1] = msg.get("vote", 0)
                            self.server.broadcast({"type": "votes_update", "votes": self.mp_map_votes})
                    
                    self.mp_vote_timer -= dt
                    # Check if all human players voted
                    human_count = self.server.count() + 1
                    if len(self.mp_map_votes) >= human_count or self.mp_vote_timer <= 0:
                        # end vote
                        vote_counts = [0] * len(self.mp_vote_options)
                        for v in self.mp_map_votes.values():
                            if 0 <= v < len(vote_counts): vote_counts[v] += 1
                        
                        max_votes = max(vote_counts) if vote_counts else 0
                        winners = [i for i, c in enumerate(vote_counts) if c == max_votes]
                        import random
                        self.mp_selected_map = random.choice(winners) if winners else 0
                        
                        self.host_start_dbd_match()
                elif self.client is not None:
                    for msg in self.client.drain():
                        if msg.get("type") == "votes_update":
                            self.mp_map_votes = msg.get("votes", {})
                            # keys might be strings via json, convert to int
                            self.mp_map_votes = {int(k): v for k, v in self.mp_map_votes.items()}
                        elif msg.get("type") == "start":
                            self.state = "mp_play"
                            self._apply_state(msg)
            elif self.state == "lobby_wait_host" and self.server is not None:
                # accept hellos, send welcomes
                for ci, msg in self.server.drain_all():
                    if msg.get("type") == "hello":
                        self.server.send_to(ci, {"type": "welcome", "id": ci + 1})
                self.server.prune_dead()

            elif self.state == "lobby_wait_client" and self.client is not None:
                self.mp_client_tick(dt)
                for msg in self.client.drain():
                    if msg.get("type") == "start_vote":
                        self.state = "lobby_map_vote"
                        self.mp_vote_timer = 15.0
                        self.mp_map_votes = {}
                    elif msg.get("type") == "start":
                        self.state = "mp_play"
                        self._apply_state(msg)

            elif self.state == "mp_end":
                # keep the client draining so it sees the host's REPLAY
                if self.client is not None:
                    self.mp_client_tick(dt)

            elif self.state == "mp_play":
                # capture local input (used by host as P0, or sent by client)
                pressed = pygame.key.get_pressed()
                # priority: up > down > left > right (matches single-player feel)
                if pressed[pygame.K_UP] or pressed[pygame.K_w]:
                    dr, dc = -1, 0
                elif pressed[pygame.K_DOWN] or pressed[pygame.K_s]:
                    dr, dc = 1, 0
                elif pressed[pygame.K_LEFT] or pressed[pygame.K_a]:
                    dr, dc = 0, -1
                elif pressed[pygame.K_RIGHT] or pressed[pygame.K_d]:
                    dr, dc = 0, 1
                else:
                    dr, dc = 0, 0
                self.mp_local_input = {
                    "dr": dr, "dc": dc, "e_held": pressed[pygame.K_e],
                    "skill_seq": self.mp_skill_seq,
                }

                if self.server is not None:
                    self.mp_host_tick(dt)
                elif self.client is not None:
                    self.mp_client_tick(dt)

            # ── render ────────────────────────────────────────────────────────
            if self.state == "menu":
                self.draw_menu()

            elif self.state == "lobby":
                self.draw_lobby()

            elif self.state == "lobby_mode_pick":
                self.draw_lobby_mode_pick()
            elif self.state == "lobby_map_vote":
                self.draw_lobby_map_vote()

            elif self.state == "lobby_join_input":
                self.draw_lobby_join_input()

            elif self.state in ("lobby_wait_host", "lobby_wait_client"):
                self.draw_lobby_wait()

            elif self.state == "mp_play":
                self.draw_mp_play()

            elif self.state == "mp_end":
                self.draw_mp_end()

            else:
                self.draw_maze()

                for g in self.gates:
                    g.draw(self.surf, self.walls)

                if self.btn_portals.state:
                    t = pygame.time.get_ticks() / 1000
                    for p in self.portals:
                        p.draw(self.surf, t)

                self.draw_exit()
                self.player.draw(self.surf)

                if self.btn_hunter.state:
                    self.hunter.draw(self.surf)

                self.draw_panel()
                self.draw_maze_timer_bar()

                if self.state == "title":
                    self.draw_overlay(
                        "VOID MAZE",
                        [
                            "WASD / ARROWS : MOVE",
                            "HOLD E : OPEN NEARBY GATE",
                            "AVOID THE RED HUNTER",
                            "REACH THE EXIT",
                        ],
                        CYAN,
                    )

                elif self.state == "dead":
                    self.draw_overlay(
                        "YOU DIED",
                        [
                            f"SCORE : {self.score}",
                            f"LEVEL : {self.level}",
                            "",
                            "PRESS ANY KEY",
                        ],
                        (255, 70, 70),
                    )

            pygame.display.flip()


if __name__ == "__main__":
    Game().run()
