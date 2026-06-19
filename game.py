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
import progression

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(BASE_DIR, "assets")
PROFILE_PATH = os.path.join(BASE_DIR, "save", "profile.json")

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
HUNTER_SLASH_RANGE  = 1.45
HUNTER_SLASH_COOLDOWN = 0.75
RUNNER_DOWNED_TIME  = 8.0
TRAP_CHECK_COUNT    = 3
TRAP_CHECK_DURATION = 1.5
TRAP_CHECK_WIDTH    = 0.18
TRAP_COOLDOWN       = 5.0
SKILL_BASE_COOLDOWN = 15.0
SKILL_SPEED_TIME    = 6.0
SKILL_INVISIBLE_TIME = 5.0
SKILL_PHASE_TIME    = 2.0
SKILL_BLIND_TIME    = 3.5
SKILL_ORB_SPAWN_MIN = 6.0
SKILL_ORB_SPAWN_MAX = 10.0
SKILL_ORB_MAX       = 6
SKILL_ORB_INITIAL   = 3
SKILL_NAMES = {
    "speed": "SPRINT",
    "invisible": "INVIS",
    "phase": "PHASE",
    "spear": "SPEAR",
    "flash": "FLASH",
    "teleport": "PEARL",
    "trap": "TRAP",
}
SKILL_COLORS = {
    "speed": (80, 220, 120),
    "invisible": (120, 220, 255),
    "phase": (190, 100, 255),
    "spear": (255, 90, 70),
    "flash": (255, 235, 80),
    "teleport": (80, 220, 210),
    "trap": (255, 130, 40),
}
RANDOM_SKILLS = ("speed", "invisible", "phase", "spear", "flash", "teleport")
SKILL_DESCRIPTIONS = {
    "speed": "Run faster for a short duration.",
    "invisible": "Hide your body from opponents.",
    "phase": "Move through walls briefly.",
    "spear": "Fast global projectile that stuns.",
    "flash": "Throw a bomb that blinds in an area.",
    "teleport": "Throw a pearl and teleport to it.",
    "trap": "Hunter-only trap with skill checks.",
}
SKILL_ICON_CELLS = {
    "speed": (0, 0),
    "invisible": (1, 0),
    "phase": (2, 0),
    "spear": (3, 0),
    "flash": (0, 1),
    "teleport": (1, 1),
    "trap": (2, 1),
}
ITEM_ATLAS_CELLS = {
    "cleaver": (0, 0),
    "spear": (1, 0),
    "flash": (2, 0),
    "teleport": (0, 1),
    "trap": (1, 1),
    "toolkit": (2, 1),
}


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
        self.font_tiny = pygame.font.Font(None, 23)
        self._load_visual_assets()

        # ── toggle buttons (positioned in panel) ─────────────────────────────
        bx = PANEL_X + 30
        self.btn_maze    = ToggleButton("MAZE SHIFT",  bx, H - 160, state=True)
        self.btn_hunter  = ToggleButton("HUNTER",      bx, H - 112, state=True)
        self.btn_portals = ToggleButton("PORTALS",     bx, H -  64, state=True)

        # menu state
        self.profile = progression.load_profile(PROFILE_PATH)
        progression.save_profile(PROFILE_PATH, self.profile)
        self.profile_name_input = self.profile.get("name", "")
        self.profile_editing = False
        self.daily_notice = ""
        self.shop_tabs = ["BUY", "UPGRADE", "LOADOUT"]
        self.shop_tab = 0
        self.shop_index = 0
        self.shop_notice = ""
        self.shop_rects = []
        self.shop_tab_rects = []
        self.menu_options = [
            "SINGLE PLAYER",
            "MULTIPLAYER",
            "PROFILE",
            "DAILY MISSIONS",
            "SHOP",
            "QUIT",
        ]
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
            "dr": 0, "dc": 0, "e_held": False, "skill_seq": 0,
            "attack_seq": 0, "interact_seq": 0, "use_seq": 0,
            "selected_skill": 0, "aim_r": 0.0, "aim_c": 0.0,
        }  # client-side
        self.mp_skill_seq      = 0     # increments on each SPACE press
        self.mp_skill_seen     = {}    # host-side last consumed sequence per pid
        self.mp_attack_seq     = 0
        self.mp_interact_seq   = 0
        self.mp_use_seq        = 0
        self.mp_selected_skill = 0
        self.mp_attack_seen    = {}
        self.mp_interact_seen  = {}
        self.mp_use_seen       = {}
        self.mp_send_timer    = 0.0   # client throttle for sending input
        self.mp_broadcast_timer = 0.0 # host throttle for broadcasting state
        self.mp_prev_positions = {}   # host-side previous cells for crossing catches

        # DBD-specific shared state
        self.mp_generators   = []     # list of Generator
        self.mp_freezing_pods   = []     # list of FreezingPod
        self.mp_projectiles  = []
        self.mp_explosions   = []
        self.mp_skill_orbs   = []
        self.mp_traps        = []
        self.mp_orb_timer    = 0.0
        self.mp_network_profiles = {
            0: progression.network_profile(self.profile)
        }
        self.mp_match_rewarded = False
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

    @staticmethod
    def _atlas_cell(atlas, col, row, columns, rows):
        cell_w = atlas.get_width() // columns
        cell_h = atlas.get_height() // rows
        return atlas.subsurface(
            pygame.Rect(col * cell_w, row * cell_h, cell_w, cell_h)
        ).copy()

    def _load_visual_assets(self):
        self.actor_sprites = {}
        self.item_sprites = {}
        self.skill_icons = {}
        self.floor_tile = None
        self.wall_tile = None
        self.panel_surface = pygame.Surface((PANEL_W, H))
        panel_draw = pygame.draw
        for y in range(H):
            mix = y / max(1, H - 1)
            color = (
                int(10 - 3 * mix),
                int(17 - 5 * mix),
                int(27 - 5 * mix),
            )
            panel_draw.line(self.panel_surface, color, (0, y), (PANEL_W, y))
        for x in range(0, PANEL_W, 32):
            panel_draw.line(
                self.panel_surface, (12, 25, 36),
                (x, 0), (x, H), 1,
            )

        try:
            characters = pygame.image.load(
                os.path.join(ASSET_DIR, "character_atlas.png")
            ).convert_alpha()
            items = pygame.image.load(
                os.path.join(ASSET_DIR, "item_atlas.png")
            ).convert_alpha()
            icons = pygame.image.load(
                os.path.join(ASSET_DIR, "skill_icons.png")
            ).convert_alpha()
            environment = pygame.image.load(
                os.path.join(ASSET_DIR, "environment_atlas.png")
            ).convert()
        except (pygame.error, FileNotFoundError):
            self.maze_floor_surface = None
            return

        actor_sizes = {
            "hunter": max(42, int(CELL * 2.05)),
            "runner": max(30, int(CELL * 1.42)),
        }
        for role, col in (("hunter", 0), ("runner", 1)):
            source = self._atlas_cell(characters, col, 0, 2, 1)
            actor_size = actor_sizes[role]
            self.actor_sprites[role] = pygame.transform.smoothscale(
                source, (actor_size, actor_size)
            )

        item_size = max(24, int(CELL * 1.12))
        for name, (col, row) in ITEM_ATLAS_CELLS.items():
            source = self._atlas_cell(items, col, row, 3, 2)
            self.item_sprites[name] = pygame.transform.smoothscale(
                source, (item_size, item_size)
            )

        for name, (col, row) in SKILL_ICON_CELLS.items():
            source = self._atlas_cell(icons, col, row, 4, 2)
            self.skill_icons[name] = pygame.transform.smoothscale(
                source, (48, 48)
            )

        floor = self._atlas_cell(environment, 0, 0, 2, 1)
        wall = self._atlas_cell(environment, 1, 0, 2, 1)
        self.floor_tile = pygame.transform.smoothscale(floor, (CELL, CELL))
        self.wall_tile = pygame.transform.smoothscale(wall, (CELL, CELL))
        self.maze_floor_surface = pygame.Surface((MAZE_W, MAZE_H))
        for r in range(ROWS):
            for c in range(COLS):
                self.maze_floor_surface.blit(
                    self.floor_tile, (c * CELL, r * CELL)
                )

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
        self.surf.fill((2, 5, 9))
        if self.maze_floor_surface is not None:
            self.surf.blit(self.maze_floor_surface, (MAZE_OX, MAZE_OY))

        gate_cells = {(g.r, g.c) for g in self.gates}

        for r in range(ROWS):
            for c in range(COLS):
                if self.walls[r][c] and (r, c) not in gate_cells:
                    rect = pygame.Rect(c * CELL + MAZE_OX, r * CELL + MAZE_OY,
                                       CELL, CELL)
                    if self.wall_tile is not None:
                        self.surf.blit(self.wall_tile, rect)
                    else:
                        pygame.draw.rect(self.surf, (0, 40, 70), rect)
                        pygame.draw.rect(self.surf, (0, 90, 140), rect, 1)

        pygame.draw.rect(
            self.surf, (14, 83, 106),
            (MAZE_OX, MAZE_OY, MAZE_W, MAZE_H), 2,
        )

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
    def _save_profile(self):
        progression.save_profile(PROFILE_PATH, self.profile)
        self.mp_network_profiles[0] = progression.network_profile(self.profile)

    def _record_daily(self, mission_key, amount=1):
        completed = progression.add_mission_progress(
            self.profile, mission_key, amount
        )
        if completed:
            definition = progression.MISSION_DEFS[mission_key]
            self.daily_notice = (
                f"MISSION COMPLETE  +{definition['coins']} COINS"
                f"  +{definition['xp']} XP"
            )
        self._save_profile()

    def _record_match_result(self):
        if self.mp_match_rewarded:
            return
        self.mp_match_rewarded = True
        self.profile["stats"]["matches"] += 1
        self._record_daily("matches")

        me = next(
            (p for p in self.mp_players if p["id"] == self.player_id),
            None,
        )
        if me is not None and me.get("role") == "runner" \
                and me.get("escaped"):
            self.profile["stats"]["escapes"] += 1
            self.profile["coins"] += 90
            progression.add_xp(self.profile, 70)
            self._record_daily("escapes")
        elif me is not None and me.get("role") == "hunter" \
                and self.mp_winner == "hunter":
            self.profile["stats"]["hunter_wins"] += 1
            self.profile["coins"] += 110
            progression.add_xp(self.profile, 80)
        self._save_profile()

    def _shop_skills(self):
        if self.shop_tabs[self.shop_tab] == "LOADOUT":
            return [
                skill for skill in progression.LOADOUT_SKILLS
                if skill in self.profile["owned_skills"]
            ]
        return list(progression.SKILL_ORDER)

    def _shop_selected_skill(self):
        skills = self._shop_skills()
        if not skills:
            return None
        self.shop_index %= len(skills)
        return skills[self.shop_index]

    def _shop_action(self):
        skill = self._shop_selected_skill()
        if skill is None:
            return
        tab = self.shop_tabs[self.shop_tab]
        owned = skill in self.profile["owned_skills"]
        level = self.profile["skill_levels"].get(skill, 1)

        if tab == "BUY":
            if skill == "trap":
                self.shop_notice = "TRAP IS HUNTER-ONLY AND ALWAYS OWNED"
            elif owned:
                self.shop_notice = "ALREADY OWNED"
            else:
                price = progression.SKILL_PRICES[skill]
                if self.profile["coins"] < price:
                    self.shop_notice = "NOT ENOUGH COINS"
                    return
                self.profile["coins"] -= price
                self.profile["owned_skills"].append(skill)
                self.shop_notice = f"PURCHASED {SKILL_NAMES[skill]}"
        elif tab == "UPGRADE":
            if not owned:
                self.shop_notice = "BUY THIS SKILL FIRST"
                return
            price = progression.skill_upgrade_price(skill, level)
            if price is None:
                self.shop_notice = "MAX LEVEL"
                return
            if self.profile["coins"] < price:
                self.shop_notice = "NOT ENOUGH COINS"
                return
            self.profile["coins"] -= price
            self.profile["skill_levels"][skill] = level + 1
            self.shop_notice = f"{SKILL_NAMES[skill]} LEVEL {level + 1}"
        else:
            equipped = self.profile["equipped_skills"]
            if skill in equipped:
                equipped.remove(skill)
                self.shop_notice = f"UNEQUIPPED {SKILL_NAMES[skill]}"
            elif len(equipped) >= 2:
                self.shop_notice = "LOADOUT IS FULL"
                return
            else:
                equipped.append(skill)
                self.shop_notice = f"EQUIPPED {SKILL_NAMES[skill]}"
        self._save_profile()

    @staticmethod
    def _skill_level_value(level, base, per_level):
        return base + max(0, level - 1) * per_level

    def _skill_stats(self, skill, level):
        cooldown = max(
            10.0,
            self._skill_level_value(level, SKILL_BASE_COOLDOWN, -1.0),
        )
        charges = 1 + max(0, level - 1) // 2
        stats = [f"Cooldown {cooldown:.0f}s", f"Charges {charges}"]
        if skill in ("speed", "invisible", "phase"):
            base = {
                "speed": SKILL_SPEED_TIME,
                "invisible": SKILL_INVISIBLE_TIME,
                "phase": SKILL_PHASE_TIME,
            }[skill]
            stats.append(f"Duration {base * (1 + .12 * (level - 1)):.1f}s")
        elif skill in ("spear", "flash", "teleport"):
            stats.append(f"Projectile speed +{12 * (level - 1)}%")
        if skill == "flash":
            stats.append(f"AOE {2 + .5 * (level - 1):.1f} cells")
        return stats

    def select_menu_option(self):
        choice = self.menu_options[self.menu_index]
        if choice == "SINGLE PLAYER":
            self.reset()
            self.state = "title"
        elif choice == "MULTIPLAYER":
            self.mp_status_msg = ""
            self.state = "lobby"
        elif choice == "PROFILE":
            self.profile_name_input = self.profile.get("name", "")
            self.profile_editing = True
            self.state = "profile"
        elif choice == "DAILY MISSIONS":
            self.state = "daily"
        elif choice == "SHOP":
            self.shop_notice = ""
            self.shop_index = 0
            self.state = "shop"
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
        self._draw_frontend_background(
            "MODE SELECT", "Choose the ruleset for this session."
        )

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
        self._draw_frontend_background(
            "MULTIPLAYER", "Host a session or connect to another operative."
        )

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
        self._draw_frontend_background(
            "CONNECT", "Enter the host address to join the facility."
        )

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
        self._draw_frontend_background(
            "READY ROOM", "Configure the match while players connect."
        )

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
        self._draw_frontend_background(
            "MAP VOTE", "Select the facility layout."
        )
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


    def _draw_frontend_background(self, section, subtitle=""):
        self.surf.fill((3, 7, 12))
        tile = max(34, CELL * 2)
        for y in range(0, H, tile):
            for x in range(0, W, tile):
                shade = 10 + ((x // tile + y // tile) % 2) * 3
                pygame.draw.rect(
                    self.surf, (shade, shade + 7, shade + 12),
                    (x, y, tile - 1, tile - 1),
                )
        veil = pygame.Surface((W, H), pygame.SRCALPHA)
        veil.fill((1, 4, 8, 178))
        self.surf.blit(veil, (0, 0))
        pygame.draw.rect(self.surf, (9, 17, 26), (0, 0, W, 86))
        pygame.draw.line(self.surf, (26, 139, 160), (0, 85), (W, 85), 2)

        brand = self.font_lg.render("VOID MAZE", True, (86, 234, 220))
        self.surf.blit(brand, (42, 19))
        section_text = self.font_sm.render(section, True, (190, 207, 215))
        self.surf.blit(
            section_text,
            (W - section_text.get_width() - 42, 29),
        )
        if subtitle:
            sub = self.font_tiny.render(subtitle, True, (109, 137, 151))
            self.surf.blit(sub, (44, 94))

        profile_name = self.profile.get("name") or "UNNAMED OPERATIVE"
        profile = self.font_tiny.render(
            f"{profile_name}   LV {self.profile['level']}"
            f"   {self.profile['coins']} C",
            True, (220, 225, 226),
        )
        self.surf.blit(
            profile, (W - profile.get_width() - 42, H - 34)
        )

    def _draw_menu_option(self, label, rect, selected):
        fill = (14, 26, 37) if selected else (9, 16, 24)
        border = (67, 226, 209) if selected else (31, 57, 69)
        pygame.draw.rect(self.surf, (1, 3, 6), rect.move(0, 5), border_radius=5)
        pygame.draw.rect(self.surf, fill, rect, border_radius=5)
        pygame.draw.rect(self.surf, border, rect, 2, border_radius=5)
        if selected:
            pygame.draw.rect(
                self.surf, (67, 226, 209),
                (rect.x, rect.y, 5, rect.height), border_radius=2,
            )
        text = self.font_med.render(
            label, True, (236, 241, 242) if selected else (153, 172, 181)
        )
        self.surf.blit(
            text, (rect.x + 24, rect.centery - text.get_height() // 2)
        )

    def draw_menu(self):
        self._draw_frontend_background(
            "MAIN TERMINAL",
            "SURVIVAL OPERATIONS TERMINAL",
        )
        title = self.font_xl.render("ENTER THE VOID", True, WHITE)
        self.surf.blit(title, (70, 158))
        accent = self.font_sm.render(
            "ASYMMETRIC HORROR // MAZE SURVIVAL",
            True, (235, 75, 78),
        )
        self.surf.blit(accent, (74, 230))

        self.menu_rects = []
        column_x = 70
        start_y = 300
        width = min(470, W // 3)
        height = 52
        for i, label in enumerate(self.menu_options):
            rect = pygame.Rect(column_x, start_y + i * 62, width, height)
            self.menu_rects.append(rect)
            self._draw_menu_option(label, rect, i == self.menu_index)

        panel = pygame.Rect(W - 520, 155, 440, 500)
        pygame.draw.rect(self.surf, (8, 15, 23), panel, border_radius=6)
        pygame.draw.rect(
            self.surf, (29, 70, 83), panel, 2, border_radius=6
        )
        hunter = self.actor_sprites.get("hunter")
        runner = self.actor_sprites.get("runner")
        if hunter is not None:
            shown = pygame.transform.smoothscale(hunter, (190, 190))
            self.surf.blit(shown, shown.get_rect(center=(panel.x + 135, panel.y + 175)))
        if runner is not None:
            shown = pygame.transform.smoothscale(runner, (135, 135))
            self.surf.blit(shown, shown.get_rect(center=(panel.x + 315, panel.y + 205)))
        mission_states = self.profile["daily"]["missions"]
        complete = sum(1 for state in mission_states.values() if state["claimed"])
        daily = self.font_med.render(
            f"DAILY OPERATIONS  {complete}/{len(mission_states)}",
            True, (240, 205, 71),
        )
        self.surf.blit(daily, (panel.x + 28, panel.y + 330))
        loadout = ", ".join(
            SKILL_NAMES[skill]
            for skill in self.profile["equipped_skills"]
        ) or "EMPTY"
        loadout_text = self.font_sm.render(
            f"LOADOUT  {loadout}", True, (174, 196, 206)
        )
        self.surf.blit(loadout_text, (panel.x + 28, panel.y + 382))

    def draw_profile(self):
        self._draw_frontend_background(
            "PROFILE",
            "Identity and persistent progression.",
        )
        panel = pygame.Rect(W // 2 - 430, 145, 860, 560)
        pygame.draw.rect(self.surf, (8, 15, 23), panel, border_radius=6)
        pygame.draw.rect(self.surf, (31, 73, 86), panel, 2, border_radius=6)

        name_label = self.font_sm.render("PLAYER NAME", True, (120, 149, 163))
        self.surf.blit(name_label, (panel.x + 44, panel.y + 42))
        name_box = pygame.Rect(panel.x + 44, panel.y + 82, 500, 62)
        pygame.draw.rect(self.surf, (4, 9, 15), name_box, border_radius=5)
        pygame.draw.rect(
            self.surf, (70, 226, 211), name_box, 2, border_radius=5
        )
        value = self.profile_name_input + ("_" if self.profile_editing else "")
        name_text = self.font_med.render(value or "ENTER NAME", True, WHITE)
        self.surf.blit(
            name_text,
            (name_box.x + 16, name_box.centery - name_text.get_height() // 2),
        )

        level = self.profile["level"]
        needed = progression.xp_needed(level)
        xp = self.profile["xp"]
        level_text = self.font_lg.render(f"LEVEL {level}", True, (239, 205, 71))
        self.surf.blit(level_text, (panel.x + 600, panel.y + 55))
        bar = pygame.Rect(panel.x + 600, panel.y + 115, 210, 14)
        pygame.draw.rect(self.surf, (27, 42, 51), bar, border_radius=4)
        pygame.draw.rect(
            self.surf, (70, 226, 211),
            (bar.x, bar.y, int(bar.width * xp / max(1, needed)), bar.height),
            border_radius=4,
        )

        stats = self.profile["stats"]
        rows = [
            ("COINS", str(self.profile["coins"])),
            ("MATCHES", str(stats["matches"])),
            ("ESCAPES", str(stats["escapes"])),
            ("HUNTER WINS", str(stats["hunter_wins"])),
            ("SKILLS USED", str(stats["skills_used"])),
        ]
        for index, (label, number) in enumerate(rows):
            row = pygame.Rect(
                panel.x + 44 + (index % 2) * 390,
                panel.y + 210 + (index // 2) * 82,
                350, 62,
            )
            pygame.draw.rect(self.surf, (11, 21, 30), row, border_radius=5)
            label_text = self.font_tiny.render(label, True, (111, 141, 154))
            number_text = self.font_med.render(number, True, WHITE)
            self.surf.blit(label_text, (row.x + 16, row.y + 9))
            self.surf.blit(number_text, (row.x + 16, row.y + 27))

        hint = self.font_sm.render("SAVE NAME", True, (130, 157, 169))
        self.surf.blit(hint, (panel.x + 44, panel.bottom - 52))

    def draw_daily(self):
        self._draw_frontend_background(
            "DAILY MISSIONS",
            "DAILY OPERATIONS",
        )
        start_y = 180
        for index, (key, definition) in enumerate(
                progression.MISSION_DEFS.items()):
            state = self.profile["daily"]["missions"][key]
            card = pygame.Rect(W // 2 - 430, start_y + index * 140, 860, 112)
            pygame.draw.rect(self.surf, (8, 16, 24), card, border_radius=6)
            color = (80, 224, 127) if state["claimed"] else (61, 210, 198)
            pygame.draw.rect(self.surf, color, card, 2, border_radius=6)
            label = self.font_med.render(definition["label"], True, WHITE)
            self.surf.blit(label, (card.x + 28, card.y + 18))
            reward = self.font_sm.render(
                f"+{definition['coins']} COINS   +{definition['xp']} XP",
                True, (239, 205, 71),
            )
            self.surf.blit(reward, (card.x + 28, card.y + 61))
            progress = min(state["progress"], definition["target"])
            status = "CLAIMED" if state["claimed"] else \
                f"{progress}/{definition['target']}"
            status_text = self.font_med.render(status, True, color)
            self.surf.blit(
                status_text,
                (card.right - status_text.get_width() - 28, card.y + 37),
            )
        if self.daily_notice:
            notice = self.font_sm.render(
                self.daily_notice, True, (239, 205, 71)
            )
            self.surf.blit(
                notice, (W // 2 - notice.get_width() // 2, H - 92)
            )

    def draw_shop(self):
        self._draw_frontend_background(
            "SKILL SHOP",
            "PERMANENT LOADOUT CONFIGURATION",
        )
        tab_y = 132
        self.shop_tab_rects = []
        for index, label in enumerate(self.shop_tabs):
            rect = pygame.Rect(W // 2 - 330 + index * 225, tab_y, 210, 48)
            self.shop_tab_rects.append(rect)
            selected = index == self.shop_tab
            pygame.draw.rect(
                self.surf, (15, 30, 40) if selected else (8, 15, 23),
                rect, border_radius=5,
            )
            pygame.draw.rect(
                self.surf,
                (70, 226, 211) if selected else (31, 61, 73),
                rect, 2, border_radius=5,
            )
            text = self.font_sm.render(label, True, WHITE)
            self.surf.blit(text, text.get_rect(center=rect.center))

        skills = self._shop_skills()
        self.shop_rects = []
        grid_x = 70
        grid_y = 230
        for index, skill in enumerate(skills):
            row = index % 4
            col = index // 4
            rect = pygame.Rect(grid_x + col * 300, grid_y + row * 98, 270, 78)
            self.shop_rects.append(rect)
            selected = index == self.shop_index
            owned = skill in self.profile["owned_skills"]
            color = SKILL_COLORS[skill]
            pygame.draw.rect(self.surf, (9, 17, 25), rect, border_radius=5)
            pygame.draw.rect(
                self.surf, color if selected else (31, 57, 68),
                rect, 3 if selected else 1, border_radius=5,
            )
            icon = self.skill_icons.get(skill)
            if icon is not None:
                self.surf.blit(icon, icon.get_rect(center=(rect.x + 45, rect.centery)))
            label = self.font_sm.render(SKILL_NAMES[skill], True, WHITE)
            self.surf.blit(label, (rect.x + 82, rect.y + 13))
            state = "OWNED" if owned else f"{progression.SKILL_PRICES[skill]} C"
            state_text = self.font_tiny.render(
                state, True, color if owned else (239, 205, 71)
            )
            self.surf.blit(state_text, (rect.x + 82, rect.y + 46))

        skill = self._shop_selected_skill()
        if skill is not None:
            detail = pygame.Rect(W - 555, 230, 480, 390)
            pygame.draw.rect(self.surf, (8, 16, 24), detail, border_radius=6)
            pygame.draw.rect(
                self.surf, SKILL_COLORS[skill], detail, 2, border_radius=6
            )
            icon = self.skill_icons.get(skill)
            if icon is not None:
                shown = pygame.transform.smoothscale(icon, (96, 96))
                self.surf.blit(shown, (detail.x + 28, detail.y + 30))
            title = self.font_lg.render(SKILL_NAMES[skill], True, WHITE)
            self.surf.blit(title, (detail.x + 145, detail.y + 34))
            level = self.profile["skill_levels"].get(skill, 1)
            level_text = self.font_sm.render(
                f"LEVEL {level}/{progression.MAX_SKILL_LEVEL}",
                True, SKILL_COLORS[skill],
            )
            self.surf.blit(level_text, (detail.x + 148, detail.y + 92))
            description = self.font_sm.render(
                SKILL_DESCRIPTIONS[skill], True, (164, 184, 194)
            )
            self.surf.blit(description, (detail.x + 28, detail.y + 150))
            for index, stat in enumerate(self._skill_stats(skill, level)):
                text = self.font_sm.render(stat, True, (219, 226, 228))
                self.surf.blit(text, (detail.x + 32, detail.y + 205 + index * 36))

            tab = self.shop_tabs[self.shop_tab]
            if tab == "BUY":
                action = "PURCHASE"
            elif tab == "UPGRADE":
                price = progression.skill_upgrade_price(skill, level)
                action = "MAX LEVEL" if price is None else f"UPGRADE {price} C"
            else:
                equipped = skill in self.profile["equipped_skills"]
                action = "UNEQUIP" if equipped else "EQUIP"
            action_text = self.font_med.render(
                action, True, (239, 205, 71)
            )
            self.surf.blit(
                action_text,
                (detail.x + 28, detail.bottom - action_text.get_height() - 28),
            )

        if self.shop_notice:
            notice = self.font_sm.render(
                self.shop_notice, True, (239, 205, 71)
            )
            self.surf.blit(notice, (70, H - 88))

    # ── multiplayer: host/client lifecycle ────────────────────────────────────
    def start_host_mode(self):
        """Open a TCP server on DEFAULT_PORT and become player 0."""
        try:
            self.server = network.Server()
        except Exception as e:
            self.mp_status_msg = f"Server failed to start: {e}"
            return
        self.player_id = 0
        self.mp_network_profiles = {
            0: progression.network_profile(self.profile)
        }
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
        self.client.send({
            "type": "hello",
            "profile": progression.network_profile(self.profile),
        })
        self.mp_status_msg = ""
        self.state = "lobby_wait_client"

    def _prune_lobby_connections(self):
        dead = self.server.prune_dead()
        if not dead:
            return
        remapped = {
            0: progression.network_profile(self.profile)
        }
        for player_id, profile in self.mp_network_profiles.items():
            if player_id == 0:
                continue
            connection_index = player_id - 1
            if connection_index in dead:
                continue
            shift = sum(1 for index in dead if index < connection_index)
            remapped[player_id - shift] = profile
        self.mp_network_profiles = remapped

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
        self.mp_attack_seen   = {}
        self.mp_interact_seen = {}
        self.mp_use_seen      = {}
        self.mp_skill_seq     = 0
        self.mp_attack_seq    = 0
        self.mp_interact_seq  = 0
        self.mp_use_seq       = 0
        self.mp_selected_skill = 0
        self.mp_local_input   = {
            "dr": 0, "dc": 0, "e_held": False, "skill_seq": 0,
            "attack_seq": 0, "interact_seq": 0, "use_seq": 0,
            "selected_skill": 0, "aim_r": 0.0, "aim_c": 0.0,
        }
        self.mp_generators   = []
        self.mp_freezing_pods   = []
        self.mp_projectiles = []
        self.mp_explosions = []
        self.mp_skill_orbs = []
        self.mp_traps = []
        self.mp_orb_timer = 0.0
        self.mp_winner       = ""
        self.mp_match_rewarded = False
        self.mp_network_profiles = {
            0: progression.network_profile(self.profile)
        }
        self.exit_unlocked   = True
        self.player_id        = 0
        self.state            = "menu"

    # ── host: start a multiplayer match ───────────────────────────────────────

    def _initialize_dbd_player(self, p):
        profile = self.mp_network_profiles.get(p["id"], {})
        if p.get("is_bot"):
            equipped = random.sample(
                list(RANDOM_SKILLS), min(2, len(RANDOM_SKILLS))
            )
            skill_levels = {skill: 1 for skill in progression.SKILL_ORDER}
            player_name = ""
        else:
            equipped = [
                skill for skill in profile.get("equipped_skills", [])
                if skill in progression.LOADOUT_SKILLS
            ][:2]
            if not equipped:
                equipped = ["speed"]
            skill_levels = {
                skill: max(
                    1, min(
                        progression.MAX_SKILL_LEVEL,
                        int(profile.get("skill_levels", {}).get(skill, 1)),
                    )
                )
                for skill in progression.SKILL_ORDER
            }
            player_name = progression.sanitize_name(profile.get("name", ""))

        skills = (
            ["trap"] + equipped[:1]
            if p.get("role") == "hunter"
            else equipped[:2]
        )
        charges = {
            skill: 1 + max(0, skill_levels.get(skill, 1) - 1) // 2
            for skill in skills
        }
        p.update({
            "name": player_name,
            "escaped": False,
            "downed": False,
            "downed_remaining": 0.0,
            "stun_remaining": 0.0,
            "speed_remaining": 0.0,
            "invisible_remaining": 0.0,
            "phase_remaining": 0.0,
            "blind_remaining": 0.0,
            "attack_cooldown": 0.0,
            "attack_anim": 0.0,
            "trap_cooldown": 0.0,
            "trapped": False,
            "trap_checks_remaining": 0,
            "trap_check_elapsed": 0.0,
            "trap_check_target": 0.6,
            "trap_check_width": TRAP_CHECK_WIDTH,
            "facing_r": 0.0,
            "facing_c": 1.0,
            "aim_r": float(p["r"]),
            "aim_c": float(p["c"] + 1),
            "selected_skill": 0,
            "skills": skills,
            "skill_levels": skill_levels,
            "skill_charges": charges,
            "skill_cooldowns": {skill: 0.0 for skill in skills},
        })

    def _assign_default_player_names(self):
        runner_number = 0
        for p in self.mp_players:
            if p.get("name"):
                continue
            if p.get("role") == "hunter":
                p["name"] = "Hunter"
            else:
                runner_number += 1
                p["name"] = f"Runner {runner_number}"

    def _mark_runner_escaped(self, runner):
        runner["escaped"] = True
        runner["alive"] = False
        runner["downed"] = False
        runner["carried"] = False
        runner["imprisoned"] = False
        for pod in self.mp_freezing_pods:
            if pod.imprisoned_pid == runner["id"]:
                pod.imprisoned_pid = None

    def _reset_local_action_sequences(self):
        self.mp_skill_seq = 0
        self.mp_attack_seq = 0
        self.mp_interact_seq = 0
        self.mp_use_seq = 0
        self.mp_selected_skill = 0
        self.mp_local_input.update({
            "skill_seq": 0,
            "attack_seq": 0,
            "interact_seq": 0,
            "use_seq": 0,
            "selected_skill": 0,
        })

    def _reset_dbd_action_state(self):
        self.mp_skill_seen = {}
        self.mp_attack_seen = {}
        self.mp_interact_seen = {}
        self.mp_use_seen = {}
        self._reset_local_action_sequences()
        self.mp_projectiles = []
        self.mp_explosions = []
        self.mp_skill_orbs = []
        self.mp_traps = []
        self.mp_orb_timer = random.uniform(
            SKILL_ORB_SPAWN_MIN, SKILL_ORB_SPAWN_MAX
        )
        self.mp_match_rewarded = False

    def _spawn_skill_orb(self):
        taken = {
            (p["r"], p["c"]) for p in self.mp_players if p["alive"]
        }
        taken.update((g.r, g.c) for g in self.mp_generators)
        taken.update((w.r, w.c) for w in self.mp_freezing_pods)
        taken.update((int(o["r"]), int(o["c"])) for o in self.mp_skill_orbs)
        taken.update((int(t["r"]), int(t["c"])) for t in self.mp_traps)
        taken.add(tuple(self.exit_pos))
        cell = free_cell(self.walls, taken)
        if cell is not None:
            self.mp_skill_orbs.append({"r": cell[0], "c": cell[1]})

    def _seed_skill_orbs(self):
        for _ in range(SKILL_ORB_INITIAL):
            self._spawn_skill_orb()

    def host_start_dbd_match(self):
        self._prune_lobby_connections()
        self.new_level()
        self.mp_generators = []
        self.mp_freezing_pods = []
        self._reset_dbd_action_state()
        self.mp_winner = ""
        self.mp_match_timer = DBD_MATCH_LENGTH
        self.exit_unlocked = False
        
        slots = 1 + self.server.count() + self.mp_settings.get("bot_runners", 0)
        self.mp_players = []
        self.mp_move_timers = []
        
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
                "carried": False, "carrying_pid": None,
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
            self._initialize_dbd_player(self.mp_players[-1])
            self.mp_move_timers.append(0.0)

        self._assign_default_player_names()
        for cell in gen_pos:
            self.mp_generators.append(Generator(cell[0], cell[1]))
        for cell in pod_pos:
            self.mp_freezing_pods.append(FreezingPod(cell[0], cell[1]))
        self.mp_skill_orbs = []
            
        self.state = "mp_play"
        self.server.broadcast({"type": "start", **self._serialize_state()})


    def host_start_match(self):
        """Host pressed SPACE — initialise level and broadcast maze + start."""
        # clean any dead connections BEFORE locking in player_id assignments
        self._prune_lobby_connections()

        self.new_level()
        self.mp_generators = []
        self.mp_freezing_pods = []
        self._reset_dbd_action_state()
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
                    "name": progression.sanitize_name(
                        self.mp_network_profiles.get(i, {}).get("name", "")
                    ),
                    "imprisoned": False, "imprison_remaining": 0.0,
                    "carried": False, "carrying_pid": None,
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
                    "carried": False, "carrying_pid": None,
                    "stun_remaining": 0.0,
                })
                self._initialize_dbd_player(self.mp_players[-1])
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
            self.mp_skill_orbs = []
            # DBD-MAZE: no portals
            self.portals = []

        self._assign_default_player_names()
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
            "projectiles": self.mp_projectiles,
            "explosions": self.mp_explosions,
            "skill_orbs": self.mp_skill_orbs,
            "traps": self.mp_traps,
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
        self.mp_projectiles = msg.get("projectiles", [])
        self.mp_explosions = msg.get("explosions", [])
        self.mp_skill_orbs = msg.get("skill_orbs", [])
        self.mp_traps = msg.get("traps", [])
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
            previous_phase = p.get("phase_remaining", 0.0)
            for key in (
                "stun_remaining", "speed_remaining",
                "invisible_remaining", "phase_remaining",
                "blind_remaining", "attack_cooldown",
                "attack_anim", "trap_cooldown",
            ):
                p[key] = max(0.0, p.get(key, 0.0) - dt)
            self._update_skill_cooldowns(p, dt)

            if p.get("downed") and not p.get("carried") \
                    and not p.get("imprisoned"):
                p["downed_remaining"] = max(
                    0.0, p.get("downed_remaining", 0.0) - dt
                )
                if p["downed_remaining"] <= 0:
                    p["downed"] = False

            if previous_phase > 0 and p.get("phase_remaining", 0.0) <= 0 \
                    and is_wall(self.walls, p["r"], p["c"], self.gates):
                p["r"], p["c"] = nearest_free(
                    self.walls, p["r"], p["c"], self.gates
                )

        # 3. per-player movement
        self.mp_prev_positions = {p["id"]: (p["r"], p["c"]) for p in self.mp_players}
        for p in self.mp_players:
            pid = p["id"]
            inp = self.mp_pending_input.get(
                pid, {"dr": 0, "dc": 0, "e_held": False}
            )
            p["selected_skill"] = max(
                0, min(
                    int(inp.get("selected_skill", p.get("selected_skill", 0))),
                    max(0, len(p.get("skills", [])) - 1),
                )
            )
            rows = len(self.walls)
            cols = len(self.walls[0]) if rows else 0
            p["aim_r"] = max(
                0.0, min(float(inp.get("aim_r", p["r"])), rows - 1.0)
            )
            p["aim_c"] = max(
                0.0, min(float(inp.get("aim_c", p["c"] + 1)), cols - 1.0)
            )
            aim_dr = p["aim_r"] - p["r"]
            aim_dc = p["aim_c"] - p["c"]
            aim_len = math.hypot(aim_dr, aim_dc)
            if aim_len > 0.001:
                p["facing_r"] = aim_dr / aim_len
                p["facing_c"] = aim_dc / aim_len
            if not p["alive"] or p.get("imprisoned") or p.get("carried") \
                    or p.get("stun_remaining", 0.0) > 0 \
                    or p.get("downed") or p.get("trapped"):
                continue
            self.mp_move_timers[pid] += dt
            dr, dc = int(inp.get("dr", 0)), int(inp.get("dc", 0))
            move_delay = MOVE_DELAY * (
                0.5 if p.get("speed_remaining", 0.0) > 0 else 1.0
            )
            if (dr or dc) and self.mp_move_timers[pid] >= move_delay:
                nr, nc = p["r"] + dr, p["c"] + dc
                phase_move = p.get("phase_remaining", 0.0) > 0 \
                    and 0 <= nr < rows and 0 <= nc < cols
                if phase_move or not is_wall(self.walls, nr, nc, self.gates):
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
                            # Escapes are individual; remaining runners keep playing.
                            self._mark_runner_escaped(p)

        # 4. gates — any adjacent alive player holding E drives toggle
        for gate in self.gates:
            holding = False
            for p in self.mp_players:
                if p["alive"] and not p.get("imprisoned") and not p.get("carried") \
                   and p.get("stun_remaining", 0.0) <= 0 \
                   and not p.get("downed") and not p.get("trapped") \
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

    def _bot_maybe_use_skill(self, p, hunter):
        skills = p.get("skills", [])
        if not skills:
            return

        target = None
        if p.get("role") == "hunter":
            candidates = [
                x for x in self.mp_players
                if x.get("role") == "runner"
                and x["alive"]
                and not x.get("downed")
                and not x.get("carried")
                and not x.get("imprisoned")
                and x.get("invisible_remaining", 0.0) <= 0
            ]
            if candidates:
                target = min(
                    candidates,
                    key=lambda x: abs(x["r"] - p["r"])
                    + abs(x["c"] - p["c"]),
                )
        elif hunter and hunter["alive"] \
                and hunter.get("invisible_remaining", 0.0) <= 0:
            target = hunter

        if target is None:
            return
        distance = math.hypot(
            target["r"] - p["r"], target["c"] - p["c"]
        )
        preferred = None
        if p.get("role") == "hunter" and "trap" in skills \
                and distance <= 2.0 and self._skill_has_charge(p, "trap"):
            preferred = "trap"
        elif distance <= 7.0:
            for skill in ("spear", "flash", "speed", "invisible", "phase", "teleport"):
                if skill in skills:
                    preferred = skill
                    break
        if preferred is None:
            return

        p["selected_skill"] = skills.index(preferred)
        dr = target["r"] - p["r"]
        dc = target["c"] - p["c"]
        length = max(0.001, math.hypot(dr, dc))
        p["facing_r"], p["facing_c"] = dr / length, dc / length
        p["aim_r"], p["aim_c"] = float(target["r"]), float(target["c"])
        inp = self.mp_pending_input.setdefault(p["id"], {})
        inp["selected_skill"] = p["selected_skill"]
        inp["aim_r"], inp["aim_c"] = p["aim_r"], p["aim_c"]
        inp["use_seq"] = int(inp.get("use_seq", 0)) + 1

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
                    or p.get("stun_remaining", 0.0) > 0 \
                    or p.get("downed") or p.get("trapped"):
                previous = self.mp_pending_input.get(pid, {})
                self.mp_pending_input[pid] = {
                    "dr": 0, "dc": 0, "e_held": False,
                    "skill_seq": int(previous.get("skill_seq", 0)),
                    "attack_seq": int(previous.get("attack_seq", 0)),
                    "interact_seq": int(previous.get("interact_seq", 0)),
                    "use_seq": int(previous.get("use_seq", 0)),
                    "selected_skill": p.get("selected_skill", 0),
                    "aim_r": p["r"] + p.get("facing_r", 0.0),
                    "aim_c": p["c"] + p.get("facing_c", 1.0),
                }
                continue

            # Decrement state-commitment timer
            p["ai_timer"] = max(0.0, p.get("ai_timer", 0.0) - dt)
            p.setdefault("ai_recent", [])
            p.setdefault("ai_target_pos", None)
            p.setdefault("ai_last_pos", None)

            if pid not in self.mp_pending_input:
                self.mp_pending_input[pid] = {
                    "dr": 0, "dc": 0, "e_held": False, "skill_seq": 0,
                    "attack_seq": 0, "interact_seq": 0, "use_seq": 0,
                    "selected_skill": p.get("selected_skill", 0),
                }
            inp = self.mp_pending_input[pid]
            inp["dr"], inp["dc"], inp["e_held"] = 0, 0, False

            self._bot_maybe_use_skill(p, hunter)
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

        downed = [
            x for x in self.mp_players
            if x.get("role") == "runner"
            and x["alive"]
            and x.get("downed")
            and not x.get("carried")
            and not x.get("imprisoned")
        ]
        best_downed, best_path = None, None
        for runner in downed:
            path = bfs_adjacent(
                self.walls, p["r"], p["c"],
                runner["r"], runner["c"], self.gates, True
            )
            if path is not None and (
                    best_path is None or len(path) < len(best_path)):
                best_downed, best_path = runner, path
        if best_downed is not None:
            if best_downed["r"] == p["r"] and best_downed["c"] == p["c"] \
                    or abs(best_downed["r"] - p["r"]) \
                    + abs(best_downed["c"] - p["c"]) <= 1:
                inp["interact_seq"] = int(inp.get("interact_seq", 0)) + 1
            else:
                self._follow_bot_path(p, inp, best_path)
            return

        # Resolve current target if commitment is still alive
        cur_target = None
        if p.get("ai_target_id") is not None and p.get("ai_timer", 0.0) > 0:
            cur_target = next((x for x in self.mp_players
                               if x["id"] == p["ai_target_id"]
                               and x["alive"]
                               and not x.get("imprisoned")
                               and not x.get("carried")
                               and not x.get("downed")
                               and x.get("invisible_remaining", 0.0) <= 0), None)

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
                          and not x.get("carried")
                          and not x.get("downed")
                          and x.get("invisible_remaining", 0.0) <= 0]
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

        dr = cur_target["r"] - p["r"]
        dc = cur_target["c"] - p["c"]
        if math.hypot(dr, dc) <= HUNTER_SLASH_RANGE:
            length = max(0.001, math.hypot(dr, dc))
            p["facing_r"], p["facing_c"] = dr / length, dc / length
            inp["attack_seq"] = int(inp.get("attack_seq", 0)) + 1
            return
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
        hunter_seen = bool(
            hunter and hunter["alive"]
            and hunter.get("invisible_remaining", 0.0) <= 0
        )
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

    def _consume_input_sequence(self, key, seen):
        pressed = set()
        for p in self.mp_players:
            pid = p["id"]
            inp = self.mp_pending_input.get(pid, {})
            seq = int(inp.get(key, 0))
            previous = seen.get(pid, 0)
            if seq != previous:
                seen[pid] = seq
                pressed.add(pid)
        return pressed

    def _consume_skill_check_presses(self):
        return self._consume_input_sequence("skill_seq", self.mp_skill_seen)

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

    def _drop_carried_runner(self, hunter):
        carried = next(
            (
                p for p in self.mp_players
                if p["id"] == hunter.get("carrying_pid")
            ),
            None,
        )
        hunter["carrying_pid"] = None
        if carried is None:
            return

        carried["carried"] = False
        carried["downed"] = True
        carried["downed_remaining"] = max(
            3.0, carried.get("downed_remaining", 0.0)
        )
        directions = [
            (
                int(round(hunter.get("facing_r", 0.0))),
                int(round(hunter.get("facing_c", 1.0))),
            ),
            (0, 1), (1, 0), (0, -1), (-1, 0),
        ]
        for dr, dc in directions:
            nr, nc = hunter["r"] + dr, hunter["c"] + dc
            if not is_wall(self.walls, nr, nc, self.gates):
                carried["r"], carried["c"] = nr, nc
                return
        carried["r"], carried["c"] = hunter["r"], hunter["c"]

    def _handle_hunter_interact(self, hunter):
        if not hunter["alive"] or hunter.get("stun_remaining", 0.0) > 0:
            return
        if hunter.get("carrying_pid") is not None:
            self._drop_carried_runner(hunter)
            return

        candidates = [
            p for p in self.mp_players
            if p.get("role") == "runner"
            and p["alive"]
            and p.get("downed")
            and not p.get("carried")
            and not p.get("imprisoned")
            and abs(p["r"] - hunter["r"]) + abs(p["c"] - hunter["c"]) <= 1
        ]
        if not candidates:
            return
        runner = min(
            candidates,
            key=lambda p: abs(p["r"] - hunter["r"])
            + abs(p["c"] - hunter["c"]),
        )
        runner["carried"] = True
        runner["downed"] = False
        runner["r"], runner["c"] = hunter["r"], hunter["c"]
        hunter["carrying_pid"] = runner["id"]

    def _handle_hunter_attack(self, hunter):
        if not hunter["alive"] or hunter.get("stun_remaining", 0.0) > 0:
            return
        if hunter.get("carrying_pid") is not None:
            return
        if hunter.get("attack_cooldown", 0.0) > 0:
            return

        hunter["attack_cooldown"] = HUNTER_SLASH_COOLDOWN
        hunter["attack_anim"] = 0.25
        facing_r = hunter.get("facing_r", 0.0)
        facing_c = hunter.get("facing_c", 1.0)
        candidates = []
        for runner in self.mp_players:
            if runner.get("role") != "runner" or not runner["alive"]:
                continue
            if runner.get("downed") or runner.get("carried") \
                    or runner.get("imprisoned"):
                continue
            dr = runner["r"] - hunter["r"]
            dc = runner["c"] - hunter["c"]
            dist = math.hypot(dr, dc)
            if dist > HUNTER_SLASH_RANGE:
                continue
            if dist > 0.01:
                dot = (dr * facing_r + dc * facing_c) / dist
                if dot < 0.15:
                    continue
            candidates.append((dist, runner))

        if not candidates:
            return
        runner = min(candidates, key=lambda item: item[0])[1]
        runner["downed"] = True
        runner["downed_remaining"] = RUNNER_DOWNED_TIME
        runner["stun_remaining"] = 0.0
        runner["trapped"] = False
        runner["trap_checks_remaining"] = 0
        runner["ai_state"] = "idle"
        runner["ai_target_pos"] = None

    @staticmethod
    def _skill_level(p, skill):
        return max(1, int(p.get("skill_levels", {}).get(skill, 1)))

    def _skill_max_charges(self, p, skill):
        return 1 + max(0, self._skill_level(p, skill) - 1) // 2

    def _skill_cooldown_time(self, p, skill):
        return max(
            10.0,
            SKILL_BASE_COOLDOWN - (self._skill_level(p, skill) - 1),
        )

    def _skill_has_charge(self, p, skill):
        return p.get("skill_charges", {}).get(skill, 0) > 0

    def _consume_player_skill(self, p, index):
        skills = p.get("skills", [])
        if not (0 <= index < len(skills)):
            return
        skill = skills[index]
        charges = p.setdefault("skill_charges", {})
        cooldowns = p.setdefault("skill_cooldowns", {})
        charges[skill] = max(0, charges.get(skill, 0) - 1)
        if charges[skill] < self._skill_max_charges(p, skill) \
                and cooldowns.get(skill, 0.0) <= 0:
            cooldowns[skill] = self._skill_cooldown_time(p, skill)

    def _update_skill_cooldowns(self, p, dt):
        charges = p.setdefault("skill_charges", {})
        cooldowns = p.setdefault("skill_cooldowns", {})
        for skill in p.get("skills", []):
            maximum = self._skill_max_charges(p, skill)
            charges[skill] = min(maximum, max(0, charges.get(skill, maximum)))
            if charges[skill] >= maximum:
                cooldowns[skill] = 0.0
                continue
            cooldowns[skill] = max(
                0.0, cooldowns.get(skill, self._skill_cooldown_time(p, skill)) - dt
            )
            if cooldowns[skill] <= 0:
                charges[skill] += 1
                cooldowns[skill] = (
                    self._skill_cooldown_time(p, skill)
                    if charges[skill] < maximum else 0.0
                )

    def _launch_projectile(self, p, kind, speed, max_range, target=None):
        target_r = target_c = None
        if target is not None:
            rows = len(self.walls)
            cols = len(self.walls[0]) if rows else 0
            target_r = max(0.0, min(float(target[0]), rows - 1.0))
            target_c = max(0.0, min(float(target[1]), cols - 1.0))
            facing_r = target_r - p["r"]
            facing_c = target_c - p["c"]
            max_range = math.hypot(facing_r, facing_c)
        else:
            facing_r = p.get("facing_r", 0.0)
            facing_c = p.get("facing_c", 1.0)
        length = math.hypot(facing_r, facing_c)
        if length <= 0.001:
            facing_r, facing_c = 0.0, 1.0
        else:
            facing_r /= length
            facing_c /= length
        self.mp_projectiles.append({
            "kind": kind,
            "owner_id": p["id"],
            "skill_level": self._skill_level(p, kind),
            "r": float(p["r"]),
            "c": float(p["c"]),
            "vr": facing_r * speed,
            "vc": facing_c * speed,
            "traveled": 0.0,
            "max_range": float(max_range),
        })
        if target is not None:
            self.mp_projectiles[-1]["target_r"] = target_r
            self.mp_projectiles[-1]["target_c"] = target_c

    def _activate_selected_skill(self, p):
        if not p["alive"] or p.get("stun_remaining", 0.0) > 0 \
                or p.get("downed") or p.get("trapped") \
                or p.get("imprisoned") or p.get("carried"):
            return
        skills = p.get("skills", [])
        if not skills:
            return
        index = max(
            0, min(p.get("selected_skill", 0), len(skills) - 1)
        )
        skill = skills[index]
        if not self._skill_has_charge(p, skill):
            return

        if skill == "trap":
            if p.get("role") != "hunter":
                return
            cell = (p["r"], p["c"])
            if any((t["r"], t["c"]) == cell for t in self.mp_traps):
                return
            self.mp_traps.append({
                "r": cell[0], "c": cell[1], "owner_id": p["id"]
            })
            self._consume_player_skill(p, index)
            return

        level = self._skill_level(p, skill)
        duration_scale = 1.0 + 0.12 * (level - 1)
        projectile_scale = 1.0 + 0.12 * (level - 1)
        if skill == "speed":
            p["speed_remaining"] = SKILL_SPEED_TIME * duration_scale
        elif skill == "invisible":
            p["invisible_remaining"] = SKILL_INVISIBLE_TIME * duration_scale
        elif skill == "phase":
            p["phase_remaining"] = SKILL_PHASE_TIME * duration_scale
        elif skill == "spear":
            rows = len(self.walls)
            cols = len(self.walls[0]) if rows else 0
            self._launch_projectile(
                p, "spear", 18.0 * projectile_scale, math.hypot(rows, cols)
            )
        elif skill == "flash":
            self._launch_projectile(
                p, "flash", 8.25 * projectile_scale, 0.0,
                (p.get("aim_r", p["r"]), p.get("aim_c", p["c"])),
            )
        elif skill == "teleport":
            self._launch_projectile(
                p, "teleport", 6.0 * projectile_scale, 0.0,
                (p.get("aim_r", p["r"]), p.get("aim_c", p["c"])),
            )
        else:
            return
        self._consume_player_skill(p, index)

    @staticmethod
    def _players_are_opponents(a, b):
        return a.get("role") != b.get("role")

    def _explode_flash(self, projectile):
        owner = next(
            (
                p for p in self.mp_players
                if p["id"] == projectile["owner_id"]
            ),
            None,
        )
        if owner is None:
            return
        level = max(1, int(projectile.get("skill_level", 1)))
        half_extent = 1.0 + 0.25 * (level - 1)
        blind_time = SKILL_BLIND_TIME * (1.0 + 0.12 * (level - 1))
        self.mp_explosions.append({
            "r": float(projectile["r"]),
            "c": float(projectile["c"]),
            "age": 0.0,
            "duration": 0.58,
            "half_extent": half_extent,
        })
        for target in self.mp_players:
            if not target["alive"] or not self._players_are_opponents(
                    owner, target):
                continue
            if abs(target["r"] - projectile["r"]) <= half_extent \
                    and abs(target["c"] - projectile["c"]) <= half_extent:
                target["blind_remaining"] = max(
                    target.get("blind_remaining", 0.0),
                    blind_time,
                )

    def _update_explosions(self, dt):
        active = []
        for explosion in self.mp_explosions:
            explosion["age"] += dt
            if explosion["age"] < explosion["duration"]:
                active.append(explosion)
        self.mp_explosions = active

    def _finish_teleport_projectile(self, projectile):
        owner = next(
            (
                p for p in self.mp_players
                if p["id"] == projectile["owner_id"]
            ),
            None,
        )
        if owner is None or not owner["alive"] \
                or owner.get("imprisoned") or owner.get("carried"):
            return
        r = int(round(projectile.get("target_r", projectile["r"])))
        c = int(round(projectile.get("target_c", projectile["c"])))
        owner["r"], owner["c"] = nearest_free(
            self.walls, r, c, self.gates
        )

    def _update_projectiles(self, dt):
        active = []
        rows = len(self.walls)
        cols = len(self.walls[0]) if rows else 0
        for projectile in self.mp_projectiles:
            speed = math.hypot(projectile["vr"], projectile["vc"])
            steps = max(1, int(math.ceil(speed * dt / 0.2)))
            step_dt = dt / steps
            finished = False
            for _ in range(steps):
                projectile["last_r"] = projectile["r"]
                projectile["last_c"] = projectile["c"]
                projectile["r"] += projectile["vr"] * step_dt
                projectile["c"] += projectile["vc"] * step_dt
                projectile["traveled"] += speed * step_dt

                kind = projectile["kind"]
                reached_target = kind in ("flash", "teleport") \
                    and projectile["traveled"] >= projectile["max_range"]
                if reached_target:
                    projectile["r"] = projectile["target_r"]
                    projectile["c"] = projectile["target_c"]
                    if kind == "flash":
                        self._explode_flash(projectile)
                    else:
                        self._finish_teleport_projectile(projectile)
                    finished = True
                    break

                rr = int(round(projectile["r"]))
                cc = int(round(projectile["c"]))
                outside = not (0 <= rr < rows and 0 <= cc < cols)

                if kind == "spear":
                    owner = next(
                        (
                            p for p in self.mp_players
                            if p["id"] == projectile["owner_id"]
                        ),
                        None,
                    )
                    if owner is not None:
                        for target in self.mp_players:
                            if target["id"] == owner["id"] \
                                    or not target["alive"] \
                                    or target.get("imprisoned") \
                                    or target.get("carried") \
                                    or not self._players_are_opponents(
                                        owner, target):
                                continue
                            if math.hypot(
                                target["r"] - projectile["r"],
                                target["c"] - projectile["c"],
                            ) <= 0.5:
                                frac = min(
                                    1.0,
                                    projectile["traveled"]
                                    / max(1.0, projectile["max_range"]),
                                )
                                target["stun_remaining"] = max(
                                    target.get("stun_remaining", 0.0),
                                    (0.7 + 9.3 * frac) * (
                                        1.0 + 0.08 * (
                                            int(projectile.get("skill_level", 1)) - 1
                                        )
                                    ),
                                )
                                finished = True
                                break
                    if outside:
                        finished = True

                if projectile["traveled"] >= projectile["max_range"]:
                    if kind == "spear":
                        finished = True
                if finished:
                    break
            if not finished:
                active.append(projectile)
        self.mp_projectiles = active

    def _reset_trap_check(self, p, reset_chain=False):
        if reset_chain:
            p["trap_checks_remaining"] = TRAP_CHECK_COUNT
        p["trap_check_elapsed"] = 0.0
        p["trap_check_target"] = random.uniform(
            0.45, 0.95 - TRAP_CHECK_WIDTH
        )

    def _update_traps(self, dt, skill_presses):
        remaining_traps = []
        for trap in self.mp_traps:
            triggered = None
            for runner in self.mp_players:
                if runner.get("role") != "runner" or not runner["alive"] \
                        or runner.get("downed") or runner.get("carried") \
                        or runner.get("imprisoned") or runner.get("trapped"):
                    continue
                if (runner["r"], runner["c"]) == (trap["r"], trap["c"]):
                    triggered = runner
                    break
            if triggered is None:
                remaining_traps.append(trap)
                continue
            triggered["trapped"] = True
            triggered["trap_checks_remaining"] = TRAP_CHECK_COUNT
            self._reset_trap_check(triggered)
        self.mp_traps = remaining_traps

        for p in self.mp_players:
            if not p.get("trapped"):
                continue
            p["trap_check_elapsed"] += dt
            needle = min(
                1.0,
                p["trap_check_elapsed"] / TRAP_CHECK_DURATION,
            )
            start = p["trap_check_target"]
            end = start + p.get("trap_check_width", TRAP_CHECK_WIDTH)
            success = False
            attempted = False
            if p.get("is_bot") and needle >= start + (end - start) / 2:
                attempted = True
                success = True
            elif p["id"] in skill_presses:
                attempted = True
                success = start <= needle <= end
            elif needle >= 1.0:
                attempted = True

            if not attempted:
                continue
            if success:
                p["trap_checks_remaining"] -= 1
                if p["trap_checks_remaining"] <= 0:
                    p["trapped"] = False
                    p["trap_check_elapsed"] = 0.0
                    continue
                self._reset_trap_check(p)
            else:
                self._reset_trap_check(p, True)

    def _update_skill_orbs(self, dt):
        self.mp_orb_timer -= dt
        if self.mp_orb_timer <= 0:
            if len(self.mp_skill_orbs) < SKILL_ORB_MAX:
                self._spawn_skill_orb()
            self.mp_orb_timer = random.uniform(
                SKILL_ORB_SPAWN_MIN, SKILL_ORB_SPAWN_MAX
            )

        remaining = []
        for orb in self.mp_skill_orbs:
            collector = next(
                (
                    p for p in self.mp_players
                    if p["alive"]
                    and not p.get("carried")
                    and not p.get("imprisoned")
                    and not p.get("downed")
                    and (p["r"], p["c"]) == (orb["r"], orb["c"])
                    and len(p.get("skills", [])) < 2
                ),
                None,
            )
            if collector is None:
                remaining.append(orb)
                continue
            collector.setdefault("skills", []).append(
                random.choice(RANDOM_SKILLS)
            )
        self.mp_skill_orbs = remaining

    def _dbd_tick(self, dt):
        """Generators, catches, imprisonment, rescues, win conditions."""
        # match timer
        self.mp_match_timer -= dt
        if self.mp_match_timer <= 0:
            self.mp_match_timer = 0
            self._end_dbd("hunter")
            return

        skill_presses = self._consume_skill_check_presses()
        attack_presses = self._consume_input_sequence(
            "attack_seq", self.mp_attack_seen
        )
        interact_presses = self._consume_input_sequence(
            "interact_seq", self.mp_interact_seen
        )
        use_presses = self._consume_input_sequence(
            "use_seq", self.mp_use_seen
        )

        hunter = next(
            (p for p in self.mp_players if p["role"] == "hunter"),
            None,
        )
        if hunter is not None:
            if hunter["id"] in attack_presses:
                self._handle_hunter_attack(hunter)
            if hunter["id"] in interact_presses:
                self._handle_hunter_interact(hunter)

        for p in self.mp_players:
            if p["id"] in use_presses:
                self._activate_selected_skill(p)

        self._update_projectiles(dt)
        self._update_explosions(dt)
        self._update_traps(dt, skill_presses)

        # Generators: repair speed stacks; one shared skill check can interrupt it.
        for gen in self.mp_generators:
            if gen.completed:
                gen.skill_active = False
                continue

            repairers = []
            for p in self.mp_players:
                if p["role"] != "runner" or not p["alive"] \
                        or p.get("imprisoned") or p.get("carried") \
                        or p.get("stun_remaining", 0.0) > 0 \
                        or p.get("downed") or p.get("trapped"):
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

        # Carrying and imprisonment. Contact alone never catches a runner.
        if hunter and hunter["alive"]:
            if hunter.get("carrying_pid") is not None:
                carried_runner = next((p for p in self.mp_players if p["id"] == hunter["carrying_pid"]), None)
                if carried_runner:
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
                else:
                    hunter["carrying_pid"] = None

        # rescue: any free runner adjacent to a freezing_pod holding an imprisoned one
        for w in self.mp_freezing_pods:
            if w.imprisoned_pid is None:
                continue
            for p in self.mp_players:
                if p["role"] != "runner" or not p["alive"] or p.get("imprisoned") or p.get("carried"):
                    continue
                if p.get("stun_remaining", 0.0) > 0 \
                        or p.get("downed") or p.get("trapped"):
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
        self._check_dbd_win_conditions()

    def _check_dbd_win_conditions(self):
        runners = [p for p in self.mp_players if p["role"] == "runner"]
        escaped = [p for p in runners if p.get("escaped")]
        active = [
            p for p in runners
            if p["alive"] and not p.get("escaped")
        ]
        if active and all(p.get("imprisoned") for p in active):
            self._end_dbd("runners" if escaped else "hunter")
            return True
        if runners and not active:
            self._end_dbd("runners" if escaped else "hunter")
            return True
        return False

    def _imprison_runner_in(self, runner, w):
        runner["r"], runner["c"] = w.r, w.c
        runner["downed"] = False
        runner["downed_remaining"] = 0.0
        runner["trapped"] = False
        runner["trap_checks_remaining"] = 0
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
        self._record_match_result()
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
                self.mp_match_rewarded = False
                self._reset_local_action_sequences()
                if self.state in ("lobby_wait_client", "mp_end"):
                    self.state = "mp_play"
            elif t == "end":
                self.mp_winner = msg.get("winner", "")
                self.state = "mp_end"
                self._record_match_result()

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
    def _draw_dbd_objects(self):
        for orb in self.mp_skill_orbs:
            x, y = cell_xy(int(orb["r"]), int(orb["c"]))
            pulse = 1.0 + 0.15 * math.sin(
                pygame.time.get_ticks() / 180.0 + x + y
            )
            radius = max(5, int(CELL * 0.22 * pulse))
            glow = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
            pygame.draw.circle(
                glow, (80, 220, 255, 60),
                (radius * 2, radius * 2), radius * 2,
            )
            self.surf.blit(glow, (x - radius * 2, y - radius * 2))
            pygame.draw.circle(self.surf, (80, 220, 255), (x, y), radius)
            pygame.draw.circle(self.surf, WHITE, (x, y), radius, 2)

        for trap in self.mp_traps:
            x, y = cell_xy(int(trap["r"]), int(trap["c"]))
            sprite = self.item_sprites.get("trap")
            if sprite is not None:
                pulse = 1.0 + 0.06 * math.sin(
                    pygame.time.get_ticks() / 130.0 + x + y
                )
                size = max(22, int(CELL * 1.0 * pulse))
                shown = pygame.transform.smoothscale(sprite, (size, size))
                self.surf.blit(shown, shown.get_rect(center=(x, y)))
            else:
                size = max(5, int(CELL * 0.28))
                points = [
                    (x - size, y + size // 2),
                    (x, y - size),
                    (x + size, y + size // 2),
                ]
                pygame.draw.polygon(self.surf, (180, 70, 30), points)
                pygame.draw.polygon(self.surf, (255, 160, 60), points, 2)

        for projectile in self.mp_projectiles:
            x = int(projectile["c"] * CELL + MAZE_OX + CELL // 2)
            y = int(projectile["r"] * CELL + MAZE_OY + CELL // 2)
            kind = projectile["kind"]
            color = SKILL_COLORS.get(kind, WHITE)
            sprite = self.item_sprites.get(kind)
            if sprite is not None:
                angle = -math.degrees(math.atan2(
                    projectile["vr"], projectile["vc"]
                ))
                shown = pygame.transform.rotozoom(sprite, angle, 0.92)
                self.surf.blit(shown, shown.get_rect(center=(x, y)))
            else:
                radius = max(5, int(CELL * 0.2))
                pygame.draw.circle(self.surf, color, (x, y), radius)
                pygame.draw.circle(self.surf, WHITE, (x, y), radius, 2)

            if kind == "teleport" \
                    and projectile["owner_id"] == self.player_id:
                self._draw_teleport_prediction(projectile)

        for explosion in self.mp_explosions:
            duration = max(0.01, explosion.get("duration", 0.58))
            progress = min(1.0, explosion.get("age", 0.0) / duration)
            x = int(explosion["c"] * CELL + MAZE_OX + CELL // 2)
            y = int(explosion["r"] * CELL + MAZE_OY + CELL // 2)
            half_extent = explosion.get("half_extent", 1.0)
            maximum = max(CELL, int(half_extent * CELL))
            radius = max(4, int(maximum * (0.25 + 0.9 * progress)))
            alpha = max(0, int(230 * (1.0 - progress)))
            size = maximum * 2 + CELL * 2
            blast = pygame.Surface((size, size), pygame.SRCALPHA)
            center = size // 2

            pygame.draw.circle(
                blast, (255, 228, 98, alpha // 3),
                (center, center), radius,
            )
            pygame.draw.circle(
                blast, (255, 244, 205, alpha),
                (center, center), radius, max(2, CELL // 8),
            )
            square_half = int(maximum * min(1.0, progress * 1.5))
            pygame.draw.rect(
                blast, (255, 164, 55, alpha),
                (
                    center - square_half, center - square_half,
                    square_half * 2, square_half * 2,
                ),
                max(2, CELL // 10),
            )
            for index in range(12):
                angle = index * math.tau / 12 + progress * 0.8
                inner = radius * 0.45
                outer = radius * (1.1 + 0.35 * (index % 2))
                pygame.draw.line(
                    blast, (255, 193, 70, alpha),
                    (
                        center + math.cos(angle) * inner,
                        center + math.sin(angle) * inner,
                    ),
                    (
                        center + math.cos(angle) * outer,
                        center + math.sin(angle) * outer,
                    ),
                    max(2, CELL // 12),
                )
            self.surf.blit(blast, (x - center, y - center))

    def _draw_teleport_prediction(self, projectile):
        r = float(projectile["r"])
        c = float(projectile["c"])
        target_r = float(projectile.get("target_r", r))
        target_c = float(projectile.get("target_c", c))
        remaining = math.hypot(target_r - r, target_c - c)
        if remaining <= 0.001:
            dr, dc = 0.0, 0.0
        else:
            dr = (target_r - r) / remaining
            dc = (target_c - c) / remaining
        step = 0.35
        distance = step
        while distance <= remaining:
            nr, nc = r + dr * distance, c + dc * distance
            if int(distance / step) % 2 == 0:
                x = int(nc * CELL + MAZE_OX + CELL // 2)
                y = int(nr * CELL + MAZE_OY + CELL // 2)
                pygame.draw.circle(self.surf, (100, 255, 235), (x, y), 3)
            distance += step
        lx = int(target_c * CELL + MAZE_OX + CELL // 2)
        ly = int(target_r * CELL + MAZE_OY + CELL // 2)
        pygame.draw.circle(
            self.surf, (100, 255, 235),
            (lx, ly), max(6, CELL // 3), 2,
        )

    def _draw_blind_overlay(self, me):
        if me is None or me.get("blind_remaining", 0.0) <= 0:
            return
        overlay = pygame.Surface((PANEL_X, H), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 248))
        x, y = cell_xy(me["r"], me["c"])
        pygame.draw.circle(
            overlay, (0, 0, 0, 0), (x, y), max(CELL * 2, 36)
        )
        self.surf.blit(overlay, (0, 0))

    @staticmethod
    def _player_facing_angle(p):
        return -math.degrees(math.atan2(
            p.get("facing_r", 0.0), p.get("facing_c", 1.0)
        ))

    def _selected_held_item(self, p):
        if p.get("role") == "hunter" and p.get("attack_anim", 0.0) > 0:
            return "cleaver"

        skills = p.get("skills", [])
        if skills:
            index = max(
                0, min(int(p.get("selected_skill", 0)), len(skills) - 1)
            )
            skill = skills[index]
            if skill in ("spear", "flash", "teleport", "trap"):
                return skill

        if p.get("role") == "hunter":
            return "cleaver"

        inp = self.mp_pending_input.get(p["id"], {})
        if p["id"] == self.player_id and self.server is None:
            inp = self.mp_local_input
        if inp.get("e_held") and any(
                not gen.completed and gen.is_adjacent(p["r"], p["c"])
                for gen in self.mp_generators):
            return "toolkit"
        return None

    def _draw_slash_animation(self, p, x, y):
        remaining = max(0.0, p.get("attack_anim", 0.0))
        progress = 1.0 - min(1.0, remaining / 0.25)
        progress = 1.0 - (1.0 - progress) ** 3
        base = math.atan2(
            p.get("facing_r", 0.0), p.get("facing_c", 1.0)
        )
        current = base + math.radians(-82 + 152 * progress)
        trail_start = max(base - math.radians(82), current - math.radians(78))

        extent = max(64, CELL * 4)
        overlay = pygame.Surface((extent, extent), pygame.SRCALPHA)
        center = extent // 2
        radius = CELL * 0.92
        points = []
        for i in range(13):
            angle = trail_start + (current - trail_start) * i / 12
            points.append((
                center + math.cos(angle) * radius,
                center + math.sin(angle) * radius,
            ))
        for i in range(1, len(points)):
            alpha = int(40 + 175 * i / len(points))
            width = max(2, int(CELL * (0.08 + 0.13 * i / len(points))))
            pygame.draw.line(
                overlay, (255, 225, 190, alpha),
                points[i - 1], points[i], width,
            )
        tip = points[-1]
        pygame.draw.circle(
            overlay, (255, 246, 220, 225),
            (int(tip[0]), int(tip[1])), max(2, CELL // 10),
        )
        self.surf.blit(overlay, (x - center, y - center))
        return -math.degrees(current)

    def _draw_held_item(self, p, x, y):
        item_name = self._selected_held_item(p)
        sprite = self.item_sprites.get(item_name)
        if sprite is None:
            return

        facing_r = p.get("facing_r", 0.0)
        facing_c = p.get("facing_c", 1.0)
        angle = self._player_facing_angle(p)
        if p.get("role") == "hunter" and p.get("attack_anim", 0.0) > 0:
            angle = self._draw_slash_animation(p, x, y)

        hand_x = x + int(facing_c * CELL * 0.42)
        hand_y = y + int(facing_r * CELL * 0.42)
        scale = 1.0 if item_name in ("spear", "cleaver") else 0.78
        shown = pygame.transform.rotozoom(sprite, angle, scale)
        self.surf.blit(shown, shown.get_rect(center=(hand_x, hand_y)))

    def _draw_multiplayer_player(self, p):
        if p.get("role") == "hunter":
            color = (255, 66, 66)
            role = "hunter"
        else:
            color = PLAYER_COLORS[p["id"] % len(PLAYER_COLORS)]
            role = "runner"

        x, y = cell_xy(p["r"], p["c"])
        radius = max(7, int(CELL * 0.34))
        if p.get("carried"):
            carrier = next(
                (
                    hunter for hunter in self.mp_players
                    if hunter.get("carrying_pid") == p["id"]
                ),
                None,
            )
            if carrier is not None:
                x -= int(carrier.get("facing_c", 1.0) * CELL * 0.28)
                y -= int(carrier.get("facing_r", 0.0) * CELL * 0.28)

        shadow = pygame.Surface((radius * 4, radius * 3), pygame.SRCALPHA)
        pygame.draw.ellipse(
            shadow, (0, 0, 0, 125),
            (radius, radius * 2, radius * 2, max(4, radius // 2)),
        )
        self.surf.blit(shadow, (x - radius * 2, y - radius * 2))

        pygame.draw.circle(self.surf, (6, 10, 15), (x, y), radius + 5)
        pygame.draw.circle(self.surf, color, (x, y), radius + 4, 2)

        sprite = self.actor_sprites.get(role)
        if sprite is None:
            pygame.draw.circle(self.surf, color, (x, y), radius)
        else:
            angle = self._player_facing_angle(p)
            scale = 1.0
            if p.get("downed"):
                angle -= 90
                scale = 0.88
            elif p.get("imprisoned") or p.get("carried"):
                scale = 0.58
            shown = pygame.transform.rotozoom(sprite, angle, scale)
            if p.get("invisible_remaining", 0.0) > 0:
                shown.set_alpha(105 if p["id"] == self.player_id else 45)
            self.surf.blit(shown, shown.get_rect(center=(x, y)))

        if not p.get("downed") and not p.get("imprisoned") \
                and not p.get("carried"):
            self._draw_held_item(p, x, y)

        if p["id"] == self.player_id:
            marker_r = radius + 8
            for start in (20, 110, 200, 290):
                pygame.draw.arc(
                    self.surf, WHITE,
                    (x - marker_r, y - marker_r, marker_r * 2, marker_r * 2),
                    math.radians(start), math.radians(start + 38), 2,
                )
        if p.get("imprisoned"):
            frac = max(
                0.0, p.get("imprison_remaining", 0)
            ) / FREEZING_POD_IMPRISON
            arc_rect = pygame.Rect(
                x - radius - 7, y - radius - 7,
                (radius + 7) * 2, (radius + 7) * 2,
            )
            pygame.draw.arc(
                self.surf, (255, 82, 82), arc_rect,
                -math.pi / 2, -math.pi / 2 + math.tau * frac, 4,
            )
        if p.get("stun_remaining", 0.0) > 0:
            for index in range(3):
                angle = pygame.time.get_ticks() / 180.0 + index * math.tau / 3
                sx = x + int(math.cos(angle) * (radius + 7))
                sy = y + int(math.sin(angle) * (radius + 7))
                pygame.draw.circle(self.surf, (255, 224, 74), (sx, sy), 3)
        if p.get("trapped"):
            pygame.draw.arc(
                self.surf, (255, 128, 38),
                (x - radius - 7, y - radius - 7,
                 (radius + 7) * 2, (radius + 7) * 2),
                0, math.tau, 3,
            )
        name_color = (255, 76, 76) if role == "hunter" else WHITE
        name_text = self.font_tiny.render(
            p.get("name", "Hunter" if role == "hunter" else "Runner"),
            True, name_color,
        )
        self.surf.blit(
            name_text,
            (x - name_text.get_width() // 2, y - radius - 25),
        )

    def draw_mp_play(self):
        self.draw_maze()

        for g in self.gates:
            g.draw(self.surf, self.walls)

        # DBD entities
        for gen in self.mp_generators:
            gen.draw(self.surf)
        for w in self.mp_freezing_pods:
            w.draw(self.surf)
        if self.mp_mode == "dbd":
            self._draw_dbd_objects()

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

        me = next(
            (p for p in self.mp_players if p["id"] == self.player_id),
            None,
        )

        # Draw carried runners last so they remain visible above their carrier.
        players_to_draw = sorted(
            self.mp_players, key=lambda player: bool(player.get("carried"))
        )
        for p in players_to_draw:
            if not p["alive"]:
                continue
            if p["id"] != self.player_id \
                    and p.get("invisible_remaining", 0.0) > 0:
                continue
            self._draw_multiplayer_player(p)

        # bot hunter (escape mode only)
        if self.mp_mode == "escape" and self.hunter is not None:
            self.hunter.draw(self.surf)

        self._draw_blind_overlay(me)
        self.draw_mp_panel()
        self.draw_skill_inventory(me)
        self.draw_skill_check()

    def draw_skill_inventory(self, me):
        if self.mp_mode != "dbd" or me is None:
            return
        skills = me.get("skills", [])
        slot_size = 66
        gap = 10
        start_x = PANEL_X + PANEL_W - 20 - slot_size * 2 - gap
        y = H - slot_size - 24
        selected = min(
            self.mp_selected_skill, max(0, len(skills) - 1)
        )

        section = pygame.Rect(
            PANEL_X + 14, y - 36, PANEL_W - 28, slot_size + 50
        )
        pygame.draw.rect(
            self.surf, (8, 13, 21), section, border_radius=6
        )
        pygame.draw.line(
            self.surf, (25, 68, 85),
            (section.x + 10, section.y),
            (section.right - 10, section.y), 2,
        )

        selected_name = (
            SKILL_NAMES[skills[selected]]
            if skills and selected < len(skills)
            else "NO SKILL"
        )
        title = self.font_tiny.render(selected_name, True, (198, 214, 222))
        self.surf.blit(title, (section.x + 12, section.y + 8))

        for index in range(2):
            x = start_x + index * (slot_size + gap)
            rect = pygame.Rect(x, y, slot_size, slot_size)
            skill = skills[index] if index < len(skills) else None
            color = SKILL_COLORS.get(skill, (45, 45, 55))
            shadow = rect.move(0, 4)
            pygame.draw.rect(
                self.surf, (2, 4, 7), shadow, border_radius=6
            )
            pygame.draw.rect(
                self.surf, (13, 19, 28), rect, border_radius=6
            )
            pygame.draw.rect(
                self.surf, color, rect,
                4 if index == selected and skill else 2,
                border_radius=6,
            )
            if skill:
                icon = self.skill_icons.get(skill)
                if icon is not None:
                    self.surf.blit(icon, icon.get_rect(center=rect.center))
                else:
                    fallback = self.font_sm.render(
                        SKILL_NAMES[skill][:3], True, color
                    )
                    self.surf.blit(
                        fallback, fallback.get_rect(center=rect.center)
                    )
                charges = me.get("skill_charges", {}).get(skill, 0)
                cooldown_value = me.get("skill_cooldowns", {}).get(skill, 0.0)
                if charges <= 0 and cooldown_value > 0:
                    veil = pygame.Surface((slot_size, slot_size), pygame.SRCALPHA)
                    veil.fill((0, 0, 0, 155))
                    self.surf.blit(veil, rect)
                    cooldown = self.font_sm.render(
                        f"{cooldown_value:.1f}", True, WHITE
                    )
                    self.surf.blit(cooldown, cooldown.get_rect(center=rect.center))
                charge_text = self.font_tiny.render(
                    f"x{charges}", True, WHITE
                )
                self.surf.blit(
                    charge_text,
                    (rect.right - charge_text.get_width() - 5, rect.bottom - 20),
                )
            if index == selected and skill:
                key = self.font_tiny.render("F", True, (8, 12, 18))
                badge = pygame.Rect(rect.x + 5, rect.y + 5, 19, 19)
                pygame.draw.rect(
                    self.surf, color, badge, border_radius=4
                )
                self.surf.blit(key, key.get_rect(center=badge.center))

        effects = []
        for key, label, color in (
            ("speed_remaining", "SPRINT", SKILL_COLORS["speed"]),
            ("invisible_remaining", "INVIS", SKILL_COLORS["invisible"]),
            ("phase_remaining", "PHASE", SKILL_COLORS["phase"]),
            ("blind_remaining", "BLIND", (255, 200, 70)),
        ):
            if me.get(key, 0.0) > 0:
                effects.append((f"{label} {me[key]:.1f}", color))
        effect_y = section.y - 28
        effect_x = PANEL_X + 18
        for text_value, color in effects[:3]:
            text = self.font_tiny.render(text_value, True, color)
            chip = pygame.Rect(
                effect_x, effect_y, text.get_width() + 16, 22
            )
            pygame.draw.rect(
                self.surf, (11, 17, 25), chip, border_radius=5
            )
            pygame.draw.rect(
                self.surf, color, chip, 1, border_radius=5
            )
            self.surf.blit(text, (chip.x + 8, chip.y + 2))
            effect_x = chip.right + 6

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

        if me.get("trapped"):
            label_text = (
                f"TRAP ESCAPE  {me.get('trap_checks_remaining', 0)} LEFT"
                "  -  PRESS SPACE"
            )
            elapsed = me.get("trap_check_elapsed", 0.0)
            duration = TRAP_CHECK_DURATION
            target_start = me.get("trap_check_target", 0.6)
            target_width = me.get("trap_check_width", TRAP_CHECK_WIDTH)
            border_color = (255, 130, 40)
        else:
            gen = next(
                (
                    g for g in self.mp_generators
                    if g.skill_active and g.skill_owner_id == self.player_id
                ),
                None,
            )
            if gen is None:
                return
            label_text = "SKILL CHECK  -  PRESS SPACE"
            elapsed = gen.skill_elapsed
            duration = gen.skill_duration
            target_start = gen.skill_target_start
            target_width = gen.skill_target_width
            border_color = (255, 220, 0)

        panel_w = min(640, PANEL_X - 80)
        panel_h = 120
        panel_x = PANEL_X // 2 - panel_w // 2
        panel_y = H - panel_h - 42
        panel = pygame.Surface((panel_w, panel_h), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 225))
        self.surf.blit(panel, (panel_x, panel_y))
        pygame.draw.rect(
            self.surf, border_color,
            (panel_x, panel_y, panel_w, panel_h), 2,
        )

        label = self.font_med.render(
            label_text, True, WHITE
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

        target_x = bar_x + int(bar_w * target_start)
        target_w = max(4, int(bar_w * target_width))
        pygame.draw.rect(
            self.surf, (80, 220, 100),
            (target_x, bar_y, target_w, bar_h),
        )

        needle = min(
            1.0, elapsed / max(0.01, duration)
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
        self.surf.blit(self.panel_surface, (PANEL_X, 0))
        pygame.draw.line(
            self.surf, (26, 145, 174), (PANEL_X, 0), (PANEL_X, H), 3
        )
        pygame.draw.line(
            self.surf, (78, 38, 42),
            (PANEL_X + 3, 0), (PANEL_X + 3, H), 1,
        )

        mode_label = {
            "escape": "ESCAPE",
            "dbd":    "DBD-MAZE",
        }.get(self.mp_mode, self.mp_mode.upper())
        role = "HOST" if self.server is not None else "CLIENT"
        brand = self.font_tiny.render("VOID // MAZE", True, (111, 137, 150))
        self.surf.blit(brand, (PANEL_X + 22, 18))
        title = self.font_med.render(mode_label, True, (93, 234, 220))
        self.surf.blit(title, (PANEL_X + 20, 43))
        role_text = self.font_tiny.render(role, True, (109, 132, 145))
        self.surf.blit(
            role_text, (PANEL_X + PANEL_W - role_text.get_width() - 20, 54)
        )
        pygame.draw.line(
            self.surf, (27, 62, 77),
            (PANEL_X + 18, 84), (PANEL_X + PANEL_W - 18, 84), 1,
        )

        if self.mp_mode == "escape":
            sub = self.font_med.render(
                f"LEVEL {self.level}", True, (235, 238, 239)
            )
            self.surf.blit(sub, (PANEL_X + 20, 102))
        else:
            mins = max(0, int(self.mp_match_timer)) // 60
            secs = max(0, int(self.mp_match_timer)) % 60
            time_txt = self.font_lg.render(
                f"{mins:01d}:{secs:02d}", True, (244, 204, 72)
            )
            self.surf.blit(time_txt, (PANEL_X + 20, 96))
            done = sum(1 for g in self.mp_generators if g.completed)
            gen_label = self.font_tiny.render(
                f"GENERATORS  {done}/{len(self.mp_generators)}",
                True, (195, 210, 218),
            )
            self.surf.blit(gen_label, (PANEL_X + 144, 103))
            segment_w = 31
            for index in range(max(1, len(self.mp_generators))):
                segment = pygame.Rect(
                    PANEL_X + 144 + index * (segment_w + 5),
                    126, segment_w, 10,
                )
                fill = (75, 220, 124) if index < done else (32, 48, 59)
                pygame.draw.rect(
                    self.surf, fill, segment, border_radius=3
                )
            status = "EXIT UNLOCKED" if self.exit_unlocked else "EXIT LOCKED"
            scol = (80, 230, 126) if self.exit_unlocked else (238, 78, 82)
            stxt = self.font_tiny.render(status, True, scol)
            self.surf.blit(stxt, (PANEL_X + 144, 144))

        # player list
        y0 = 190
        head = self.font_tiny.render("MATCH STATUS", True, (121, 148, 161))
        self.surf.blit(head, (PANEL_X + 20, y0))
        pygame.draw.line(
            self.surf, (28, 55, 67),
            (PANEL_X + 20, y0 + 25),
            (PANEL_X + PANEL_W - 20, y0 + 25), 1,
        )

        for i, p in enumerate(self.mp_players):
            if p.get("role") == "hunter":
                color    = (255, 60, 60)
                role_tag = "HUNTER"
            else:
                color    = PLAYER_COLORS[p["id"] % len(PLAYER_COLORS)]
                role_tag = "RUNNER"
            label = p.get(
                "name", "Hunter" if p.get("role") == "hunter" else "Runner"
            ) + ("  YOU" if p["id"] == self.player_id else "")
            if p.get("escaped"):
                status = "ESCAPED"
            elif not p["alive"]:
                status = "ELIMINATED"
            elif p.get("imprisoned"):
                status = f"FROZEN  {p['imprison_remaining']:.0f}s"
            elif p.get("carried"):
                status = "CARRIED"
            elif p.get("downed"):
                status = f"DOWNED  {p['downed_remaining']:.1f}s"
            elif p.get("trapped"):
                status = f"TRAPPED  {p['trap_checks_remaining']}"
            elif p.get("stun_remaining", 0.0) > 0:
                status = f"STUNNED  {p['stun_remaining']:.1f}s"
            else:
                status = "ALIVE"
            row = pygame.Rect(
                PANEL_X + 16, y0 + 36 + i * 48, PANEL_W - 32, 40
            )
            pygame.draw.rect(
                self.surf, (11, 18, 27), row, border_radius=5
            )
            pygame.draw.rect(
                self.surf, color,
                (row.x, row.y, 4, row.height), border_radius=2,
            )
            pygame.draw.circle(
                self.surf, color, (row.x + 19, row.centery), 6
            )
            name_color = (
                (255, 76, 76)
                if p.get("role") == "hunter" else WHITE
            )
            name_text = self.font_tiny.render(label, True, name_color)
            role_text = self.font_tiny.render(role_tag, True, color)
            status_color = (
                (104, 217, 139) if status == "ALIVE" else (229, 185, 82)
            )
            status_text = self.font_tiny.render(status, True, status_color)
            self.surf.blit(name_text, (row.x + 32, row.y + 4))
            self.surf.blit(role_text, (row.x + 32, row.y + 21))
            self.surf.blit(
                status_text,
                (row.right - status_text.get_width() - 10, row.y + 12),
            )

    def draw_mp_end(self):
        # render the maze + entities frozen behind the overlay
        self.draw_mp_play()

        if self.mp_winner == "runners":
            title  = "RUNNERS WIN"
            color  = (80, 255, 80)
            reason = "At least one runner escaped the facility."
        elif self.mp_winner == "hunter":
            title  = "HUNTER WINS"
            color  = (255, 80, 80)
            reason = "All runners were eliminated, frozen, or time expired."
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
        if self.mp_mode == "dbd":
            self.host_start_dbd_match()
        else:
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
                        elif self.state in ("profile", "daily", "shop"):
                            if self.state == "profile":
                                self.profile_name_input = self.profile.get(
                                    "name", ""
                                )
                                self.profile_editing = False
                            self.state = "menu"
                        else:
                            self.state = "menu"

                    elif self.state == "mp_play" and event.key == pygame.K_SPACE:
                        self.mp_skill_seq += 1
                        self.mp_attack_seq += 1

                    elif self.state == "mp_play" and event.key == pygame.K_e:
                        self.mp_interact_seq += 1

                    elif self.state == "mp_play" and event.key == pygame.K_f:
                        self.mp_use_seq += 1
                        self.profile["stats"]["skills_used"] += 1
                        self._record_daily("skills")

                    elif self.state == "menu":
                        if event.key in (pygame.K_UP, pygame.K_w):
                            self.menu_index = (self.menu_index - 1) % len(self.menu_options)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            self.menu_index = (self.menu_index + 1) % len(self.menu_options)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self.select_menu_option()

                    elif self.state == "profile":
                        if event.key == pygame.K_RETURN:
                            self.profile["name"] = progression.sanitize_name(
                                self.profile_name_input
                            )
                            self.profile_name_input = self.profile["name"]
                            self.profile_editing = False
                            self._save_profile()
                        elif event.key == pygame.K_BACKSPACE:
                            self.profile_name_input = \
                                self.profile_name_input[:-1]
                        elif event.unicode and not (
                                pygame.key.get_mods() & pygame.KMOD_CTRL):
                            self.profile_name_input = \
                                progression.sanitize_name(
                                    self.profile_name_input + event.unicode
                                )

                    elif self.state == "shop":
                        if event.key in (pygame.K_LEFT, pygame.K_a):
                            self.shop_tab = (
                                self.shop_tab - 1
                            ) % len(self.shop_tabs)
                            self.shop_index = 0
                            self.shop_notice = ""
                        elif event.key in (pygame.K_RIGHT, pygame.K_d):
                            self.shop_tab = (
                                self.shop_tab + 1
                            ) % len(self.shop_tabs)
                            self.shop_index = 0
                            self.shop_notice = ""
                        elif event.key in (pygame.K_UP, pygame.K_w):
                            skills = self._shop_skills()
                            if skills:
                                self.shop_index = (
                                    self.shop_index - 1
                                ) % len(skills)
                        elif event.key in (pygame.K_DOWN, pygame.K_s):
                            skills = self._shop_skills()
                            if skills:
                                self.shop_index = (
                                    self.shop_index + 1
                                ) % len(skills)
                        elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                            self._shop_action()

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
                    if self.state == "mp_play":
                        self.mp_attack_seq += 1
                    elif self.state == "menu":
                        self.handle_menu_click(event.pos)
                    elif self.state == "shop":
                        tab_clicked = False
                        for index, rect in enumerate(self.shop_tab_rects):
                            if rect.collidepoint(event.pos):
                                self.shop_tab = index
                                self.shop_index = 0
                                self.shop_notice = ""
                                tab_clicked = True
                                break
                        if tab_clicked:
                            continue
                        for index, rect in enumerate(self.shop_rects):
                            if rect.collidepoint(event.pos):
                                self.shop_index = index
                                self._shop_action()
                                break
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

                if event.type == pygame.MOUSEWHEEL \
                        and self.state == "mp_play":
                    me = next(
                        (
                            p for p in self.mp_players
                            if p["id"] == self.player_id
                        ),
                        None,
                    )
                    skill_count = len(me.get("skills", [])) if me else 0
                    if skill_count:
                        direction = -1 if event.y > 0 else 1
                        self.mp_selected_skill = (
                            self.mp_selected_skill + direction
                        ) % skill_count

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
                            self._reset_local_action_sequences()
                            self._apply_state(msg)
            elif self.state == "lobby_wait_host" and self.server is not None:
                # accept hellos, send welcomes
                for ci, msg in self.server.drain_all():
                    if msg.get("type") == "hello":
                        self.mp_network_profiles[ci + 1] = msg.get(
                            "profile", {}
                        )
                        self.server.send_to(ci, {"type": "welcome", "id": ci + 1})
                self._prune_lobby_connections()

            elif self.state == "lobby_wait_client" and self.client is not None:
                self.mp_client_tick(dt)
                for msg in self.client.drain():
                    if msg.get("type") == "start_vote":
                        self.state = "lobby_map_vote"
                        self.mp_vote_timer = 15.0
                        self.mp_map_votes = {}
                    elif msg.get("type") == "start":
                        self.state = "mp_play"
                        self._reset_local_action_sequences()
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
                me = next(
                    (
                        p for p in self.mp_players
                        if p["id"] == self.player_id
                    ),
                    None,
                )
                skill_count = len(me.get("skills", [])) if me else 0
                if skill_count:
                    self.mp_selected_skill %= skill_count
                else:
                    self.mp_selected_skill = 0
                mouse_x, mouse_y = pygame.mouse.get_pos()
                aim_c = (mouse_x - MAZE_OX - CELL / 2) / CELL
                aim_r = (mouse_y - MAZE_OY - CELL / 2) / CELL
                self.mp_local_input = {
                    "dr": dr, "dc": dc, "e_held": pressed[pygame.K_e],
                    "skill_seq": self.mp_skill_seq,
                    "attack_seq": self.mp_attack_seq,
                    "interact_seq": self.mp_interact_seq,
                    "use_seq": self.mp_use_seq,
                    "selected_skill": self.mp_selected_skill,
                    "aim_r": aim_r,
                    "aim_c": aim_c,
                }

                if self.server is not None:
                    self.mp_host_tick(dt)
                elif self.client is not None:
                    self.mp_client_tick(dt)

            # ── render ────────────────────────────────────────────────────────
            if self.state == "menu":
                self.draw_menu()

            elif self.state == "profile":
                self.draw_profile()

            elif self.state == "daily":
                self.draw_daily()

            elif self.state == "shop":
                self.draw_shop()

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
